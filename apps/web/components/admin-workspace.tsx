"use client";
import { useEffect, useState } from "react";
import { Activity, ChevronLeft, ChevronRight, FileText, Loader2, MessageSquare, Search, ShieldCheck, Users } from "lucide-react";
import { api } from "./api";

type Account = {id: string; username: string; display_name: string; role: string; conversations_count: number; runs_count: number};
type RecordItem = {id: string; owner_id: string; username: string; display_name: string; title: string; kind: string; updated_at: string; status: string};
type Detail = {kind: string; record: {title?: string; question?: string; username: string; display_name: string; status?: string; artifact?: {summary: string; claims: {subject: string; dimension: string; text: string; evidence_ids: string[]}[]} | null; plan?: {subjects: string[]; dimensions: string[]}; error?: string}; messages?: {id: string; role: string; content: string; status: string; model: string | null}[]; evidence?: {id: string; title: string; quote: string}[]};
type Audit = {id: string; username: string; action: string; target_id: string; created_at: string};

export default function AdminWorkspace() {
  const [overview, setOverview] = useState<Record<string, number>>({});
  const [users, setUsers] = useState<Account[]>([]);
  const [userId, setUserId] = useState("");
  const [records, setRecords] = useState<RecordItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [userOffset, setUserOffset] = useState(0);
  const [userTotal, setUserTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [audit, setAudit] = useState<Audit[] | null>(null);
  const [selectedRecord, setSelectedRecord] = useState("");

  useEffect(() => {
    let active = true;
    Promise.all([api<Record<string, number>>("/admin/overview"), api<{items: Account[]; total: number}>(`/admin/users?offset=${userOffset}`)]).then(([o, u]) => {if (active) {setOverview(o); setUsers(u.items); setUserTotal(u.total);}}).catch(e => {if (active) setError(e.message);});
    return () => {active = false;};
  }, [userOffset]);

  useEffect(() => {
    let active = true; setLoading(true); setDetail(null); setSelectedRecord("");
    const params = new URLSearchParams({q: filter, offset: String(offset), ...(userId ? {user_id: userId} : {})});
    api<{items: RecordItem[]; total: number}>(`/admin/records?${params}`).then(result => {if (active) {setRecords(result.items); setTotal(result.total);}}).catch(e => {if (active) setError(e.message);}).finally(() => {if (active) setLoading(false);});
    return () => {active = false;};
  }, [userId, offset, filter]);

  useEffect(() => {
    if (!selectedRecord) {setDetail(null); return;}
    let active = true; setDetailLoading(true); setDetail(null);
    api<Detail>(`/admin/records/${selectedRecord}`).then(d => {if (active) setDetail(d);}).catch(e => {if (active) setError(e.message);}).finally(() => {if (active) setDetailLoading(false);});
    return () => {active = false;};
  }, [selectedRecord]);

  async function showAudit() {
    try {setAudit(await api<Audit[]>("/admin/audit"));} catch(e) {setError(e instanceof Error ? e.message : "读取失败");}
  }

  return <main className="admin-screen"><div className="admin-heading"><div><p className="eyebrow">ADMIN CONSOLE</p><h1>账号与记录</h1><p>按账号审阅普通会话与深度研究。所有访问受管理员权限保护。</p></div><button className="secondary" onClick={showAudit}><Activity size={14}/> 访问审计</button></div>{error && <div className="inline-warning" role="alert">{error}</div>}<div className="admin-stats">{[{key: "users", label: "注册账号", icon: <Users size={18}/>}, {key: "conversations", label: "普通会话", icon: <MessageSquare size={18}/>}, {key: "messages", label: "会话消息", icon: <Activity size={18}/>}, {key: "research_runs", label: "研究任务", icon: <FileText size={18}/>}].map(s => <div key={s.key}>{s.icon}<strong>{overview[s.key] ?? "—"}</strong><span>{s.label}</span></div>)}</div><div className="admin-layout"><aside className="admin-accounts"><div className="admin-list-title">账号筛选 <span>{userTotal}</span></div><button className={!userId ? "active" : ""} onClick={() => {setUserId(""); setOffset(0);}}>全部账号</button>{users.map(u => <button key={u.id} className={u.id === userId ? "active" : ""} onClick={() => {setUserId(u.id); setOffset(0);}}><div className="avatar">{u.display_name.slice(0, 1)}</div><div><strong>{u.display_name}{u.role === "admin" && <ShieldCheck size={11}/>}</strong><small>@{u.username}</small><span>{u.conversations_count} 会话 · {u.runs_count} 研究</span></div></button>)}<div className="admin-pagination"><button aria-label="上一页账号" disabled={userOffset === 0} onClick={() => setUserOffset(n => Math.max(0, n - 50))}><ChevronLeft size={15}/></button><small>{userOffset + 1}–{Math.min(userOffset + 50, userTotal)}</small><button aria-label="下一页账号" disabled={userOffset + 50 >= userTotal} onClick={() => setUserOffset(n => n + 50)}><ChevronRight size={15}/></button></div></aside><section className="admin-records"><form className="admin-search" onSubmit={e => {e.preventDefault(); setFilter(query); setOffset(0);}}><Search size={15}/><input aria-label="搜索记录" placeholder="搜索会话标题或研究问题" value={query} onChange={e => setQuery(e.target.value)}/><button className="secondary small">搜索</button></form><div className="admin-record-list">{loading ? <Loader2 className="spin" size={18}/> : records.map(r => <button key={`${r.kind}/${r.id}`} className={selectedRecord === `${r.kind}/${r.id}` ? "active" : ""} onClick={() => {setSelectedRecord(`${r.kind}/${r.id}`); setAudit(null);}}><span className="record-kind">{r.kind === "chat" ? <MessageSquare size={16}/> : <FileText size={16}/>}</span><div><strong>{r.title}</strong><small>@{r.username} · {r.kind === "chat" ? "普通会话" : "深度研究"} · {new Date(r.updated_at).toLocaleString("zh-CN")}</small></div><ChevronRight size={14}/></button>)}{!loading && !records.length && <p className="admin-empty">该筛选下还没有记录。</p>}</div><div className="admin-pagination"><span>共 {total} 条记录</span><button aria-label="上一页记录" disabled={offset === 0} onClick={() => setOffset(n => Math.max(0, n - 50))}><ChevronLeft size={15}/></button><button aria-label="下一页记录" disabled={offset + 50 >= total} onClick={() => setOffset(n => n + 50)}><ChevronRight size={15}/></button></div></section><section className="admin-detail">{audit ? <><h2>管理员访问审计</h2><p className="footnote">记录管理员查看列表与具体内容的操作。</p>{audit.map(a => <div className="audit-item" key={a.id}><strong>{a.username} · {a.action}</strong><code>{a.target_id || "全部账号"}</code><small>{new Date(a.created_at).toLocaleString("zh-CN")}</small></div>)}</> : detailLoading ? <Loader2 className="spin" size={20}/> : detail ? <><div className="admin-detail-meta"><span>{detail.kind === "chat" ? "普通会话" : "深度研究"}</span><span>只读审阅</span></div><h2>{detail.record.title || detail.record.question}</h2><p className="admin-owner">{detail.record.display_name} · @{detail.record.username}</p>{detail.messages?.map(m => <div key={m.id} className={`admin-message ${m.role}`}><strong>{m.role === "user" ? "用户" : "助手"}<span>{m.model === "demo" ? "模拟回复" : m.model}</span></strong><p>{m.content || (m.status === "pending" ? "生成中…" : "空消息")}</p></div>)}{detail.kind === "research" && <><div className="admin-message"><strong>研究状态</strong><p>{detail.record.status}</p>{detail.record.plan && <p>对象：{detail.record.plan.subjects?.join("、")}<br/>维度：{detail.record.plan.dimensions?.join("、")}</p>}</div>{detail.record.artifact ? <><p className="admin-summary">{detail.record.artifact.summary}</p>{detail.record.artifact.claims.map((c, i) => <div className="admin-message" key={i}><strong>{c.subject} · {c.dimension}</strong><p>{c.text}</p><small>{c.evidence_ids.length} 条关联证据</small></div>)}</> : <p className="admin-empty">尚未交付研究成果。</p>}{detail.evidence?.map(e => <details className="admin-evidence" key={e.id}><summary>{e.title}</summary><p>{e.quote}</p><code>{e.id}</code></details>)}</>}</> : <div className="admin-empty-detail"><ShieldCheck size={32}/><h3>选择一条记录</h3><p>会话消息、研究计划和原文证据<br/>将在这里以只读方式展示。</p></div>}</section></div></main>;
}
