"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUp, Check, Layers, Loader2, MessageSquare, Plus, Sparkles, User as UserIcon } from "lucide-react";
import { api, streamApi, User } from "./api";

import { createTextPlayback } from "./text-playback";

type Conversation = {id: string; title: string; updated_at: string; messages_count?: number};
type Message = {id: string; role: string; content: string; status: string; model: string | null; tokens: number; created_at: string};
type History = {conversation: Conversation; messages: Message[]};

export default function ChatWorkspace({user}: {user: User}) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selected, setSelected] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [content, setContent] = useState("");
  const [mode, setMode] = useState("demo");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const selectedRef = useRef(selected); selectedRef.current = selected;
  const requestRef = useRef<{content: string; id: string} | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const streamingRef = useRef(false);
  const playbackRef = useRef<ReturnType<typeof createTextPlayback> | null>(null);
  useEffect(() => () => {playbackRef.current?.cancel();}, []);

  const loadList = useCallback(async () => {
    const list = await api<Conversation[]>("/conversations"); setConversations(list); return list;
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([api<Conversation[]>("/conversations"), api<{chat_mode: string}>("/health")]).then(([list, health]) => {
      if (active) {setConversations(list); setMode(health.chat_mode); if (list.length) setSelected(list[0].id);}
    }).catch(e => {if (active) setError(e.message);});
    return () => {active = false;};
  }, []);

  useEffect(() => {
    setMessages([]); setError(""); setContent(""); requestRef.current = null;
    if (!selected) return;
    let active = true;
    setLoading(true);
    api<History>(`/conversations/${selected}`).then(history => {if (active && !streamingRef.current) setMessages(history.messages);}).catch(e => {if (active) setError(e.message);}).finally(() => {if (active) setLoading(false);});
    return () => {active = false;};
  }, [selected]);

  useEffect(() => {scrollRef.current?.scrollIntoView({behavior: "smooth", block: "end"});}, [messages, busy]);

  // A request may still be finishing after a browser reconnect.
  useEffect(() => {
    if (busy || !messages.some(m => m.status === "pending") || !selected) return;
    const timer = setInterval(() => {api<History>(`/conversations/${selected}`).then(h => {if (selectedRef.current === selected && !streamingRef.current) setMessages(h.messages);}).catch(() => {});}, 1500);
    return () => clearInterval(timer);
  }, [messages, selected, busy]);

  async function newConversation() {
    setBusy(true); setError("");
    try {
      const conversation = await api<Conversation>("/conversations", {method: "POST", body: JSON.stringify({title: "新会话"})});
      await loadList(); setSelected(conversation.id);
    } catch(e) {setError(e instanceof Error ? e.message : "创建失败");} finally {setBusy(false);}
  }

  async function send() {
    const text = content.trim();
    if (!text || busy || messages.some(m => m.status === "pending")) return;
    setBusy(true); setError("");
    let id = selected;
    try {
      if (!id) {
        const conversation = await api<Conversation>("/conversations", {method: "POST", body: JSON.stringify({title: text.slice(0, 40)})});
        id = conversation.id; selectedRef.current = id; setSelected(id);
      }
      if (!requestRef.current || requestRef.current.content !== text) requestRef.current = {content: text, id: crypto.randomUUID()};
      const request = requestRef.current;
      const pending: Message[] = [{id: `local-${request.id}`, role: "user", content: text, status: "completed", model: null, tokens: 0, created_at: new Date().toISOString()}, {id: `pending-${request.id}`, role: "assistant", content: "", status: "pending", model: mode, tokens: 0, created_at: new Date().toISOString()}];
      setContent("");
      // Show persisted history plus an optimistic turn. The response replaces it with DB records.
      setMessages(prev => [...prev.filter(m => !m.id.startsWith("pending-")), ...pending]);
      streamingRef.current = true;
      let finalHistory: History | null = null;
      const received = new Map<string, string>();
      const playback = createTextPlayback((messageId, chunk) => {
        if (selectedRef.current !== id) return;
        setMessages(prev => prev.map(m => m.id === messageId ? {...m, content: m.content + chunk} : m));
      });
      playbackRef.current = playback;
      await streamApi(`/conversations/${id}/messages?stream=true`, {content: text, client_id: request.id}, (event, data) => {
        if (selectedRef.current !== id) return;
        if (event === "delta") {
          const delta = data as {id: string; text: string};
          received.set(delta.id, (received.get(delta.id) || "") + delta.text);
          playback.enqueue(delta.id, delta.text);
        } else if (event === "start" || event === "done" || event === "error") {
          const history = data as History & {message?: string};
          if (event === "start") setMessages(history.messages);
          else {
            finalHistory = history;
            // Final snapshots may include an error suffix not present in delta events.
            for (const m of history.messages) {
              const text = received.get(m.id);
              if (text !== undefined && m.content.startsWith(text)) playback.enqueue(m.id, m.content.slice(text.length));
            }
          }
          if (event === "error") setError(history.message || "生成失败");
        }
      });
      // Do not replace the animated text with the full response at network completion.
      await playback.drain();
      if (selectedRef.current === id && finalHistory) setMessages((finalHistory as History).messages);
      playback.cancel();
      playbackRef.current = null;
      requestRef.current = null;
      await loadList();
    } catch(e) {
      await playbackRef.current?.drain();
      playbackRef.current?.cancel(); playbackRef.current = null;
      setError(e instanceof Error ? e.message : "回复失败"); setContent(text);
      if (id) {try {const history = await api<History>(`/conversations/${id}`); if (selectedRef.current === id) setMessages(history.messages);} catch {}}
    } finally {streamingRef.current = false; setBusy(false);}
  }

  const pending = messages.some(m => m.status === "pending");
  return <div className="chat-screen"><aside className="chat-sidebar"><div className="chat-sidebar-title"><MessageSquare size={19}/><strong>你的会话</strong></div><button className="new-project" disabled={busy} onClick={newConversation}><Plus size={16}/> 新增会话</button><div className="nav-label">会话历史 <span>{conversations.length}</span></div><div className="conversation-list">{conversations.map(c => <button key={c.id} className={c.id === selected ? "active" : ""} disabled={busy} onClick={() => setSelected(c.id)}><MessageSquare size={14}/><span>{c.title}<small>{new Date(c.updated_at).toLocaleDateString("zh-CN")}</small></span></button>)}{!conversations.length && <p className="nav-empty">开始提问，或创建一个空白会话。</p>}</div><div className="chat-sidebar-footer"><div className="avatar">{user.display_name.slice(0, 1)}</div><div>{user.display_name}<small>记录仅在你的空间中显示</small></div></div></aside><main className="chat-main"><div className="chat-titlebar"><span>{conversations.find(c => c.id === selected)?.title || "新会话"}</span><span className="mode-badge"><i/>{mode === "demo" ? "演示回复" : "模型会话"}</span></div>{error && <div className="error-banner" role="alert">{error}</div>}<div className="chat-transcript">{loading && <div className="chat-loading"><Loader2 className="spin" size={19}/></div>}{!loading && !messages.length && <div className="chat-welcome"><div className="chat-welcome-icon"><Sparkles size={29}/></div><p className="eyebrow">A SPACE FOR YOUR IDEAS</p><h1>今天，想聊些什么？</h1><p>从一个想法开始。当前会话会保留上下文，<br/>复杂问题可以切换到深度研究。</p><div className="chat-suggestions">{["帮我梳理一个研究问题", "解释 Agent 的工作流程", "给我一个竞品分析框架"].map(q => <button key={q} onClick={() => setContent(q)}>{q}<ArrowUp size={13}/></button>)}</div></div>}{messages.map(m => <div key={m.id} className={`message ${m.role} ${m.status}`}><div className="message-avatar">{m.role === "assistant" ? <Layers size={16}/> : <UserIcon size={16}/>}</div><div className="message-body"><div className="message-author">{m.role === "user" ? user.display_name : "Research Assistant"}<small>{m.role === "assistant" && m.model ? m.model === "demo" ? "模拟回复" : m.model : ""}</small></div>{m.status === "pending" && !m.content ? <div className="message-thinking"><Loader2 size={14} className="spin"/> 正在生成回复…</div> : <div className="message-text">{m.content}{m.status === "pending" && <span className="stream-cursor" aria-label="正在输出"/>}</div>}{m.status === "failed" && <small className="message-failed">生成失败 · 记录已保存</small>}</div></div>)}<div ref={scrollRef}/></div><div className="chat-composer-area">{mode === "demo" && <div className="demo-note">演示模式未调用模型；配置模型服务后可使用真实多轮问答。</div>}<div className="composer"><textarea aria-label="会话消息" placeholder="发送消息，继续这个会话…" maxLength={10000} value={content} onChange={e => setContent(e.target.value)} onKeyDown={e => {if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {e.preventDefault(); send();}}}/><div className="composer-bottom"><span className="chat-context-note"><Check size={12}/> 当前会话上下文已保存</span><button className="send-button" aria-label="发送消息" disabled={busy || pending || !content.trim()} onClick={send}>{busy ? <Loader2 size={17} className="spin"/> : <ArrowUp size={18}/>}</button></div></div><p className="composer-hint">Enter 发送 · Shift + Enter 换行 · 管理员可审阅记录</p></div></main></div>;
}
