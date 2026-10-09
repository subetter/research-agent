import asyncio
import csv
import io
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, Response, JSONResponse
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from .config import settings, Settings
from .store import Store
from .schemas import ProjectCreate, RunCreate, PlanUpdate, RunCommand, ClarifyUpdate, DocumentUpdate, ArtifactEdit
from .engine import Engine, coverage_matrix
from .accounts import account_router, session_user, COOKIE
from .conversations import conversation_router
from .admin import admin_router

TERMINAL = {"completed", "partial", "failed", "cancelled"}


def create_app(config: Settings | None = None):
    config = config or settings

    @asynccontextmanager
    async def lifespan(app):
        config.data_path.mkdir(parents=True, exist_ok=True)
        store = Store(config.data_path / "workbench.sqlite")
        with store.lock, store.conn:
            store.conn.execute("UPDATE chat_messages SET status='failed',content=CASE WHEN content='' THEN '服务重启中断了生成；消息已保存，请重新提问。' ELSE content || char(10) || '服务重启中断了生成，以上内容已保存。' END WHERE status='pending'")
        async with AsyncSqliteSaver.from_conn_string(str(config.data_path / "checkpoints.sqlite")) as saver:
            app.state.store = store
            app.state.engine = Engine(config, store, saver)
            # A local single-process server never silently restarts billable calls.
            for run in store.query("SELECT * FROM runs WHERE status IN ('running','planning','pause_requested','cancel_requested')"):
                store.update(run["id"], status="cancelled" if run["status"] == "cancel_requested" else "paused")
            try:
                yield
            finally:
                await app.state.engine.shutdown()
                store.close()

    app = FastAPI(title="Research Workbench", version="0.3.0", lifespan=lifespan)

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Bind to loopback and reject foreign browser origins, including DNS-rebinding hosts.
        if request.headers.get("host", "").split(":")[0] not in {"localhost", "127.0.0.1", "testserver"}:
            return Response("Local access only", status_code=403)
        origin = request.headers.get("origin")
        if origin and origin not in {"http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:8000", "http://127.0.0.1:8000"}:
            return Response("Origin not allowed", status_code=403)
        path = request.url.path
        public = {"/api/health", "/api/auth/bootstrap", "/api/auth/register", "/api/auth/login"}
        if path.startswith("/api/") and path not in public:
            user = session_user(app.state.store, request.cookies.get(COOKIE))
            if not user:
                return JSONResponse({"detail": "请先登录"}, status_code=401)
            request.state.user = user
            if path.startswith("/api/admin/"):
                if user["role"] != "admin":
                    return JSONResponse({"detail": "仅管理员可访问"}, status_code=403)
            else:
                # Owner checks also cover evidence, traces, uploads, commands and exports.
                match = re.match(r"^/api/(projects|runs|conversations)/([^/]+)(?:/|$)", path)
                if match:
                    resource, resource_id = match.groups()
                    store = app.state.store
                    if resource == "projects":
                        project = store.project(resource_id)
                        owner = project.get("owner_id") if project else None
                    elif resource == "runs":
                        run = store.run(resource_id)
                        project = store.project(run["project_id"]) if run else None
                        owner = project.get("owner_id") if project else None
                    else:
                        records = store.query("SELECT owner_id FROM conversations WHERE id=?", (resource_id,))
                        owner = records[0]["owner_id"] if records else None
                    if owner != user["id"]:
                        return JSONResponse({"detail": "资源不存在或无权访问"}, status_code=404)
        return await call_next(request)

    def store():
        return app.state.store

    def get_project(project_id):
        project = store().project(project_id)
        if not project:
            raise HTTPException(404, "项目不存在")
        return project

    def get_run(run_id):
        run = store().run(run_id)
        if not run:
            raise HTTPException(404, "任务不存在")
        run["budget"] = {"model_limit": config.max_model_calls, "report_reserved": config.report_reserved_calls, "search_limit": run["plan"].get("max_search_calls", config.max_search_calls)}
        plan = run.get("plan") or {}
        run["waiting"] = plan.get("waiting") if run["status"] == "waiting_input" else None
        if plan.get("subjects") and plan.get("dimensions"):
            run["coverage"] = coverage_matrix(plan, store().evidence(run_id))
        versions = store().list_artifact_versions(run_id)
        if not versions and run.get("artifact"):
            store().add_artifact_version(run_id, run["project_id"], run["artifact"], origin="system")
            versions = store().list_artifact_versions(run_id)
        run["artifact_versions"] = versions
        run["artifact_version"] = versions[-1]["version"] if versions else None
        return run

    @app.get("/api/health")
    def health():
        return {"status": "ok", "mode": config.research_mode, "live_ready": config.live_ready,
                "chat_ready": bool(config.llm_api_key and config.llm_model), "chat_mode": config.effective_chat_mode,
                "model": config.llm_model, "provider": config.llm_provider,
                "version": "0.3.0", "storage": "SQLite", "runtime": "LangGraph"}

    @app.get("/api/projects")
    def projects(request: Request):
        return store().query("SELECT p.*, (SELECT COUNT(*) FROM runs r WHERE r.project_id=p.id) AS runs_count FROM projects p WHERE owner_id=? ORDER BY created_at DESC", (request.state.user["id"],))

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectCreate, request: Request):
        if not body.name.strip():
            raise HTTPException(422, "项目名称不能为空")
        return store().create_project(body.name.strip(), body.background, request.state.user["id"])

    @app.get("/api/projects/{project_id}")
    def project(project_id: str):
        return get_project(project_id)

    @app.get("/api/projects/{project_id}/runs")
    def runs(project_id: str):
        get_project(project_id)
        return store().query("SELECT * FROM runs WHERE project_id=? ORDER BY created_at DESC", (project_id,))

    @app.post("/api/projects/{project_id}/runs", status_code=202)
    async def create_run(project_id: str, body: RunCreate, request: Request):
        get_project(project_id)
        if config.research_mode not in ("demo", "live"):
            raise HTTPException(503, "RESEARCH_MODE 必须为 demo 或 live")
        if config.research_mode == "live" and not config.live_ready:
            raise HTTPException(503, "联网模式需要配置模型、模型密钥与 Tavily 密钥")
        if body.parent_run_id:
            parent = get_run(body.parent_run_id)
            if get_project(parent["project_id"])["owner_id"] != request.state.user["id"]:
                raise HTTPException(404, "父任务不存在或无权访问")
            if parent["project_id"] != project_id or not parent["artifact"]:
                raise HTTPException(400, "父任务必须为同一项目中已交付的研究")
        run = store().create_run(project_id, body.question, config.research_mode, {"request": body.model_dump()}, body.parent_run_id)
        app.state.engine.schedule(run["id"], {"run_id": run["id"], "request": body.model_dump()})
        return run

    @app.get("/api/runs/{run_id}")
    def run(run_id: str):
        return get_run(run_id)

    @app.put("/api/runs/{run_id}/plan")
    def plan(run_id: str, body: PlanUpdate):
        run = get_run(run_id)
        if run["status"] != "waiting_input":
            raise HTTPException(409, "仅可在启动前编辑计划")
        if (run.get("plan") or {}).get("waiting") == "clarify":
            raise HTTPException(409, "请先补充研究对象与比较维度")
        next_plan = body.plan.model_dump()
        next_plan["max_search_calls"] = min(next_plan["max_search_calls"], config.max_search_calls)
        next_plan["max_gap_rounds"] = min(next_plan["max_gap_rounds"], config.max_gap_rounds)
        current_plan = run.get("plan") or {}
        if not next_plan.get("skill_name"):
            next_plan["skill_name"] = current_plan.get("skill_name") or ""
            next_plan["skill_version"] = current_plan.get("skill_version") or ""
        try:
            return store().update(run_id, body.expected_revision, plan=next_plan)
        except ValueError:
            raise HTTPException(409, "计划已更新，请刷新后重试")

    @app.put("/api/runs/{run_id}/clarify")
    def clarify(run_id: str, body: ClarifyUpdate):
        run = get_run(run_id)
        if run["status"] != "waiting_input" or (run.get("plan") or {}).get("waiting") != "clarify":
            raise HTTPException(409, "当前不是澄清阶段")
        current = dict(run.get("plan") or {})
        request = dict(current.get("request") or {"question": run["question"]})
        request["subjects"] = body.subjects
        request["dimensions"] = body.dimensions
        current["request"] = request
        try:
            return store().update(run_id, body.expected_revision, plan=current)
        except ValueError:
            raise HTTPException(409, "状态已更新，请刷新后重试")

    @app.post("/api/runs/{run_id}/commands", status_code=202)
    async def command(run_id: str, body: RunCommand):
        run = get_run(run_id)
        if run["revision"] != body.expected_revision:
            raise HTTPException(409, "状态已更新，请刷新后重试")
        allowed = {"start": {"waiting_input"}, "resume": {"paused"}, "retry": {"failed"}, "pause": {"running", "planning"}, "cancel": {"planning", "waiting_input", "running", "paused", "pause_requested"}}
        if run["status"] not in allowed[body.action]:
            raise HTTPException(409, "当前状态不支持该操作")
        active = app.state.engine.tasks.get(run_id)
        if body.action in {"start", "resume", "retry"}:
            if active and not active.done():
                raise HTTPException(409, "后台任务正在收尾，请稍后重试")
            next_input = await app.state.engine.resume_input(run_id)
            store().update(run_id, body.expected_revision, status="running", error=None)
            app.state.engine.schedule(run_id, next_input)
        else:
            is_active = active and not active.done()
            target = ("pause_requested" if body.action == "pause" else "cancel_requested") if is_active else ("paused" if body.action == "pause" else "cancelled")
            store().update(run_id, body.expected_revision, status=target)
        return get_run(run_id)

    @app.get("/api/runs/{run_id}/evidence")
    def evidence(run_id: str):
        get_run(run_id)
        return store().evidence(run_id)

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request, after: int = 0):
        get_run(run_id)
        try:
            cursor = max(after, int(request.headers.get("last-event-id", "0")))
        except ValueError:
            raise HTTPException(400, "无效的事件序号")
        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                rows = store().query("SELECT * FROM events WHERE run_id=? AND seq>? ORDER BY seq", (run_id, cursor))
                for event in rows:
                    cursor = event["seq"]
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if get_run(run_id)["status"] in TERMINAL:
                    break
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.6)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/runs/{run_id}/trace")
    def trace(run_id: str):
        get_run(run_id)
        return store().query("SELECT * FROM events WHERE run_id=? ORDER BY seq", (run_id,))

    @app.get("/api/projects/{project_id}/documents")
    def documents(project_id: str):
        get_project(project_id)
        rows = store().query("SELECT id,name,LENGTH(content) AS characters,created_at,citable FROM documents WHERE project_id=? ORDER BY created_at DESC", (project_id,))
        for row in rows:
            row["citable"] = bool(row.get("citable"))
        return rows

    @app.post("/api/projects/{project_id}/documents", status_code=201)
    async def upload(project_id: str, file: UploadFile = File(...), citable: bool = Form(False)):
        get_project(project_id)
        name = Path(file.filename or "document.txt").name
        if Path(name).suffix.lower() not in {".txt", ".md", ".csv"}:
            raise HTTPException(415, "首版支持 UTF-8 TXT、Markdown 和 CSV；PDF 解析将在下一阶段加入")
        raw = await file.read(1_000_001)
        if len(raw) > 1_000_000:
            raise HTTPException(413, "文件最大 1 MB")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise HTTPException(422, "文件需要 UTF-8 编码")
        return store().add_document(project_id, name, text, citable)

    @app.patch("/api/projects/{project_id}/documents/{document_id}")
    def update_document(project_id: str, document_id: str, body: DocumentUpdate):
        get_project(project_id)
        document = store().set_document_citable(project_id, document_id, body.citable)
        if not document:
            raise HTTPException(404, "资料不存在")
        document["citable"] = bool(document.get("citable"))
        document["characters"] = len(document.get("content") or "")
        document.pop("content", None)
        return document

    @app.get("/api/runs/{run_id}/artifacts")
    def artifacts(run_id: str):
        run = get_run(run_id)
        return run["artifact_versions"]

    @app.get("/api/runs/{run_id}/artifacts/{version}")
    def artifact_version(run_id: str, version: int):
        get_run(run_id)
        row = store().get_artifact_version(run_id, version)
        if not row:
            raise HTTPException(404, "成果版本不存在")
        return row

    @app.patch("/api/runs/{run_id}/artifact")
    def edit_artifact(run_id: str, body: ArtifactEdit):
        run = get_run(run_id)
        if not run.get("artifact"):
            raise HTTPException(409, "尚未生成成果")
        if run["status"] not in {"completed", "partial"}:
            raise HTTPException(409, "仅可编辑已交付的成果")
        base = store().get_artifact_version(run_id, body.base_version)
        if not base:
            raise HTTPException(404, "成果版本不存在")
        content = json.loads(json.dumps(base["content"]))
        if body.summary is not None:
            content["summary"] = body.summary
        by_id = {claim["id"]: claim for claim in content.get("claims") or []}
        for edit in body.claims:
            claim = by_id.get(edit.id)
            if not claim:
                raise HTTPException(404, "结论不存在")
            if claim.get("text") == edit.text:
                continue
            claim["text"] = edit.text
            claim["verification"] = "unconfirmed"
        claims = content.get("claims") or []
        coverage = content.get("coverage") or {}
        content["metrics"] = {
            "claims": len(claims),
            "evidence": content.get("metrics", {}).get("evidence", 0),
            "unknown": sum(item.get("kind") == "unknown" for item in claims),
            "with_references": sum(bool(item.get("evidence_ids")) for item in claims),
            "supported": sum(item.get("verification") in {"fully", "partial", "reference_checked"} for item in claims),
            "cells_covered": coverage.get("covered", 0),
            "cells_total": coverage.get("total", 0),
        }
        store().add_artifact_version(run_id, run["project_id"], content, origin="user", parent_version_id=base["id"])
        try:
            store().update(run_id, body.expected_revision, artifact=content)
        except ValueError:
            raise HTTPException(409, "状态已更新，请刷新后重试")
        return get_run(run_id)

    @app.get("/api/runs/{run_id}/export")
    def export(run_id: str, format: str = "markdown", version: int | None = None):
        run = get_run(run_id)
        if version is not None:
            row = store().get_artifact_version(run_id, version)
            if not row:
                raise HTTPException(404, "成果版本不存在")
            artifact = row["content"]
        else:
            artifact = run["artifact"]
        if not artifact:
            raise HTTPException(409, "尚未生成成果")
        if format == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["模式", "对象", "维度", "结论", "类型", "核验", "证据 ID"])
            for claim in artifact["claims"]:
                values = [run["mode"], claim["subject"], claim["dimension"], claim["text"], claim["kind"], claim.get("verification", ""), ";".join(claim["evidence_ids"])]
                # Spreadsheet formula injection protection.
                writer.writerow(["'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value for value in values])
            content = "\ufeff" + output.getvalue()
            suffix, mime = "csv", "text/csv"
        elif format == "markdown":
            lines = [f"# {artifact['title']}", "", f"> 模式：{run['mode']}。{artifact['verification_note']}", "", artifact["summary"], ""]
            for claim in artifact["claims"]:
                lines.extend([f"## {claim['subject']} · {claim['dimension']}", "", claim["text"], "", f"核验：{claim.get('verification', '')}", "", "证据：" + ", ".join(claim["evidence_ids"]), ""])
            lines.append("## 原文证据")
            for ev in store().evidence(run_id):
                lines.extend(["", f"### {ev['id']}", "", f"{ev['title']} · {ev['url']} · {ev['locator']}", "", ev["quote"], ""])
            content = "\n".join(lines)
            suffix, mime = "md", "text/markdown"
        else:
            raise HTTPException(400, "仅支持 markdown 或 csv")
        return Response(content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{run_id}.{suffix}"'})

    app.include_router(account_router(store))
    app.include_router(conversation_router(store, config))
    app.include_router(admin_router(store))
    return app


app = create_app()
