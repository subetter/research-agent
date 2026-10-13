import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


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
            self._migrate_documents()

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

    def add_evidence(self, project_id, run_id, task_key, item):
        from .freshness import parse_published_at
        published_at = parse_published_at(item.get("published_at"))
        with self.lock, self.conn:
            existing = self.conn.execute("SELECT id FROM evidence WHERE run_id=? AND task_key=? AND url=? AND locator=?", (run_id, task_key, item["url"], item["locator"])).fetchone()
            if existing:
                return existing[0]
            evidence_id = uid("ev")
            self.conn.execute(
                "INSERT INTO evidence(id,project_id,run_id,task_key,title,url,quote,locator,source_type,fetched_at,published_at,dimension) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (evidence_id, project_id, run_id, task_key, item["title"], item["url"], item["quote"], item["locator"], item["source_type"], item.get("fetched_at", now()), published_at, item.get("dimension")),
            )
            self._event(run_id, "evidence.created", {"evidence_id": evidence_id, "title": item["title"], "published_at": published_at})
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

    def document(self, document_id):
        rows = self.query("SELECT * FROM documents WHERE id=?", (document_id,))
        if not rows:
            return None
        return self._present_document(rows[0])

    def _present_document(self, row):
        item = dict(row)
        item["characters"] = len(item.get("content") or "")
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

    def add_document(self, project_id, name, content):
        from .config import Settings
        from .documents import ingest_text
        return ingest_text(self, Settings(_env_file=None), project_id, name, content)

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
                self.conn.execute("INSERT INTO document_chunks_fts(text,chunk_id,project_id) VALUES(?,?,?)", (row["text"], row["id"], row["project_id"]))

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
