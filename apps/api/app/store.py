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
            evidence_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(evidence)")}
            if "published_at" not in evidence_columns:
                self.conn.execute("ALTER TABLE evidence ADD COLUMN published_at TEXT")
            if "dimension" not in evidence_columns:
                self.conn.execute("ALTER TABLE evidence ADD COLUMN dimension TEXT")
            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS claim_evidence (
              run_id TEXT NOT NULL,
              claim_id TEXT NOT NULL,
              evidence_id TEXT NOT NULL,
              relation TEXT NOT NULL CHECK(relation IN ('support','conflict')),
              PRIMARY KEY (run_id, claim_id, evidence_id, relation)
            )""")
            self._migrate_evidence()
            self._migrate_documents()
            self._migrate_artifacts()
            self._migrate_execution_records()

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
        if "citable" in out and out["citable"] is not None:
            out["citable"] = bool(out["citable"])
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
                # Failed/unknown calls may retry the same operation without opening a new ledger slot.
                if existing[0] == "unknown":
                    self.conn.execute("UPDATE usage SET status='reserved', tokens=0 WHERE run_id=? AND key=?", (run_id, key))
                    return True
                return False
            total = self.conn.execute("SELECT COUNT(*) FROM usage WHERE run_id=? AND kind=?", (run_id, kind)).fetchone()[0]
            if total >= limit:
                return False
            self.conn.execute("INSERT INTO usage(run_id,key,kind,status) VALUES(?,?,?,?)", (run_id, key, kind, "reserved"))
            return True

    def settle(self, run_id, key, tokens=0, status="succeeded"):
        with self.lock, self.conn:
            self.conn.execute("UPDATE usage SET status=?,tokens=? WHERE run_id=? AND key=?", (status, tokens, run_id, key))

    def add_evidence(self, project_id, run_id, task_key, item):
        from .freshness import parse_published_at
        published_at = parse_published_at(item.get("published_at"))
        quote = item["quote"]
        url = item["url"]
        locator = item["locator"]
        dims = normalize_dimensions(item.get("dimensions"))
        if item.get("dimension") and item["dimension"] not in dims:
            dims = list(dict.fromkeys([*(dims or []), item["dimension"]]))
        if not dims:
            dims = infer_dimensions_from_title(item.get("title", ""))
        dimension = item.get("dimension") or (dims[0] if dims else None)
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
                return existing[0]
            evidence_id = uid("ev")
            self.conn.execute(
                """INSERT INTO evidence(id,project_id,run_id,task_key,title,url,quote,locator,source_type,fetched_at,published_at,dimension,dimensions,canonical_url,content_hash,citation_role)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (evidence_id, project_id, run_id, task_key, item["title"], url, quote, locator, item["source_type"], item.get("fetched_at", now()), published_at, dimension, dims_json, canon, digest, role),
            )
            self._event(run_id, "evidence.created", {
                "evidence_id": evidence_id,
                "title": item["title"],
                "published_at": published_at,
                "dimensions": dims,
                "subject": subject_from_task_key(task_key),
                "url": url,
                "source_type": item["source_type"],
                "citation_role": role,
            })
            return evidence_id

    def evidence(self, run_id):
        return self.query("SELECT * FROM evidence WHERE run_id=? ORDER BY rowid", (run_id,))

    def replace_claim_evidence(self, run_id, rows):
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM claim_evidence WHERE run_id=?", (run_id,))
            self.conn.executemany(
                "INSERT OR IGNORE INTO claim_evidence(run_id,claim_id,evidence_id,relation) VALUES(?,?,?,?)",
                [(run_id, row["claim_id"], row["evidence_id"], row["relation"]) for row in rows],
            )

    def claim_evidence(self, run_id):
        return self.query("SELECT claim_id,evidence_id,relation FROM claim_evidence WHERE run_id=? ORDER BY rowid", (run_id,))

    def _migrate_evidence(self):
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(evidence)")}
        extras = {
            "published_at": "TEXT",
            "dimension": "TEXT",
            "dimensions": "TEXT DEFAULT '[]'",
            "canonical_url": "TEXT",
            "content_hash": "TEXT",
            "citation_role": "TEXT DEFAULT 'fact'",
        }
        for name, spec in extras.items():
            if name not in columns:
                self.conn.execute(f"ALTER TABLE evidence ADD COLUMN {name} {spec}")
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

    def _migrate_documents(self):
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(documents)")}
        extras = {
            "status": "TEXT DEFAULT 'ready'",
            "error": "TEXT",
            "content_hash": "TEXT",
            "parser_version": "TEXT",
            "source_type": "TEXT DEFAULT 'text'",
            "page_count": "INTEGER",
            "bytes": "INTEGER",
            "citable": "INTEGER DEFAULT 0",
        }
        for name, spec in extras.items():
            if name not in columns:
                self.conn.execute(f"ALTER TABLE documents ADD COLUMN {name} {spec}")
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS document_chunks (
          id TEXT PRIMARY KEY,
          document_id TEXT NOT NULL REFERENCES documents(id),
          project_id TEXT NOT NULL,
          page INTEGER,
          paragraph INTEGER,
          char_start INTEGER,
          char_end INTEGER,
          locator TEXT NOT NULL,
          text TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          embedding BLOB,
          embedding_model TEXT NOT NULL,
          index_version TEXT NOT NULL,
          embedding_dim INTEGER,
          UNIQUE(document_id, locator)
        )""")
        chunk_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(document_chunks)")}
        if "embedding_dim" not in chunk_columns:
            self.conn.execute("ALTER TABLE document_chunks ADD COLUMN embedding_dim INTEGER")
            for chunk_id, blob in self.conn.execute("SELECT id, embedding FROM document_chunks").fetchall():
                dim = (len(blob) // 4) if blob else None
                self.conn.execute("UPDATE document_chunks SET embedding_dim=? WHERE id=?", (dim, chunk_id))
        self.conn.execute("CREATE INDEX IF NOT EXISTS chunks_project_model_idx ON document_chunks(project_id, embedding_model, index_version)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS chunks_project_space_idx ON document_chunks(project_id, embedding_model, index_version, embedding_dim)")
        self.conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS document_chunks_fts USING fts5(text, chunk_id UNINDEXED, project_id UNINDEXED, tokenize='unicode61')")
        self._rebuild_fts_if_needed()

    def _rebuild_fts_if_needed(self):
        self.conn.execute("CREATE TABLE IF NOT EXISTS store_meta (key TEXT PRIMARY KEY, value TEXT)")
        row = self.conn.execute("SELECT value FROM store_meta WHERE key='fts_text_mode'").fetchone()
        if row and row[0] == "explicit_ngrams":
            return
        self.conn.execute("DROP TABLE IF EXISTS document_chunks_fts")
        self.conn.execute("CREATE VIRTUAL TABLE document_chunks_fts USING fts5(text, chunk_id UNINDEXED, project_id UNINDEXED, tokenize='unicode61')")
        from .embeddings import fts_index_text
        for chunk_id, project_id, text in self.conn.execute("SELECT id, project_id, text FROM document_chunks").fetchall():
            self.conn.execute("INSERT INTO document_chunks_fts(text,chunk_id,project_id) VALUES(?,?,?)", (fts_index_text(text or ""), chunk_id, project_id))
        self.conn.execute("INSERT OR REPLACE INTO store_meta(key,value) VALUES('fts_text_mode','explicit_ngrams')")

    def document(self, document_id):
        rows = self.query("SELECT * FROM documents WHERE id=?", (document_id,))
        if not rows:
            return None
        return self._present_document(rows[0])

    def _present_document(self, row):
        item = dict(row)
        item["characters"] = len(item.get("content") or "")
        item["citable"] = bool(item.get("citable"))
        return item

    def list_documents(self, project_id):
        rows = self.query("SELECT * FROM documents WHERE project_id=? ORDER BY created_at DESC", (project_id,))
        return [self._present_document(row) for row in rows]

    def create_document(self, project_id, name, status="processing", source_type="text", bytes=0, content_hash=None):
        document_id = uid("doc")
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO documents(id,project_id,name,content,created_at,status,error,content_hash,parser_version,source_type,page_count,bytes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (document_id, project_id, name, "", now(), status, None, content_hash, None, source_type, None, bytes),
            )
        return self.document(document_id)

    def update_document(self, document_id, **fields):
        allowed = {"status", "error", "content", "page_count", "source_type", "parser_version", "content_hash", "bytes"}
        if not fields or not fields.keys() <= allowed:
            raise ValueError("invalid_document_field")
        with self.lock, self.conn:
            assignments = ",".join(f"{key}=?" for key in fields)
            self.conn.execute(f"UPDATE documents SET {assignments} WHERE id=?", (*fields.values(), document_id))
        return self.document(document_id)

    def add_document(self, project_id, name, content, citable=False):
        from .config import Settings
        from .documents import ingest_text
        document = ingest_text(self, Settings(_env_file=None), project_id, name, content)
        if citable:
            self.set_document_citable(project_id, document["id"], True)
            document = self.document(document["id"])
        return document

    def set_document_citable(self, project_id, document_id, citable):
        with self.lock, self.conn:
            current = self.conn.execute("SELECT id FROM documents WHERE project_id=? AND id=?", (project_id, document_id)).fetchone()
            if current is None:
                return None
            self.conn.execute("UPDATE documents SET citable=? WHERE id=?", (1 if citable else 0, document_id))
        return self.document(document_id)

    def replace_chunks(self, document_id, rows):
        with self.lock, self.conn:
            existing = [item[0] for item in self.conn.execute("SELECT id FROM document_chunks WHERE document_id=?", (document_id,)).fetchall()]
            self.conn.execute("DELETE FROM document_chunks WHERE document_id=?", (document_id,))
            for chunk_id in existing:
                self.conn.execute("DELETE FROM document_chunks_fts WHERE chunk_id=?", (chunk_id,))
            for row in rows:
                self.conn.execute(
                    "INSERT INTO document_chunks(id,document_id,project_id,page,paragraph,char_start,char_end,locator,text,content_hash,embedding,embedding_model,index_version,embedding_dim) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (row["id"], row["document_id"], row["project_id"], row.get("page"), row.get("paragraph"), row.get("char_start"), row.get("char_end"), row["locator"], row["text"], row["content_hash"], row.get("embedding"), row["embedding_model"], row["index_version"], row.get("embedding_dim")),
                )
                from .embeddings import fts_index_text
                self.conn.execute("INSERT INTO document_chunks_fts(text,chunk_id,project_id) VALUES(?,?,?)", (fts_index_text(row["text"]), row["id"], row["project_id"]))

    def chunks_for_document(self, document_id):
        return self.query("SELECT * FROM document_chunks WHERE document_id=? ORDER BY page, paragraph, char_start", (document_id,))

    def chunks_for_project(self, project_id, embedding_model, index_version, embedding_dim=None):
        sql = """SELECT c.* FROM document_chunks c
               JOIN documents d ON d.id=c.document_id
               WHERE c.project_id=? AND c.embedding_model=? AND c.index_version=? AND d.status='ready'"""
        args = [project_id, embedding_model, index_version]
        if embedding_dim:
            sql += " AND c.embedding_dim=?"
            args.append(embedding_dim)
        return self.query(sql, args)

    def chunk(self, chunk_id):
        rows = self.query("SELECT * FROM document_chunks WHERE id=?", (chunk_id,))
        return rows[0] if rows else None

    def fts_search(self, project_id, match, limit, embedding_model, index_version, embedding_dim=None):
        if not match:
            return []
        sql = """SELECT c.id FROM document_chunks_fts f
                   JOIN document_chunks c ON c.id=f.chunk_id
                   JOIN documents d ON d.id=c.document_id
                   WHERE f.project_id=? AND c.project_id=? AND c.embedding_model=? AND c.index_version=?
                     AND d.status='ready' AND document_chunks_fts MATCH ?"""
        args = [project_id, project_id, embedding_model, index_version, match]
        if embedding_dim:
            sql += " AND c.embedding_dim=?"
            args.append(embedding_dim)
        sql += " LIMIT ?"
        args.append(limit)
        with self.lock:
            rows = self.conn.execute(sql, args).fetchall()
        return [row[0] for row in rows]

    def chunks_need_reindex(self, document_id, embedding_model, index_version, embedding_dim=None):
        rows = self.query("SELECT embedding_model,index_version,embedding_dim FROM document_chunks WHERE document_id=? LIMIT 1", (document_id,))
        if not rows:
            return True
        row = rows[0]
        if row["embedding_model"] != embedding_model or row["index_version"] != index_version:
            return True
        stored_dim = row.get("embedding_dim")
        if embedding_dim:
            if not stored_dim or int(stored_dim) != int(embedding_dim):
                return True
        return False

    def known_embedding_dim(self, project_id, embedding_model, index_version):
        rows = self.query(
            """SELECT embedding_dim FROM document_chunks
               WHERE project_id=? AND embedding_model=? AND index_version=? AND embedding_dim IS NOT NULL
               LIMIT 1""",
            (project_id, embedding_model, index_version),
        )
        return int(rows[0]["embedding_dim"]) if rows and rows[0]["embedding_dim"] else None

    def documents_for_reindex(self, project_id=None):
        if project_id:
            return self.query("SELECT * FROM documents WHERE project_id=? ORDER BY created_at", (project_id,))
        return self.query("SELECT * FROM documents ORDER BY created_at")

    def ready_documents(self, project_id):
        return [item for item in self.list_documents(project_id) if item.get("status") == "ready"]

    def _migrate_execution_records(self):
        self.conn.execute("""
        CREATE TABLE IF NOT EXISTS execution_records (
          id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL,
          operation_key TEXT,
          kind TEXT NOT NULL,
          stage TEXT,
          subject TEXT,
          name TEXT,
          role TEXT,
          status TEXT,
          started_at TEXT,
          finished_at TEXT,
          latency_ms INTEGER,
          tokens_in INTEGER DEFAULT 0,
          tokens_out INTEGER DEFAULT 0,
          tokens_total INTEGER DEFAULT 0,
          preview TEXT,
          payload TEXT,
          truncated INTEGER DEFAULT 0,
          seq INTEGER
        )""")
        self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS execution_record_key_idx ON execution_records(run_id, operation_key)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS execution_record_run_idx ON execution_records(run_id, seq)")

    def _present_record(self, row):
        if row is None:
            return None
        item = dict(row)
        for key in ("preview", "payload"):
            if item.get(key):
                try:
                    item[key] = json.loads(item[key])
                except (TypeError, json.JSONDecodeError):
                    pass
        item["truncated"] = bool(item.get("truncated"))
        return item

    def execution_record(self, record_id):
        with self.lock:
            row = self.conn.execute("SELECT * FROM execution_records WHERE id=?", (record_id,)).fetchone()
        return self._present_record(row)

    def execution_record_by_key(self, run_id, operation_key):
        with self.lock:
            row = self.conn.execute("SELECT * FROM execution_records WHERE run_id=? AND operation_key=?", (run_id, operation_key)).fetchone()
        return self._present_record(row)

    def execution_records(self, run_id):
        with self.lock:
            rows = self.conn.execute("SELECT * FROM execution_records WHERE run_id=? ORDER BY seq, started_at", (run_id,)).fetchall()
        return [self._present_record(row) for row in rows]

    def upsert_execution_record(self, row):
        record_id = row["id"]
        with self.lock, self.conn:
            existing = self.conn.execute("SELECT * FROM execution_records WHERE id=?", (record_id,)).fetchone()
            if existing is None:
                seq = self.conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM execution_records WHERE run_id=?", (row["run_id"],)).fetchone()[0]
                self.conn.execute(
                    """INSERT INTO execution_records(id,run_id,operation_key,kind,stage,subject,name,role,status,started_at,finished_at,latency_ms,tokens_in,tokens_out,tokens_total,preview,payload,truncated,seq)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        record_id,
                        row["run_id"],
                        row.get("operation_key"),
                        row.get("kind") or "llm",
                        row.get("stage"),
                        row.get("subject"),
                        row.get("name"),
                        row.get("role"),
                        row.get("status") or "running",
                        row.get("started_at") or now(),
                        row.get("finished_at"),
                        row.get("latency_ms"),
                        row.get("tokens_in") or 0,
                        row.get("tokens_out") or 0,
                        row.get("tokens_total") or 0,
                        json.dumps(row.get("preview") or {}, ensure_ascii=False),
                        json.dumps(row.get("payload") or {}, ensure_ascii=False),
                        1 if row.get("truncated") else 0,
                        seq,
                    ),
                )
                return self.execution_record(record_id)
            merged = dict(existing)
            for key, value in row.items():
                if key == "id":
                    continue
                if key in {"preview", "payload"} and not isinstance(value, str):
                    merged[key] = json.dumps(value if value is not None else {}, ensure_ascii=False)
                elif key == "truncated":
                    merged[key] = 1 if value else 0
                elif value is not None or key in row:
                    merged[key] = value
            self.conn.execute(
                """UPDATE execution_records SET operation_key=?,kind=?,stage=?,subject=?,name=?,role=?,status=?,started_at=?,finished_at=?,latency_ms=?,tokens_in=?,tokens_out=?,tokens_total=?,preview=?,payload=?,truncated=? WHERE id=?""",
                (
                    merged.get("operation_key"),
                    merged.get("kind"),
                    merged.get("stage"),
                    merged.get("subject"),
                    merged.get("name"),
                    merged.get("role"),
                    merged.get("status"),
                    merged.get("started_at"),
                    merged.get("finished_at"),
                    merged.get("latency_ms"),
                    merged.get("tokens_in") or 0,
                    merged.get("tokens_out") or 0,
                    merged.get("tokens_total") or 0,
                    merged.get("preview") if isinstance(merged.get("preview"), str) else json.dumps(merged.get("preview") or {}, ensure_ascii=False),
                    merged.get("payload") if isinstance(merged.get("payload"), str) else json.dumps(merged.get("payload") or {}, ensure_ascii=False),
                    merged.get("truncated") or 0,
                    record_id,
                ),
            )
        return self.execution_record(record_id)
