from fastapi import APIRouter, Request, Query, HTTPException
from .store import uid, now


def admin_router(store_getter):
    router = APIRouter(prefix="/api/admin", tags=["admin"])

    def audit(request, action, target=None):
        store = store_getter()
        with store.lock, store.conn:
            store.conn.execute("INSERT INTO admin_audit VALUES(?,?,?,?,?)", (uid("audit"), request.state.user["id"], action, target, now()))

    @router.get("/overview")
    def overview():
        store = store_getter()
        return {name: store.query(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] for name, table in [("users", "users"), ("conversations", "conversations"), ("messages", "chat_messages"), ("research_runs", "runs")]}

    @router.get("/users")
    def users(q: str = "", limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        query = "%" + q[:100] + "%"
        store = store_getter()
        records = store.query("""SELECT u.id,u.username,u.display_name,u.role,u.created_at,
          (SELECT COUNT(*) FROM conversations c WHERE c.owner_id=u.id) AS conversations_count,
          (SELECT COUNT(*) FROM projects p JOIN runs r ON r.project_id=p.id WHERE p.owner_id=u.id) AS runs_count
          FROM users u WHERE u.username LIKE ? OR u.display_name LIKE ? ORDER BY u.created_at DESC LIMIT ? OFFSET ?""", (query, query, limit, offset))
        total = store.query("SELECT COUNT(*) AS n FROM users WHERE username LIKE ? OR display_name LIKE ?", (query, query))[0]["n"]
        return {"items": records, "total": total}

    @router.get("/records")
    def records(request: Request, user_id: str | None = None, q: str = "", limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
        store = store_getter()
        query = "%" + q[:100] + "%"
        sql = """SELECT * FROM (
          SELECT c.id,c.owner_id,u.username,u.display_name,c.title,'chat' AS kind,c.updated_at,'saved' AS status FROM conversations c JOIN users u ON u.id=c.owner_id
          UNION ALL
          SELECT r.id,p.owner_id,u.username,u.display_name,r.question AS title,'research' AS kind,r.updated_at,r.status FROM runs r JOIN projects p ON p.id=r.project_id JOIN users u ON u.id=p.owner_id
          ) WHERE (? IS NULL OR owner_id=?) AND title LIKE ?"""
        args = (user_id, user_id, query)
        items = store.query(sql + " ORDER BY updated_at DESC LIMIT ? OFFSET ?", (*args, limit, offset))
        total = store.query("SELECT COUNT(*) AS n FROM (" + sql + ")", args)[0]["n"]
        audit(request, "records.list", user_id)
        return {"items": items, "total": total}

    @router.get("/records/{kind}/{record_id}")
    def detail(kind: str, record_id: str, request: Request):
        store = store_getter()
        if kind == "chat":
            records = store.query("SELECT c.*,u.username,u.display_name FROM conversations c JOIN users u ON u.id=c.owner_id WHERE c.id=?", (record_id,))
            if not records:
                raise HTTPException(404, "会话不存在")
            result = {"kind": kind, "record": records[0], "messages": store.query("SELECT * FROM chat_messages WHERE conversation_id=? ORDER BY rowid", (record_id,))}
        elif kind == "research":
            run = store.run(record_id)
            if not run:
                raise HTTPException(404, "研究不存在")
            project = store.project(run["project_id"])
            user = store.query("SELECT username,display_name FROM users WHERE id=?", (project["owner_id"],))
            result = {"kind": kind, "record": {**run, **(user[0] if user else {})}, "evidence": store.evidence(record_id)}
        else:
            raise HTTPException(404, "记录类型不存在")
        audit(request, f"{kind}.read", record_id)
        return result

    @router.get("/audit")
    def audit_list(limit: int = Query(50, ge=1, le=100)):
        return store_getter().query("SELECT a.*,u.username FROM admin_audit a JOIN users u ON u.id=a.actor_id ORDER BY a.created_at DESC LIMIT ?", (limit,))

    return router
