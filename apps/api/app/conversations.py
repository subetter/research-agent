import asyncio
import sqlite3
import json
import time
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from .store import uid, now
from .llm import complete, stream_complete
from .tracing import get_tracing


class ConversationCreate(BaseModel):
    title: str = Field(default="新会话", min_length=1, max_length=100)


class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=10000)
    client_id: str = Field(min_length=8, max_length=100)


def conversation_router(store_getter, config):
    router = APIRouter(prefix="/api/conversations", tags=["conversations"])

    def get(conversation_id):
        items = store_getter().query("SELECT * FROM conversations WHERE id=?", (conversation_id,))
        if not items:
            raise HTTPException(404, "会话不存在")
        return items[0]

    def history(conversation_id):
        return store_getter().query("SELECT * FROM chat_messages WHERE conversation_id=? ORDER BY rowid", (conversation_id,))

    @router.get("")
    def conversations(request: Request):
        return store_getter().query("SELECT c.*, (SELECT COUNT(*) FROM chat_messages m WHERE m.conversation_id=c.id) AS messages_count FROM conversations c WHERE owner_id=? ORDER BY updated_at DESC", (request.state.user["id"],))

    @router.post("", status_code=201)
    def create(body: ConversationCreate, request: Request):
        store = store_getter()
        conversation_id = uid("chat")
        with store.lock, store.conn:
            store.conn.execute("INSERT INTO conversations VALUES(?,?,?,?,?)", (conversation_id, request.state.user["id"], body.title.strip() or "新会话", now(), now()))
        return get(conversation_id)

    @router.get("/{conversation_id}")
    def conversation(conversation_id: str):
        return {"conversation": get(conversation_id), "messages": history(conversation_id)}

    @router.post("/{conversation_id}/messages")
    async def send(conversation_id: str, body: MessageCreate, stream: bool = False):
        store = store_getter()
        convo = get(conversation_id)
        content = body.content.strip()
        if not content:
            raise HTTPException(422, "消息不能为空")
        mode = config.effective_chat_mode
        if mode not in {"demo", "live"}:
            raise HTTPException(503, "RESEARCH_MODE 必须为 demo 或 live")
        if mode == "live" and not (config.llm_api_key and config.llm_model):
            raise HTTPException(503, "普通会话需要配置模型与密钥，不需要 Tavily 密钥")
        assistant_id = uid("msg")
        try:
            with store.lock, store.conn:
                existing = store.conn.execute("SELECT id,content FROM chat_messages WHERE conversation_id=? AND client_id=? AND role='user'", (conversation_id, body.client_id)).fetchone()
                if existing:
                    if existing["content"] != content:
                        raise HTTPException(409, "同一个请求标识不能用于不同消息")
                    snapshot = {"conversation": get(conversation_id), "messages": history(conversation_id), "replayed": True}
                    if stream:
                        async def replay():
                            yield "event: done\ndata: " + json.dumps(snapshot, ensure_ascii=False) + "\n\n"
                        return StreamingResponse(replay(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
                    return snapshot
                pending = store.conn.execute("SELECT id FROM chat_messages WHERE conversation_id=? AND status='pending'", (conversation_id,)).fetchone()
                if pending:
                    raise HTTPException(409, "该会话正在生成回复，请稍后再发送")
                stamp = now()
                store.conn.execute("INSERT INTO chat_messages VALUES(?,?,?,?,?,?,?,?,?)", (uid("msg"), conversation_id, "user", content, "completed", None, 0, body.client_id, stamp))
                store.conn.execute("INSERT INTO chat_messages VALUES(?,?,?,?,?,?,?,?,?)", (assistant_id, conversation_id, "assistant", "", "pending", "demo" if mode == "demo" else config.llm_model, 0, None, stamp))
                title = content[:40] if convo["title"] == "新会话" else convo["title"]
                store.conn.execute("UPDATE conversations SET title=?,updated_at=? WHERE id=?", (title, stamp, conversation_id))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "该会话正在生成回复")

        if stream:
            async def events():
                reply = ""
                tokens = 0
                checkpoint = time.monotonic()
                tracing = get_tracing()
                token = tracing.start(conversation_id, name="chat-conversation", metadata={"conversation_id": conversation_id, "stream": True})
                def snapshot():
                    return {"conversation": get(conversation_id), "messages": history(conversation_id)}
                def encode(event, data):
                    return "event: " + event + "\ndata: " + json.dumps(data, ensure_ascii=False) + "\n\n"
                def save(status):
                    with store.lock, store.conn:
                        store.conn.execute("UPDATE chat_messages SET content=?,status=?,tokens=? WHERE id=?", (reply, status, tokens, assistant_id))
                        store.conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now(), conversation_id))
                try:
                    with tracing.span("chat.turn", trace_id=conversation_id, metadata={"assistant_id": assistant_id, "mode": mode}):
                        yield encode("start", snapshot())
                        prior = [m for m in history(conversation_id) if m["status"] == "completed"]
                        if mode == "demo":
                            previous = [m["content"] for m in prior if m["role"] == "user"]
                            text = f"【演示回复 · 未调用真实模型】\n\n收到你的问题：{content}\n\n这是本会话的第 {len(previous)} 次提问。"
                            if len(previous) > 1:
                                text += f"上一条问题是「{previous[-2][:120]}」。"
                            text += "\n\n配置模型密钥后，可使用真实的多轮流式问答。"
                            async def chunks():
                                for index in range(0, len(text), 8):
                                    await asyncio.sleep(.025)
                                    yield {"text": text[index:index+8]}
                            source = chunks()
                        else:
                            context = []
                            size = 0
                            for message in reversed(prior[-30:]):
                                if size + len(message["content"]) > 30000:
                                    break
                                context.append({"role": message["role"], "content": message["content"]})
                                size += len(message["content"])
                            model_messages = [{"role": "system", "content": "你是研究工作台的日常会话助手。结合当前会话上下文，用中文清楚回答。没有搜索工具，不要声称浏览或验证了实时资料。"}, *reversed(context)]
                            source = stream_complete(config, model_messages)
                        async for chunk in source:
                            if "tokens" in chunk:
                                tokens = chunk["tokens"]
                            if chunk.get("text"):
                                reply += chunk["text"]
                                if time.monotonic() - checkpoint > .2:
                                    save("pending")
                                    checkpoint = time.monotonic()
                                yield encode("delta", {"id": assistant_id, "text": chunk["text"]})
                        save("completed")
                        yield encode("done", snapshot())
                except BaseException as exc:
                    failure = str(exc) if isinstance(exc, ValueError) else "生成连接中断；已保存收到的内容，请重新提问。"
                    reply = (reply + "\n\n" + failure) if reply else failure
                    save("failed")
                    if isinstance(exc, (asyncio.CancelledError, GeneratorExit)):
                        raise
                    yield encode("error", {"message": failure, **snapshot()})
                finally:
                    tracing.flush()
                    tracing.reset(token)
            return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        tracing = get_tracing()
        token = tracing.start(conversation_id, name="chat-conversation", metadata={"conversation_id": conversation_id, "stream": False})
        try:
            with tracing.span("chat.turn", trace_id=conversation_id, metadata={"assistant_id": assistant_id, "mode": mode}):
                prior = [m for m in history(conversation_id) if m["status"] == "completed"]
                if mode == "demo":
                    await asyncio.sleep(0.4)
                    previous = [m["content"] for m in prior if m["role"] == "user"]
                    reply = f"【演示回复 · 未调用真实模型】\n\n收到你的问题：{content}\n\n这是本会话的第 {len(previous)} 次提问。"
                    if len(previous) > 1:
                        reply += f"上一条问题是「{previous[-2][:120]}」，它与当前消息都已保存在同一个会话中。"
                    reply += "\n\n配置模型密钥后，这里会使用会话上下文生成真实回答。普通会话不执行深度研究或联网搜索。"
                    tokens = 0
                else:
                    context = []
                    size = 0
                    for message in reversed(prior[-30:]):
                        if size + len(message["content"]) > 30000:
                            break
                        context.append({"role": message["role"], "content": message["content"]})
                        size += len(message["content"])
                    messages = [{"role": "system", "content": "你是研究工作台的日常会话助手。结合当前会话上下文，用中文清楚回答。此会话没有搜索工具，不要声称浏览或验证了实时资料。"}, *reversed(context)]
                    result = await complete(config, messages)
                    reply = result["choices"][0]["message"].get("content")
                    if not isinstance(reply, str) or not reply.strip():
                        raise ValueError("模型返回空消息")
                    tokens = result.get("usage", {}).get("total_tokens", 0)
                with store.lock, store.conn:
                    store.conn.execute("UPDATE chat_messages SET content=?,status='completed',tokens=? WHERE id=?", (reply, tokens, assistant_id))
                    store.conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now(), conversation_id))
        except BaseException as exc:
            failure = str(exc) if isinstance(exc, ValueError) else "回复生成失败；用户消息已保存。可重新提问，或检查模型配置。"
            with store.lock, store.conn:
                store.conn.execute("UPDATE chat_messages SET content=?,status='failed' WHERE id=?", (failure, assistant_id))
            if isinstance(exc, asyncio.CancelledError):
                raise
        finally:
            tracing.flush()
            tracing.reset(token)
        return {"conversation": get(conversation_id), "messages": history(conversation_id)}

    return router
