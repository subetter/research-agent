import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def canonical_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if not parsed.scheme or not parsed.hostname:
        return (url or "").strip()
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    else:
        netloc = host
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", parsed.query, ""))


def content_hash(text: str) -> str:
    return hashlib.sha256(" ".join((text or "").split()).encode()).hexdigest()


def normalize_dimensions(value) -> list[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            value = [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item.strip() for item in value if isinstance(item, str) and item.strip()))


def subject_from_task_key(task_key: str) -> str | None:
    if not task_key or not task_key.startswith("research:"):
        return None
    body = task_key[len("research:"):]
    return body.rsplit(":", 1)[0] if ":" in body else body


def infer_dimensions_from_title(title: str) -> list[str]:
    parts = [part.strip() for part in (title or "").split("·")]
    if len(parts) >= 3 and "模拟" in parts[-1]:
        return [parts[1]] if parts[1] else []
    return []


class Store:
    """Local multi-account persistence. Mutations serialize in short DB transactions."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.conn:
            self.conn.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS projects (
              id TEXT PRIMARY KEY, name TEXT, background TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS runs (
              id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), question TEXT,
              status TEXT, revision INTEGER DEFAULT 0, mode TEXT, plan TEXT,
              parent_run_id TEXT, artifact TEXT, error TEXT, created_at TEXT, updated_at TEXT);
            CREATE TABLE IF NOT EXISTS events (
              run_id TEXT REFERENCES runs(id), seq INTEGER, type TEXT, payload TEXT,
              created_at TEXT, PRIMARY KEY(run_id, seq));
            CREATE TABLE IF NOT EXISTS evidence (
              id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), run_id TEXT,
              task_key TEXT, title TEXT, url TEXT, quote TEXT, locator TEXT,
              source_type TEXT, fetched_at TEXT,
              dimensions TEXT DEFAULT '[]',
              canonical_url TEXT,
              content_hash TEXT,
              UNIQUE(run_id, task_key, url, locator));
            CREATE TABLE IF NOT EXISTS operations (
              run_id TEXT, key TEXT, result TEXT, PRIMARY KEY(run_id, key));
            CREATE TABLE IF NOT EXISTS usage (
              run_id TEXT, key TEXT, kind TEXT, status TEXT, tokens INTEGER DEFAULT 0,
              PRIMARY KEY(run_id, key));
            CREATE TABLE IF NOT EXISTS documents (
              id TEXT PRIMARY KEY, project_id TEXT REFERENCES projects(id), name TEXT,
              content TEXT, created_at TEXT);
            """)
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
              id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL, display_name TEXT NOT NULL,
              password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','user')), created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions (
              token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id), expires_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS login_attempts (
              key TEXT PRIMARY KEY, failures INTEGER NOT NULL, blocked_until REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS conversations (
              id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), title TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chat_messages (
              id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id),
              role TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL,
              model TEXT, tokens INTEGER DEFAULT 0, client_id TEXT, created_at TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS chat_request_unique ON chat_messages(conversation_id,client_id) WHERE role='user';
            CREATE UNIQUE INDEX IF NOT EXISTS chat_one_pending ON chat_messages(conversation_id) WHERE role='assistant' AND status='pending';
            CREATE INDEX IF NOT EXISTS chat_history_idx ON chat_messages(conversation_id,created_at);
            CREATE TABLE IF NOT EXISTS admin_audit (
              id TEXT PRIMARY KEY, actor_id TEXT NOT NULL REFERENCES users(id), action TEXT NOT NULL,
              target_id TEXT, created_at TEXT NOT NULL);
            """)
            columns = {row[1] for row in self.conn.execute("PRAGMA table_info(projects)")}
            if "owner_id" not in columns:
                self.conn.execute("ALTER TABLE projects ADD COLUMN owner_id TEXT REFERENCES users(id)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS projects_owner_idx ON projects(owner_id)")
            self._migrate_evidence()
            self._migrate_documents()
            self._migrate_artifacts()

    def close(self):
        self.conn.close()

    @staticmethod
    def decode(row):
        if row is None:
            return None
        out = dict(row)
        for key in ("plan", "artifact", "payload", "result"):
            if key in out and out[key] is not None:
                out[key] = json.loads(out[key])
        if "dimensions" in out:
            out["dimensions"] = normalize_dimensions(out["dimensions"])
        return out

    def query(self, sql, args=()):
        with self.lock:
            return [self.decode(r) for r in self.conn.execute(sql, args).fetchall()]

    def project(self, project_id):
        rows = self.query("SELECT * FROM projects WHERE id=?", (project_id,))
        return rows[0] if rows else None

    def run(self, run_id):
        rows = self.query("SELECT * FROM runs WHERE id=?", (run_id,))
        if not rows:
            return None
        result = rows[0]
        result["usage"] = self.query("SELECT kind,status,COUNT(*) AS calls,SUM(tokens) AS tokens FROM usage WHERE run_id=? GROUP BY kind,status", (run_id,))
        return result

    def create_project(self, name, background, owner_id=None):
        project_id = uid("project")
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO projects(id,name,background,created_at,owner_id) VALUES(?,?,?,?,?)", (project_id, name, background, now(), owner_id))
        return self.project(project_id)

    def create_run(self, project_id, question, mode, plan, parent_run_id=None):
        run_id = uid("run")
        stamp = now()
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO runs(id,project_id,question,status,mode,plan,parent_run_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                              (run_id, project_id, question, "planning", mode, json.dumps(plan, ensure_ascii=False), parent_run_id, stamp, stamp))
            self._event(run_id, "run.created", {"mode": mode})
        return self.run(run_id)

    def _event(self, run_id, type, payload):
        seq = self.conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM events WHERE run_id=?", (run_id,)).fetchone()[0]
        self.conn.execute("INSERT INTO events VALUES(?,?,?,?,?)", (run_id, seq, type, json.dumps(payload, ensure_ascii=False), now()))

    def event(self, run_id, type, payload):
        with self.lock, self.conn:
            self._event(run_id, type, payload)

    def update(self, run_id, expected_revision=None, **fields):
        with self.lock, self.conn:
            current = self.conn.execute("SELECT revision FROM runs WHERE id=?", (run_id,)).fetchone()
            if current is None:
                raise KeyError(run_id)
            if expected_revision is not None and current[0] != expected_revision:
                raise ValueError("revision_conflict")
            fields["revision"] = current[0] + 1
            fields["updated_at"] = now()
            for key in ("plan", "artifact"):
                if key in fields:
                    fields[key] = json.dumps(fields[key], ensure_ascii=False)
            allowed = {"status", "revision", "updated_at", "plan", "artifact", "error"}
            if not fields.keys() <= allowed:
                raise ValueError("invalid_field")
            sql = ",".join(f"{k}=?" for k in fields)
            self.conn.execute(f"UPDATE runs SET {sql} WHERE id=?", (*fields.values(), run_id))
            if "status" in fields:
                self._event(run_id, f"run.{fields['status']}", {})
        return self.run(run_id)

    def operation(self, run_id, key):
        rows = self.query("SELECT result FROM operations WHERE run_id=? AND key=?", (run_id, key))
        return rows[0]["result"] if rows else None

    def save_operation(self, run_id, key, result):
        with self.lock, self.conn:
            self.conn.execute("INSERT OR IGNORE INTO operations VALUES(?,?,?)", (run_id, key, json.dumps(result, ensure_ascii=False)))

    def usage_count(self, run_id, kind):
        return self.query("SELECT COUNT(*) AS calls FROM usage WHERE run_id=? AND kind=?", (run_id, kind))[0]["calls"]

    def reserve(self, run_id, key, kind, limit):
        with self.lock, self.conn:
            existing = self.conn.execute("SELECT status FROM usage WHERE run_id=? AND key=?", (run_id, key)).fetchone()
            if existing:
                # Unknown/in-flight calls still consume budget; retries are a new operation.
                return False
            total = self.conn.execute("SELECT COUNT(*) FROM usage WHERE run_id=? AND kind=?", (run_id, kind)).fetchone()[0]
            if total >= limit:
                return False
            self.conn.execute("INSERT INTO usage(run_id,key,kind,status) VALUES(?,?,?,?)", (run_id, key, kind, "reserved"))
            return True

    def settle(self, run_id, key, tokens=0, status="succeeded"):
        with self.lock, self.conn:
            self.conn.execute("UPDATE usage SET status=?,tokens=? WHERE run_id=? AND key=?", (status, tokens, run_id, key))

    def _migrate_evidence(self):
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(evidence)")}
        if "dimensions" not in columns:
            self.conn.execute("ALTER TABLE evidence ADD COLUMN dimensions TEXT DEFAULT '[]'")
        if "canonical_url" not in columns:
            self.conn.execute("ALTER TABLE evidence ADD COLUMN canonical_url TEXT")
        if "content_hash" not in columns:
            self.conn.execute("ALTER TABLE evidence ADD COLUMN content_hash TEXT")
        if "citation_role" not in columns:
            self.conn.execute("ALTER TABLE evidence ADD COLUMN citation_role TEXT DEFAULT 'fact'")
        rows = self.conn.execute("SELECT id, title, url, quote, dimensions, canonical_url, content_hash FROM evidence").fetchall()
        for row in rows:
            updates = {}
            dims = normalize_dimensions(row["dimensions"])
            if not dims:
                inferred = infer_dimensions_from_title(row["title"])
                if inferred:
                    updates["dimensions"] = json.dumps(inferred, ensure_ascii=False)
            if not row["canonical_url"] and row["url"]:
                updates["canonical_url"] = canonical_url(row["url"])
            if not row["content_hash"] and row["quote"]:
                updates["content_hash"] = content_hash(row["quote"])
            if updates:
                sql = ",".join(f"{key}=?" for key in updates)
                self.conn.execute(f"UPDATE evidence SET {sql} WHERE id=?", (*updates.values(), row["id"]))

    def _migrate_documents(self):
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(documents)")}
        if "citable" not in columns:
            self.conn.execute("ALTER TABLE documents ADD COLUMN citable INTEGER DEFAULT 0")

    def add_evidence(self, project_id, run_id, task_key, item):
        quote = item["quote"]
        url = item["url"]
        locator = item["locator"]
        dims = normalize_dimensions(item.get("dimensions"))
        if not dims:
            dims = infer_dimensions_from_title(item.get("title", ""))
        canon = item.get("canonical_url") or canonical_url(url)
        digest = item.get("content_hash") or content_hash(quote)
        dims_json = json.dumps(dims, ensure_ascii=False)
        role = item.get("citation_role") or ("analysis" if item.get("source_type") == "document" and not item.get("citable") else "fact")
        with self.lock, self.conn:
            existing = self.conn.execute(
                """SELECT id, dimensions FROM evidence WHERE run_id=? AND (
                     (content_hash IS NOT NULL AND content_hash=?)
                     OR (canonical_url IS NOT NULL AND canonical_url=? AND quote=?)
                     OR (task_key=? AND url=? AND locator=?)
                   )""",
                (run_id, digest, canon, quote, task_key, url, locator),
            ).fetchone()
            if existing:
                merged = list(dict.fromkeys(normalize_dimensions(existing["dimensions"]) + dims))
                if merged != normalize_dimensions(existing["dimensions"]):
                    self.conn.execute("UPDATE evidence SET dimensions=? WHERE id=?", (json.dumps(merged, ensure_ascii=False), existing["id"]))
                return existing["id"]
            evidence_id = uid("ev")
            self.conn.execute(
                """INSERT INTO evidence(id,project_id,run_id,task_key,title,url,quote,locator,source_type,fetched_at,dimensions,canonical_url,content_hash,citation_role)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (evidence_id, project_id, run_id, task_key, item["title"], url, quote, locator, item["source_type"], item.get("fetched_at", now()), dims_json, canon, digest, role),
            )
            self._event(run_id, "evidence.created", {
                "evidence_id": evidence_id,
                "title": item["title"],
                "dimensions": dims,
                "subject": subject_from_task_key(task_key),
                "url": url,
                "source_type": item["source_type"],
                "citation_role": role,
            })
            return evidence_id

    def evidence(self, run_id):
        return self.query("SELECT * FROM evidence WHERE run_id=? ORDER BY rowid", (run_id,))

    def add_document(self, project_id, name, content, citable=False):
        document_id = uid("doc")
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO documents(id,project_id,name,content,created_at,citable) VALUES(?,?,?,?,?,?)",
                (document_id, project_id, name, content, now(), 1 if citable else 0),
            )
        return {"id": document_id, "name": name, "characters": len(content), "citable": bool(citable)}

    def document(self, project_id, document_id):
        rows = self.query("SELECT * FROM documents WHERE project_id=? AND id=?", (project_id, document_id))
        return rows[0] if rows else None

    def _migrate_artifacts(self):
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifact_versions (
              id TEXT PRIMARY KEY,
              run_id TEXT REFERENCES runs(id),
              project_id TEXT,
              version INTEGER NOT NULL,
              parent_version_id TEXT,
              origin TEXT NOT NULL CHECK(origin IN ('system','user')),
              content TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(run_id, version)
            )
            """
        )

    def list_artifact_versions(self, run_id):
        return self.query(
            "SELECT id,run_id,project_id,version,parent_version_id,origin,created_at FROM artifact_versions WHERE run_id=? ORDER BY version",
            (run_id,),
        )

    def get_artifact_version(self, run_id, version):
        rows = self.query("SELECT * FROM artifact_versions WHERE run_id=? AND version=?", (run_id, version))
        if not rows:
            return None
        row = rows[0]
        if isinstance(row.get("content"), str):
            row["content"] = json.loads(row["content"])
        return row

    def add_artifact_version(self, run_id, project_id, content, origin="system", parent_version_id=None):
        if origin not in {"system", "user"}:
            raise ValueError("invalid_origin")
        with self.lock, self.conn:
            latest = self.conn.execute(
                "SELECT id,version FROM artifact_versions WHERE run_id=? ORDER BY version DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            version = (latest["version"] if latest else 0) + 1
            parent = parent_version_id or (latest["id"] if latest else None)
            artifact_id = uid("art")
            self.conn.execute(
                "INSERT INTO artifact_versions(id,run_id,project_id,version,parent_version_id,origin,content,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (artifact_id, run_id, project_id, version, parent, origin, json.dumps(content, ensure_ascii=False), now()),
            )
            self._event(run_id, "artifact.created" if origin == "system" else "artifact.edited", {
                "version": version,
                "origin": origin,
                "parent_version_id": parent,
            })
        return self.get_artifact_version(run_id, version)

    def set_document_citable(self, project_id, document_id, citable):
        with self.lock, self.conn:
            current = self.conn.execute("SELECT id FROM documents WHERE project_id=? AND id=?", (project_id, document_id)).fetchone()
            if current is None:
                return None
            self.conn.execute("UPDATE documents SET citable=? WHERE id=?", (1 if citable else 0, document_id))
        return self.document(project_id, document_id)
