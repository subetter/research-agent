"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight, Cpu, Wrench } from "lucide-react";
import { api } from "./api";

export type ExecRecord = {
  id: string;
  kind: "llm" | "tool" | string;
  stage?: string | null;
  subject?: string | null;
  name?: string | null;
  role?: string | null;
  status?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  latency_ms?: number | null;
  tokens_in?: number;
  tokens_out?: number;
  tokens_total?: number;
  truncated?: boolean;
  preview?: Record<string, unknown>;
  payload?: Record<string, unknown>;
};

const STAGE_LABEL: Record<string, string> = {
  plan: "规划",
  approve: "计划确认",
  research: "检索/阅读",
  gap_check: "缺口与冲突检查",
  supplement: "补充研究",
  synthesize: "综合",
  render: "校验",
  report: "生成报告",
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function pretty(value: unknown) {
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

function CollapsedJson({value, label}: {value: unknown; label: string}) {
  const text = typeof value === "string" ? value : pretty(value);
  const long = text.length > 500;
  const [open, setOpen] = useState(!long);
  return <div className="exec-json">
    <div className="exec-json-top"><span>{label}</span>{long && <button type="button" onClick={() => setOpen(v => !v)}>{open ? "收起" : `展开全文（${text.length} 字）`}</button>}</div>
    <pre>{open ? text : `${text.slice(0, 420)}…`}</pre>
  </div>;
}

function pageFromLocator(locator?: unknown) {
  const match = String(locator || "").match(/page:(\d+)/);
  return match ? Number(match[1]) : null;
}

function MessageList({messages}: {messages: unknown[]}) {
  if (!messages.length) return <p className="fold-empty">没有保存请求消息</p>;
  return <div className="exec-messages">
    {messages.map((item, index) => {
      const message = asRecord(item);
      const role = String(message.role || "message");
      return <article key={index} className={`exec-msg ${role}`}>
        <strong>{role === "system" ? "system" : role === "user" ? "user" : role === "assistant" ? "assistant" : role === "tool" ? "tool" : role}</strong>
        {message.content != null && message.content !== "" && <CollapsedJson label="content" value={message.content}/>}
        {!!message.tool_calls && <CollapsedJson label="tool_calls" value={message.tool_calls}/>}
        {message.tool_call_id != null && <p className="exec-tool-id">tool_call_id {String(message.tool_call_id)}</p>}
      </article>;
    })}
  </div>;
}

function resultItems(result: unknown): Record<string, unknown>[] {
  if (Array.isArray(result)) return result.map(asRecord);
  const body = asRecord(result);
  for (const key of ["results", "evidence", "hits", "chunks"]) {
    if (Array.isArray(body[key])) return (body[key] as unknown[]).map(asRecord);
  }
  return [];
}

function ToolHits({name, result}: {name?: string | null; result: unknown}) {
  const items = resultItems(result);
  if (!items.length) return <CollapsedJson label="工具结果" value={result}/>;
  return <div className="tool-hits">
    {items.map((item, index) => {
      const title = String(item.title || item.id || `结果 ${index + 1}`);
      const url = String(item.url || "");
      const locator = String(item.locator || "");
      const page = item.page != null && item.page !== "" ? Number(item.page) : pageFromLocator(locator);
      const excerpt = String(item.quote || item.snippet || item.excerpt || item.content || "");
      return <article key={index} className="tool-hit">
        <strong>{title}</strong>
        {url && (url.startsWith("http") ? <a href={url} target="_blank" rel="noopener noreferrer">{url}</a> : <span className="tool-hit-url">{url}</span>)}
        <div className="tool-hit-meta">
          {page != null && !Number.isNaN(page) && <em>第 {page} 页</em>}
          {locator && <em>{locator}</em>}
          {name === "search_project" && !locator && page == null && <em>项目资料</em>}
        </div>
        {excerpt && <CollapsedJson label={name === "read_source" ? "正文摘录" : "摘要"} value={excerpt}/>}
      </article>;
    })}
  </div>;
}

function LlmBody({record}: {record: ExecRecord}) {
  const payload = asRecord(record.payload);
  const preview = asRecord(record.preview);
  const request = asRecord(payload.request || preview.request);
  const response = asRecord(payload.response || preview.response);
  const messages = (request.messages || preview.messages || []) as unknown[];
  const tools = request.tools || preview.tools;
  const params = request.params || preview.params;
  const error = payload.error || preview.error;
  return <>
    {error != null && error !== "" && <p className="exec-error">错误：{String(error)}</p>}
    <div className="exec-block"><span className="exec-block-label">请求消息</span><MessageList messages={Array.isArray(messages) ? messages : []}/></div>
    {!!tools && <CollapsedJson label="提供的工具 schema" value={tools}/>}
    {!!params && <CollapsedJson label="调用参数（temperature / JSON mode 等）" value={params}/>}
    <CollapsedJson label="模型回复" value={{content: response.content, tool_calls: response.tool_calls, finish_reason: response.finish_reason, parsed: response.parsed}}/>
    {record.truncated && <p className="truncate-note">已截断 · 已加载完整可用正文</p>}
  </>;
}

function ToolBody({record}: {record: ExecRecord}) {
  const payload = asRecord(record.payload);
  const preview = asRecord(record.preview);
  const args = payload.arguments || preview.arguments;
  const result = payload.result || preview.result;
  const error = payload.error || preview.error;
  const retry = payload.retry || preview.retry || payload.attempt || preview.attempt;
  return <>
    {error != null && error !== "" && <p className="exec-error">错误：{String(error)}</p>}
    {retry != null && retry !== "" && <p className="exec-retry">重试 / 轮次：{String(retry)}</p>}
    {record.status === "failed" && !error && <p className="exec-error">这次工具调用失败，可查看参数与结果。</p>}
    <CollapsedJson label="工具参数" value={args}/>
    <div className="exec-block"><span className="exec-block-label">工具结果</span><ToolHits name={record.name} result={result}/></div>
    {record.truncated && <p className="truncate-note">已截断 · 已加载完整可用正文</p>}
  </>;
}

function RecordCard({record, runId, defaultOpen=false}: {record: ExecRecord; runId: string; defaultOpen?: boolean}) {
  const [open, setOpen] = useState(defaultOpen);
  const [full, setFull] = useState<ExecRecord>(record);
  const [loading, setLoading] = useState(false);
  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && record.truncated && !full.payload) {
      setLoading(true);
      try { setFull(await api<ExecRecord>(`/runs/${runId}/records/${record.id}`)); }
      catch { setFull(record); }
      finally { setLoading(false); }
    }
  }
  const view = full.payload ? full : record;
  const when = record.started_at ? new Date(record.started_at).toLocaleTimeString("zh-CN", {hour12: false}) : "";
  return <article className={`exec-item ${record.kind} ${record.status || ""}`}>
    <button type="button" className="exec-item-head" onClick={toggle} aria-expanded={open}>
      {open ? <ChevronDown size={14}/> : <ChevronRight size={14}/>}
      <span className="exec-kind">{record.kind === "llm" ? <Cpu size={13}/> : <Wrench size={13}/>}{record.kind === "llm" ? "模型调用" : "工具调用"}</span>
      <strong>{record.kind === "llm" ? (record.role || "研究") : (record.name || "工具")}</strong>
      <span className="exec-meta">
        {record.name && record.kind === "llm" && <em>{record.name}</em>}
        {record.subject && <em>{record.subject}</em>}
        {when && <em>{when}</em>}
        {record.latency_ms != null && <em>{record.latency_ms} ms</em>}
        {record.kind === "llm" && <em>入 {record.tokens_in || 0} / 出 {record.tokens_out || 0}</em>}
        <em className={`exec-status ${record.status}`}>{record.status === "succeeded" ? "成功" : record.status === "failed" ? "失败" : record.status === "running" ? "进行中" : record.status}</em>
        {record.truncated && <em>已截断</em>}
      </span>
    </button>
    {open && <div className="exec-item-body">{loading ? <p>正在加载完整记录…</p> : record.kind === "llm" ? <LlmBody record={view}/> : <ToolBody record={view}/>}</div>}
  </article>;
}

export default function ExecutionLog({records, runId, focusStage}: {records: ExecRecord[]; runId: string; focusStage?: string | null}) {
  const groups = useMemo(() => {
    const order: string[] = [];
    const map = new Map<string, ExecRecord[]>();
    for (const record of records) {
      const key = record.stage || "other";
      if (!map.has(key)) { map.set(key, []); order.push(key); }
      map.get(key)!.push(record);
    }
    return order.map(id => ({id, label: STAGE_LABEL[id] || id, items: map.get(id) || []}));
  }, [records]);

  if (!records.length) {
    return <div className="pending-output"><h3>执行记录将随研究实时出现</h3><p>每次模型调用与工具结果都会保存在这里，刷新后仍可查看。</p></div>;
  }
  return <div className="exec-log">
    {groups.map(group => (
      <section key={group.id} id={`exec-stage-${group.id}`} className={`exec-group${focusStage === group.id ? " focused" : ""}`}>
        <h3>{group.label}<span>{group.items.length}</span></h3>
        {group.items.map((record, index) => <RecordCard key={record.id} record={record} runId={runId} defaultOpen={focusStage === group.id && index < 2}/>)}
      </section>
    ))}
  </div>;
}
