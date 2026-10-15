"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUpRight, ArrowUp, BookOpen, ChevronRight, Compass, Copy, Download, FileText, FlaskConical, Globe, Layers, Loader2, Pause, Play, Plus, RefreshCw, Search, ShieldCheck, Square, Upload, X, Settings2, Scale, Clock } from "lucide-react";

import { api } from "./api";
import ResearchProgress, { Progress } from "./research-progress";
import MarkdownPreview from "./markdown-preview";
import ExecutionLog, { ExecRecord } from "./execution-log";
import { ReportDocument, WebsitePreview } from "./report-document";

type Project = {id: string; name: string; background: string; runs_count: number};
type SubQuestion = {id: string; question: string; parent_id?: string | null};
type Plan = {goal: string; mode?: "grid" | "general"; subjects: string[]; dimensions: string[]; questions: string[]; subquestions?: SubQuestion[]; max_search_calls: number; max_gap_rounds: number; as_of?: string | null; max_evidence_age_days?: number; require_freshness?: boolean; waiting?: string; clarify_question?: string; request?: {subjects?: string[]; dimensions?: string[]; mode?: string}; skill_name?: string; skill_version?: string; regions?: string[]};
type Claim = {id: string; subject: string; dimension: string; text: string; evidence_ids: string[]; conflict_evidence_ids?: string[]; kind: string; verification: string; subquestion_id?: string};
type Evidence = {id: string; title: string; url: string; quote: string; locator: string; source_type: string; fetched_at: string; published_at?: string | null; freshness?: "fresh" | "stale" | "unknown"; freshness_label?: string; dimension?: string | null; citation_role?: string};
type CoverageCell = {subject: string; dimension: string; evidence_ids: string[]; covered: boolean; context_quote_count?: number; subquestion_id?: string; question?: string; parent_id?: string | null};
type Coverage = {mode?: string; subjects: string[]; dimensions: string[]; cells: CoverageCell[]; covered: number; total: number; gaps: {subject: string; dimension: string}[]; subquestions?: SubQuestion[]};
type ConflictPosition = {label: string; text: string; evidence_ids: string[]; freshness?: string; freshness_label?: string | null};
type Conflict = {id: string; subject: string; dimension: string; positions: ConflictPosition[]};
type ArtifactMetrics = {claims: number; evidence: number; unknown: number; with_references: number; conflicts?: number; stale_evidence?: number; undated_evidence?: number; fresh_evidence?: number};
type Artifact = {title: string; summary: string; claims: Claim[]; conflicts?: Conflict[]; verification_note: string; metrics: ArtifactMetrics; markdown?: string; coverage?: Coverage; outline?: {id: string; title: string}[]; sections?: {id: string; title: string; body: string; evidence_ids?: string[]}[]; open_questions?: string[]; sources?: {id: string; title: string; url: string}[]; research_mode?: string};
type Run = {budget?: {model_limit: number; report_reserved: number; search_limit: number}; coverage?: Coverage | null; waiting?: string | null; id: string; question: string; status: string; revision: number; mode: string; plan: Plan; error: string | null; created_at?: string; artifact: Artifact | null; claim_evidence?: {claim_id: string; evidence_id: string; relation: string}[]; usage?: {kind: string; calls: number; tokens: number}[]; progress?: Progress; records?: ExecRecord[]; markdown?: string | null; trace_url?: string | null};
type Event = {seq: number; type: string; payload: Record<string, unknown>; created_at: string};
type Doc = {id: string; name: string; characters: number; status?: string; error?: string | null; page_count?: number | null; source_type?: string; citable?: boolean};
type RetrievalHealth = {provider?: string; embedder?: string; reranker?: string; embedding_dim?: number | null; fallback?: boolean; index_version?: string};
type Health = {mode: string; live_ready: boolean; retrieval?: RetrievalHealth};

function pageFromLocator(locator?: string) {
  const match = (locator || "").match(/page:(\d+)/);
  return match ? Number(match[1]) : null;
}
const terminal = new Set(["completed", "partial", "cancelled", "failed"]);
const statusText: Record<string, string> = {planning: "正在规划", waiting_input: "等待确认", running: "研究进行中", pause_requested: "正在暂停", paused: "已暂停", cancel_requested: "正在取消", cancelled: "已取消", completed: "已完成", partial: "部分交付", failed: "执行失败"};
const eventText: Record<string, string> = {"budget.reached": "研究预算已达上限，转入报告阶段", "run.created": "创建研究", "plan.started": "拆解研究目标", "skill.selected": "选用研究方法", "plan.proposed": "研究计划已生成", "run.waiting_input": "等待你确认", "run.running": "研究已启动", "task.started": "研究员开始调查", "task.finished": "研究员完成调查", "task.reused": "复用已有研究证据", "evidence.created": "保存原文证据", "tool.started": "调用研究工具", "tool.finished": "工具调用完成", "coverage.checked": "检查覆盖、冲突与时效", "research.followup": "跟进仍未知的问题", "report.started": "综合分析与写作", "verify.started": "对照原文核验引用", "verify.finished": "引用核验完成", "model.finished": "模型调用完成", "run.completed": "成果已交付", "run.partial": "交付已有研究结果", "run.paused": "已保存进度", "task.failed": "子任务失败", "run.failed": "任务执行失败"};
const skillTitle: Record<string, string> = {competitor_analysis: "竞品分析", industry_landscape: "行业格局", research_report: "综合研究报告"};

function FreshnessBadge({status, label}: {status?: string; label?: string | null}) {
  const kind = status === "fresh" || status === "stale" ? status : "unknown";
  const text = label || (kind === "fresh" ? "较新" : kind === "stale" ? "过时" : "未标注日期");
  return <span className={`freshness-badge ${kind}`}>{text}</span>;
}

export default function Workbench() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [runs, setRuns] = useState<Run[]>([]);
  const [runId, setRunId] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [indexNote, setIndexNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [question, setQuestion] = useState("");
  const [subjects, setSubjects] = useState("ChatGPT, Gemini, Claude");
  const [dimensions, setDimensions] = useState("产品形态, 研究流程, 交付方式");
  const [events, setEvents] = useState<Event[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence | null>(null);
  const [focusStage, setFocusStage] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [nowTick, setNowTick] = useState(0);
  const [draftAsOf, setDraftAsOf] = useState("");
  const [draftRequireFreshness, setDraftRequireFreshness] = useState(false);
  const [draftMaxAge, setDraftMaxAge] = useState("365");
  const [docs, setDocs] = useState<Doc[]>([]);
  const [showProject, setShowProject] = useState(false);
  const [newName, setNewName] = useState("");
  const [background, setBackground] = useState("");
  const [showScope, setShowScope] = useState(false);
  const [showDocs, setShowDocs] = useState(false);
  const [streamEpoch, setStreamEpoch] = useState(0);
  const [draftSubjects, setDraftSubjects] = useState("");
  const [draftDimensions, setDraftDimensions] = useState("");
  const [draftMode, setDraftMode] = useState<"grid" | "general">("grid");
  const [draftSubquestions, setDraftSubquestions] = useState<SubQuestion[]>([]);
  const [docCitable, setDocCitable] = useState(false);
  const [showWebsite, setShowWebsite] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const selectedProject = projects.find(p => p.id === projectId);
  const selectedRunRef = useRef(runId);
  selectedRunRef.current = runId;
  const selectedProjectRef = useRef(projectId);
  selectedProjectRef.current = projectId;
  const split = (text: string) => text.split(/[,，\n]/).map(s => s.trim()).filter(Boolean);

  const loadProjects = useCallback(async () => {
    const items = await api<Project[]>("/projects"); setProjects(items); return items;
  }, []);

  useEffect(() => {
    Promise.all([loadProjects(), api<Health>("/health")]).then(([items, h]) => {
      setHealth(h); if (items.length) setProjectId(items[0].id);
    }).catch(e => setError(e.message));
  }, [loadProjects]);

  useEffect(() => {
    setRunId(""); setRun(null); setRuns([]); setEvidence([]); setEvents([]); setDocs([]); setSelectedEvidence(null);
    if (!projectId) return;
    let cancelled = false;
    Promise.all([api<Run[]>(`/projects/${projectId}/runs`), api<Doc[]>(`/projects/${projectId}/documents`)]).then(([items, documents]) => {
      if (!cancelled) {setRuns(items); setDocs(documents); if (items.length) setRunId(items[0].id);}
    }).catch(e => {if (!cancelled) setError(e.message);});
    return () => {cancelled = true;};
  }, [projectId]);

  useEffect(() => {
    if (!projectId || !docs.some(doc => doc.status === "processing")) return;
    const timer = setInterval(() => {
      api<Doc[]>(`/projects/${projectId}/documents`).then(items => {
        if (selectedProjectRef.current === projectId) setDocs(items);
      }).catch(() => {});
    }, 1200);
    return () => clearInterval(timer);
  }, [projectId, docs]);

  const refreshRun = useCallback(async (id: string) => {
    const [current, sources] = await Promise.all([api<Run>(`/runs/${id}`), api<Evidence[]>(`/runs/${id}/evidence`)]);
    if (selectedRunRef.current !== id) return;
    setRun(current); setEvidence(sources);
    setRuns(items => items.map(r => r.id === id ? current : r));
    return current;
  }, []);

  useEffect(() => {
    setRun(null); setEvidence([]); setEvents([]); setSelectedEvidence(null);
    if (!runId) return;
    let stopped = false;
    let source: EventSource | null = null;
    refreshRun(runId).then(current => {
      if (!current || stopped) return;
      if (terminal.has(current.status)) {
        api<Event[]>(`/runs/${runId}/trace`).then(list => {if (!stopped) setEvents(list);}).catch(e => {if (!stopped) setError(e.message);});
      } else {
        source = new EventSource(`/api/runs/${runId}/events`);
        source.onmessage = event => {
          const item = JSON.parse(event.data) as Event;
          setEvents(prev => prev.some(e => e.seq === item.seq) ? prev : [...prev, item]);
          refreshRun(runId).then(r => {if (r && terminal.has(r.status)) source?.close();}).catch(e => {if (!stopped) setError(e.message);});
        };
      }
    }).catch(e => {if (!stopped) setError(e.message);});
    const timer = setInterval(() => {refreshRun(runId).catch(() => {});}, 2000);
    return () => {stopped = true; source?.close(); clearInterval(timer);};
  }, [runId, refreshRun, streamEpoch]);

  useEffect(() => {
    if (run?.status !== "waiting_input") return;
    if (run.waiting === "clarify" || run.plan.waiting === "clarify") {
      setDraftSubjects((run.plan.request?.subjects || []).join(", "));
      setDraftDimensions((run.plan.request?.dimensions || []).join(", "));
      return;
    }
    setDraftSubjects((run.plan.subjects || []).join(", "));
    setDraftDimensions((run.plan.dimensions || []).join(", "));
    setDraftMode(run.plan.mode === "general" ? "general" : "grid");
    setDraftSubquestions(run.plan.subquestions || []);
    setDraftAsOf(run.plan.as_of || "");
    setDraftRequireFreshness(!!run.plan.require_freshness);
    setDraftMaxAge(String(run.plan.max_evidence_age_days || 365));
  }, [run?.id, run?.status, run?.waiting]);

  async function action(fn: () => Promise<void>) {
    setBusy(true); setError("");
    try {await fn();} catch (e) {setError(e instanceof Error ? e.message : "操作失败");} finally {setBusy(false);}
  }

  async function createProject() {
    await action(async () => {
      const p = await api<Project>("/projects", {method: "POST", body: JSON.stringify({name: newName, background})});
      await loadProjects(); setProjectId(p.id); setShowProject(false); setNewName(""); setBackground("");
    });
  }

  async function submitResearch() {
    if (question.trim().length < 5) {setError("请描述至少 5 个字的研究目标"); return;}
    await action(async () => {
      let id = projectId;
      if (!id) {
        const p = await api<Project>("/projects", {method: "POST", body: JSON.stringify({name: "我的第一个研究项目", background: ""})});
        id = p.id; await loadProjects();
      }
      const current = await api<Run>(`/projects/${id}/runs`, {method: "POST", body: JSON.stringify({question, subjects: split(subjects), dimensions: split(dimensions), parent_run_id: run?.artifact ? run.id : null})});
      // Let the project selection effect load the run list if a project was just created.
      if (projectId === id) {setRuns(prev => [current, ...prev]); setRunId(current.id);} else {setProjectId(id);}
      setQuestion(""); setFocusStage(null); setCopied(false);
    });
  }

  async function command(actionName: string) {
    if (!run) return;
    const currentId = run.id;
    await action(async () => {
      let latest = await api<Run>(`/runs/${currentId}`);
      if (actionName === "start") {
        if (latest.waiting === "clarify" || latest.plan.waiting === "clarify") {
          latest = await api<Run>(`/runs/${currentId}/clarify`, {method: "PUT", body: JSON.stringify({expected_revision: latest.revision, subjects: split(draftSubjects), dimensions: split(draftDimensions)})});
        } else {
          const nextPlan = {
            ...latest.plan,
            mode: draftMode,
            subjects: draftMode === "grid" ? split(draftSubjects) : (split(draftSubjects).length ? split(draftSubjects) : [latest.question.slice(0, 40)]),
            dimensions: draftMode === "grid" ? split(draftDimensions) : (split(draftDimensions).length ? split(draftDimensions) : ["要点"]),
            subquestions: draftMode === "general" ? draftSubquestions.filter(item => item.question.trim()) : (latest.plan.subquestions || []),
            as_of: draftAsOf || null,
            require_freshness: draftRequireFreshness,
            max_evidence_age_days: Number(draftMaxAge) || 365,
          };
          latest = await api<Run>(`/runs/${currentId}/plan`, {method: "PUT", body: JSON.stringify({expected_revision: latest.revision, plan: nextPlan})});
        }
      }
      const updated = await api<Run>(`/runs/${currentId}/commands`, {method: "POST", body: JSON.stringify({action: actionName, expected_revision: latest.revision})});
      if (selectedRunRef.current === currentId) setRun(updated);
      setStreamEpoch(value => value + 1);
    });
  }

  async function upload(file: File) {
    const id = projectId;
    await action(async () => {
      const data = new FormData(); data.append("file", file); if (docCitable) data.append("citable", "true");
      const response = await fetch(`/api/projects/${id}/documents`, {method: "POST", body: data});
      if (!response.ok) {const body = await response.json(); throw new Error(body.detail);}
      const items = await api<Doc[]>(`/projects/${id}/documents`);
      if (selectedProjectRef.current === id) setDocs(items);
    });
    if (fileInput.current) fileInput.current.value = "";
  }

  async function reindexLibrary() {
    const id = projectId;
    await action(async () => {
      const result = await api<{reindexed: number; embedder: string; embedding_dim?: number | null}>(`/projects/${id}/documents/reindex`, {method: "POST", body: JSON.stringify({force: true})});
      const items = await api<Doc[]>(`/projects/${id}/documents`);
      if (selectedProjectRef.current === id) {
        setDocs(items);
        setIndexNote(`已按 ${result.embedder}${result.embedding_dim ? ` · ${result.embedding_dim} 维` : ""} 重建 ${result.reindexed} 份资料。`);
      }
    });
  }

  useEffect(() => {
    if (!run || terminal.has(run.status)) return;
    const timer = setInterval(() => setNowTick(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [run?.id, run?.status]);

  const running = run && ["running", "planning", "pause_requested", "cancel_requested"].includes(run.status);
  const clarifying = run?.status === "waiting_input" && (run.waiting === "clarify" || run.plan.waiting === "clarify");
  const markdown = run?.markdown || run?.artifact?.markdown || "";
  const progress = run?.progress;
  const coverage = run?.coverage || run?.artifact?.coverage || null;
  const isGeneral = (run?.plan.mode || run?.artifact?.research_mode) === "general";
  function addSubquestion(parentId: string | null = null) {
    const ident = `sq_${Date.now().toString(36)}`;
    setDraftSubquestions(items => [...items, {id: ident, question: "", parent_id: parentId}]);
  }
  const elapsed = run?.created_at && !terminal.has(run.status)
    ? Math.max(0, Math.floor((Math.max(nowTick, Date.now()) - new Date(run.created_at).getTime()) / 1000))
    : (progress?.elapsed_seconds || 0);

  async function copyMarkdown() {
    if (!markdown) return;
    try {
      await navigator.clipboard.writeText(markdown);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    } catch {
      setError("复制失败，请改用下载");
    }
  }

  function selectStage(id: string) {
    setFocusStage(id);
    requestAnimationFrame(() => document.getElementById(`exec-stage-${id}`)?.scrollIntoView({behavior: "smooth", block: "start"}));
  }

  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><div className="brand-icon"><Layers size={21}/></div><div>Research<span>WORKBENCH</span></div></div>
      <button className="new-project" onClick={() => setShowProject(true)}><Plus size={16}/> 新建研究项目 <span>⌘</span></button>
      <div className="nav-label">工作空间</div>
      <button className="nav-item selected" onClick={() => setShowDocs(false)}><Compass size={17}/> 研究工作台</button>
      <button className="nav-item" disabled={!projectId} onClick={() => setShowDocs(v => !v)}><BookOpen size={17}/> 项目资料 <span>{docs.length}</span></button>
      <div className="nav-label project-label">我的项目 <span>{projects.length}</span></div>
      <div className="project-list">{projects.map(p => <button key={p.id} className={`project-item ${p.id === projectId ? "active" : ""}`} onClick={() => setProjectId(p.id)}><span className="project-dot"/><span>{p.name}</span><ChevronRight size={13}/></button>)}</div>
      <div className="nav-label">研究记录</div>
      <div className="run-list">{runs.map(r => <button key={r.id} className={r.id === runId ? "active" : ""} onClick={() => {setRunId(r.id); setShowDocs(false);}}><FileText size={14}/><span>{r.question}</span><i className={r.status}/></button>)}{!runs.length && <p className="nav-empty">你的研究会保存在这里</p>}</div>
      <div className="sidebar-bottom"><div className="avatar">R</div><div>个人研究空间<span>本地版本 · v0.1</span></div><ShieldCheck size={17}/></div>
    </aside>

    <main className="main">
      <header className="topbar"><div className="breadcrumb">工作空间 <ChevronRight size={13}/><strong>{selectedProject?.name || "研究工作台"}</strong></div><div className={`mode-badge ${health?.mode === "live" ? "live" : ""}`}><span/>{health ? health.mode === "live" ? "联网模式" : "演示模式" : "连接服务中"}</div></header>
      {error && <div className="error-banner" role="alert">{error}<button aria-label="关闭错误" onClick={() => setError("")}><X size={15}/></button></div>}
      <section className="main-content">
        {showDocs ? <><div className="section-heading"><div><p className="eyebrow">PROJECT LIBRARY</p><h1>项目资料</h1><p>上传报告或论文后，研究会用全文 + 向量混合检索引用具体页码。未标记为可引用事实的资料只能支撑分析。</p>{health?.retrieval && <p className="retrieval-meta">检索：{health.retrieval.embedder}{health.retrieval.embedding_dim ? ` · ${health.retrieval.embedding_dim} 维` : ""} · 重排 {health.retrieval.reranker}{health.retrieval.fallback ? " · 未配置密钥，已回退本地哈希" : ""}</p>}{indexNote && <p className="index-note">{indexNote}</p>}</div><div className="library-actions"><button className="secondary small" disabled={busy || !projectId || !docs.some(d => (d.status || "ready") === "ready")} onClick={reindexLibrary}><RefreshCw size={15}/> 重建索引</button><button className="primary small" disabled={busy || !projectId} onClick={() => fileInput.current?.click()}><Upload size={15}/> 上传资料</button></div></div><label className="citable-toggle"><input type="checkbox" checked={docCitable} onChange={e => setDocCitable(e.target.checked)}/> 新上传的资料可作为事实引用（默认仅分析）</label><div className="library-grid">{docs.map(d => <div className={`document-card ${d.status || "ready"}`} key={d.id}><FileText size={25}/><h3>{d.name}</h3><p>{d.status === "processing" ? "正在解析并建立索引…" : d.status === "failed" ? (d.error || "解析失败，未加入检索") : `${(d.characters || 0).toLocaleString()} 字符${d.page_count ? ` · ${d.page_count} 页` : ""} · ${d.citable ? "可作事实引用" : "仅作分析"}`}</p><span className={`doc-status ${d.status || "ready"}`}>{d.status === "processing" ? "处理中" : d.status === "failed" ? "失败" : "已就绪"}</span>{(d.status || "ready") === "ready" && <button className="secondary small" disabled={busy} onClick={() => action(async () => {const updated = await api<Doc>(`/projects/${projectId}/documents/${d.id}`, {method: "PATCH", body: JSON.stringify({citable: !d.citable})}); setDocs(items => items.map(item => item.id === d.id ? {...item, ...updated} : item));})}>{d.citable ? "改为仅分析" : "标为可引用事实"}</button>}</div>)}{!docs.length && <div className="empty-library"><BookOpen size={30}/><h3>为研究补充背景</h3><p>支持 UTF-8 TXT、Markdown、CSV 与 PDF，单个文件最大 5 MB。解析失败会明确标出，不会静默丢掉。</p><button className="secondary" onClick={() => fileInput.current?.click()}>选择文件</button></div>}</div><p className="footnote">仅检索状态为「已就绪」的资料。换嵌入模型或维度会重建索引，不会混用向量空间。无 SiliconFlow 密钥时回退 local-hash-v1 与词法重排。</p></> : <>
          {!run ? <div className="welcome"><div className="eyebrow"><span/> YOUR NEXT DISCOVERY</div><h1>让好问题，<br/><span>成为有依据的洞察。</span></h1><p className="welcome-copy">对照比较或开放深挖，都能写成报告或网站。<br/>规划、探索、验证，再交付。</p><div className="welcome-features"><span><Search size={15}/> 多源探索</span><span><ShieldCheck size={15}/> 证据溯源</span><span><Scale size={15}/> 冲突对照</span><span><Clock size={15}/> 证据时效</span></div><div className="sample-grid">{[{title: "理解产品格局", q: "调研 ChatGPT、Gemini 和 Claude 的深度研究产品形态，比较研究流程与交付方式。", icon: <Compass size={20}/>}, {title: "核对冲突与时效", q: "比较 ChatGPT 与 Gemini 的定价口径，标出互相冲突的来源，并检查证据是否过时。", subjects: "ChatGPT, Gemini", dimensions: "定价", icon: <Scale size={20}/>}, {title: "开放深挖一个主题", q: "智能体记忆系统如何工作，领域将往何处去", icon: <BookOpen size={20}/>}].map(s => <button key={s.title} className="sample-card" onClick={() => {setQuestion(s.q); if ("subjects" in s && s.subjects) setSubjects(s.subjects); if ("dimensions" in s && s.dimensions) {setDimensions(s.dimensions); setShowScope(true);}}}>{s.icon}<strong>{s.title}</strong><p>{s.q}</p><ArrowUpRight size={16}/></button>)}</div></div> : <div className="research-detail">
            <div className="section-heading"><div><p className="eyebrow">深度研究</p><h1>{run.question}</h1><div className="session-meta"><span className={`status ${run.status}`}>{running && <Loader2 size={12} className="spin"/>}{statusText[run.status]}</span><span>{run.mode === "demo" ? "模拟研究 · 不包含真实事实" : "联网研究"}</span>{run.plan.skill_name && <span>方法 {skillTitle[run.plan.skill_name] || run.plan.skill_name} v{run.plan.skill_version}</span>}{run.trace_url && <a className="trace-link" href={run.trace_url} target="_blank" rel="noopener noreferrer">查看轨迹</a>}<span>{run.id.slice(-8)}</span></div></div><div className="run-actions">{["running", "planning"].includes(run.status) && <button className="icon-button" aria-label="暂停研究" disabled={busy} onClick={() => command("pause")}><Pause size={16}/></button>}{run.status === "paused" && <button className="secondary" disabled={busy} onClick={() => command("resume")}><Play size={14}/> 恢复研究</button>}{run.status === "failed" && <button className="secondary" disabled={busy} onClick={() => command("retry")}>重试</button>}{!terminal.has(run.status) && <button className="icon-button" aria-label="取消研究" disabled={busy} onClick={() => command("cancel")}><Square size={14}/></button>}</div></div>
            {run.error && <div className="inline-warning" role="alert"><strong>本次研究未完成</strong><p>{run.error}</p><small>已保留 {evidence.length} 条证据。仍可下载已有的部分 Markdown。</small></div>}
            {progress && <ResearchProgress progress={{...progress, elapsed_seconds: elapsed}} onSelectStage={selectStage} selectedStage={focusStage}/>}
            {coverage && coverage.total > 0 && coverage.mode === "general" && <div className="coverage-panel"><div className="coverage-heading"><strong>子问题覆盖</strong><span>已覆盖 {coverage.covered} / {coverage.total} 个问题</span></div><ul className="question-tree">{(run.plan.subquestions || coverage.subquestions || []).filter(item => !item.parent_id).map(item => {const children = (run.plan.subquestions || coverage.subquestions || []).filter(child => child.parent_id === item.id); const cell = coverage.cells.find(c => c.subject === item.id || c.subquestion_id === item.id); return <li key={item.id} className={cell?.covered ? "covered" : "missing"}><strong>{item.question}</strong><span>{cell?.covered ? "有原文" : "调查中"}</span>{children.length > 0 && <ul>{children.map(child => {const nested = coverage.cells.find(c => c.subject === child.id || c.subquestion_id === child.id); return <li key={child.id} className={nested?.covered ? "covered" : "missing"}>{child.question}<span>{nested?.covered ? "有原文" : "调查中"}</span></li>;})}</ul>}</li>;})}</ul></div>}
            {coverage && coverage.total > 0 && coverage.mode !== "general" && <div className="coverage-panel"><div className="coverage-heading"><strong>覆盖矩阵</strong><span>已覆盖 {coverage.covered} / {coverage.total} 个对象×维度</span></div><div className="table-scroll"><table className="coverage-table"><thead><tr><th>对象</th>{coverage.dimensions.map(d => <th key={d}>{d}</th>)}</tr></thead><tbody>{coverage.subjects.map(s => <tr key={s}><th>{s}</th>{coverage.dimensions.map(d => {const cell = coverage.cells.find(c => c.subject === s && c.dimension === d); return <td key={d} className={cell?.covered ? "covered" : "missing"}>{cell?.covered ? "有原文" : "缺失"}</td>;})}</tr>)}</tbody></table></div></div>}
            {clarifying && <div className="plan-card waiting-card"><div className="waiting-banner">还看不出比较对象或维度</div><div className="plan-header"><div className="plan-icon"><Settings2 size={19}/></div><div><h3>先补全研究范围</h3><p>{run.plan.clarify_question || "问题里还看不出比较对象或维度。补全后才会生成计划。"}</p></div></div><label>研究对象<input value={draftSubjects} onChange={e => setDraftSubjects(e.target.value)} aria-label="澄清研究对象"/></label><label>比较维度<input value={draftDimensions} onChange={e => setDraftDimensions(e.target.value)} aria-label="澄清比较维度"/></label><div className="plan-footer"><span>只问这一次；时间与地区会写在计划卡上</span><button className="primary" disabled={busy || !split(draftSubjects).length || !split(draftDimensions).length} onClick={() => command("start")}><Play size={14}/> 生成研究计划</button></div></div>}
            {run.status === "waiting_input" && !clarifying && <div className="plan-card waiting-card"><div className="waiting-banner">需要你确认计划后才会开始检索</div><div className="plan-header"><div className="plan-icon"><Settings2 size={19}/></div><div><h3>确认研究方向</h3><p>对照题用对象×维度；开放主题拆成可编辑的子问题树。时效要求确认后，研究员才会并行探索。</p></div></div><div className="mode-switch" role="group" aria-label="研究模式"><button type="button" className={draftMode === "grid" ? "active" : ""} onClick={() => setDraftMode("grid")}>对照表</button><button type="button" className={draftMode === "general" ? "active" : ""} onClick={() => {setDraftMode("general"); if (!draftSubquestions.length) setDraftSubquestions(run.plan.subquestions || [{id: "sq_1", question: run.question, parent_id: null}]);}}>开放深挖</button></div>{draftMode === "grid" ? <><label>研究对象<input value={draftSubjects} onChange={e => setDraftSubjects(e.target.value)} aria-label="计划研究对象"/></label><label>比较维度<input value={draftDimensions} onChange={e => setDraftDimensions(e.target.value)} aria-label="计划比较维度"/></label></> : <div className="subq-editor"><div className="plan-skill">子问题树 · 可增删改，研究员会跟进未知点</div>{draftSubquestions.filter(item => !item.parent_id).map(item => <div key={item.id} className="subq-node"><input aria-label={`子问题 ${item.id}`} value={item.question} onChange={e => setDraftSubquestions(list => list.map(row => row.id === item.id ? {...row, question: e.target.value} : row))}/><button type="button" className="secondary small" onClick={() => addSubquestion(item.id)}>加子问</button><button type="button" className="secondary small" onClick={() => setDraftSubquestions(list => list.filter(row => row.id !== item.id && row.parent_id !== item.id))}>删除</button>{draftSubquestions.filter(child => child.parent_id === item.id).map(child => <div key={child.id} className="subq-child"><input aria-label={`子问题 ${child.id}`} value={child.question} onChange={e => setDraftSubquestions(list => list.map(row => row.id === child.id ? {...row, question: e.target.value} : row))}/><button type="button" className="secondary small" onClick={() => setDraftSubquestions(list => list.filter(row => row.id !== child.id))}>删除</button></div>)}</div>)}<button type="button" className="secondary small" onClick={() => addSubquestion(null)}>增加问题</button></div>}<div className="plan-freshness"><label>时效截止日<input type="date" value={draftAsOf} onChange={e => setDraftAsOf(e.target.value)} aria-label="计划时效截止日"/></label><label>最长有效期（天）<input type="number" min={1} max={3650} value={draftMaxAge} onChange={e => setDraftMaxAge(e.target.value)} aria-label="证据最长有效期"/></label><label className="plan-check"><input type="checkbox" checked={draftRequireFreshness} onChange={e => setDraftRequireFreshness(e.target.checked)}/> 关注证据时效：过时或未标注发布日期视为缺口</label></div>{run.plan.skill_name && <div className="plan-defaults"><div className="plan-skill">研究方法：{skillTitle[run.plan.skill_name] || run.plan.skill_name} · v{run.plan.skill_version}</div>默认范围：截至 {run.plan.as_of || "提交日"} · 地区 {(run.plan.regions || ["未限定"]).join("、")}</div>}<div className="plan-footer"><span>最多 {run.plan.max_search_calls} 次搜索 · {run.plan.max_gap_rounds} 轮补充研究</span><button className="primary" disabled={busy || (draftMode === "general" && !draftSubquestions.some(item => item.question.trim()))} onClick={() => command("start")}><Play size={14}/> 确认并开始研究</button></div></div>}
            {markdown && <section className="result-card" aria-label="研究报告">
              <div className="result-card-top">
                <div><p className="eyebrow">Markdown 成果</p><h2>{run.status === "completed" ? "研究报告已生成" : run.status === "partial" ? "部分研究报告" : "已保存的部分文稿"}</h2><p>预览与下载同一份 Markdown 文件。编号引用对应文末参考文献。</p></div>
                <div className="result-actions">
                  <button className="secondary small" onClick={copyMarkdown}><Copy size={14}/>{copied ? "已复制" : "复制"}</button>
                  <a className="primary small" href={`/api/runs/${run.id}/export?format=markdown`}><Download size={14}/> 下载 .md</a>
                  <a className="secondary small" href={`/api/runs/${run.id}/export?format=csv`}>导出 CSV</a>
                  {run.artifact?.sections?.length ? <button className="secondary small" onClick={() => setShowWebsite(v => !v)}><Globe size={14}/>{showWebsite ? "收起网站" : "网站导出"}</button> : null}
                </div>
              </div>
              {run.artifact?.sections?.length ? <ReportDocument artifact={run.artifact} evidence={evidence} onOpen={item => setSelectedEvidence(evidence.find(row => row.id === item.id) || item as Evidence)}/> : <MarkdownPreview markdown={markdown}/>}
              {showWebsite && run.artifact?.sections?.length ? <WebsitePreview runId={run.id}/> : null}
            </section>}
            {!markdown && !running && run.status !== "waiting_input" && <div className="pending-output"><FileText size={27}/><h3>还没有可下载的 Markdown</h3><p>研究完成后会在这里预览并提供下载。</p></div>}
            <section className="exec-panel">
              <div className="exec-panel-top"><h2>执行记录</h2><p>按阶段查看每次模型调用的请求结构，以及每次工具调用的参数与结果。</p></div>
              <ExecutionLog records={run.records || []} runId={run.id} focusStage={focusStage}/>
            </section>
            <div className="extra-folds">
              <details><summary>证据来源 {evidence.length}</summary><div className="source-list">{evidence.map((e, i) => <button key={e.id} className="source-card" onClick={() => setSelectedEvidence(e)}><span className="source-index">{String(i + 1).padStart(2, "0")}</span><div><h3>{e.title}<FreshnessBadge status={e.freshness} label={e.freshness_label}/></h3><p>{e.quote.slice(0, 110)}</p><small>{e.source_type === "demo" ? "模拟来源" : e.source_type === "web" ? "互联网来源" : "项目资料"}{pageFromLocator(e.locator) ? ` · 第 ${pageFromLocator(e.locator)} 页` : ""} · {e.locator}</small></div></button>)}{!evidence.length && <p className="fold-empty">还没有保存原文。</p>}</div></details>
              <details><summary>来源冲突 {run.artifact?.conflicts?.length || 0}</summary>{run.artifact?.conflicts?.length ? <div className="conflict-list">{run.artifact.conflicts.map(conflict => <article className="conflict-card" key={conflict.id}><header><Scale size={16}/><h3>{conflict.subject} · {conflict.dimension}</h3></header><div className="conflict-positions">{conflict.positions.map(position => <div key={position.label}><div className="conflict-pos-top"><strong>{position.label}</strong><FreshnessBadge status={position.freshness} label={position.freshness_label}/></div><p>{position.text}</p></div>)}</div></article>)}</div> : <p className="fold-empty">当前没有检测到来源冲突。</p>}</details>
              {!isGeneral && <details><summary>对比表</summary>{run.artifact ? <div className="comparison"><div className="table-scroll"><table><thead><tr><th>比较维度</th>{run.plan.subjects.map(s => <th key={s}>{s}</th>)}</tr></thead><tbody>{run.plan.dimensions.map(d => <tr key={d}><th>{d}</th>{run.plan.subjects.map(s => {const c = run.artifact?.claims.find(c => c.subject === s && c.dimension === d); return <td key={s}>{c?.text || "未确认"}</td>;})}</tr>)}</tbody></table></div></div> : <p className="fold-empty">报告生成后可查看对比表。</p>}</details>}
              {run.budget && <details><summary>研究预算</summary><div className="budget-panel"><div className="budget-grid">{[{kind: "model", label: "模型调用", limit: run.budget.model_limit}, {kind: "search", label: "联网搜索", limit: run.budget.search_limit}].map(item => {const used = (run.usage || []).filter(u => u.kind === item.kind).reduce((n, u) => n + u.calls, 0); return <div key={item.kind}><span>{item.label}</span><strong>{used} / {item.limit}</strong><progress value={Math.min(used, item.limit)} max={item.limit}/></div>;})}<div><span>原文证据</span><strong>{evidence.length} 条</strong></div></div></div></details>}
              <details><summary>原始事件轨迹 {events.length}</summary><div className="timeline">{events.map(e => <div className="timeline-item" key={e.seq}><span className="timeline-dot"/><div><strong>{eventText[e.type] || statusText[e.type.replace("run.", "")] || e.type}</strong><p>{String(e.payload.subject || e.payload.title || e.payload.tool || e.payload.operation || e.payload.reason || "")}</p><code>{e.type}</code></div><time>{new Date(e.created_at).toLocaleTimeString("zh-CN", {hour12: false})}</time></div>)}{!events.length && <p>暂无执行事件</p>}</div></details>
            </div>
          </div>}
        </>}
      </section>
      {!showDocs && <div className="composer-wrap">{health?.mode === "demo" && <div className="demo-note"><FlaskConical size={13}/> 当前使用模拟资料演示流程；配置密钥后可切换联网研究。</div>}<div className="composer">{showScope && <div className="scope-fields"><label>研究对象<input aria-label="研究对象" value={subjects} onChange={e => setSubjects(e.target.value)} placeholder="逗号分隔，最多 6 个"/></label><label>比较维度<input aria-label="比较维度" value={dimensions} onChange={e => setDimensions(e.target.value)} placeholder="逗号分隔，最多 8 个"/></label></div>}<textarea aria-label="研究目标" placeholder={run?.artifact ? "继续研究：补充问题或新增对象…" : "描述你想深入研究的问题…"} value={question} onChange={e => setQuestion(e.target.value)} onKeyDown={e => {if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {e.preventDefault(); submitResearch();}}}/><div className="composer-bottom"><div><button onClick={() => setShowScope(v => !v)} className={showScope ? "active" : ""}><Settings2 size={14}/> 研究范围</button><button disabled={!projectId || busy} onClick={() => fileInput.current?.click()}><Plus size={14}/> 添加资料</button>{run?.artifact && <span className="followup-badge">基于当前研究追加</span>}</div><button className="send-button" aria-label="提交研究" disabled={busy || !!running} onClick={submitResearch}>{busy ? <Loader2 size={18} className="spin"/> : <ArrowUp size={19}/>}</button></div></div><p className="composer-hint">对照题写清对象和维度会直接出计划；开放主题会拆成子问题树。⌘ / Ctrl + Enter 提交</p></div>}
      <input type="file" hidden ref={fileInput} accept=".md,.txt,.csv,.pdf" onChange={e => {const f = e.target.files?.[0]; if (f) upload(f);}}/>
    </main>

    {selectedEvidence && <div className="drawer-backdrop" onClick={() => setSelectedEvidence(null)}><aside className="evidence-drawer" onClick={e => e.stopPropagation()}><div className="drawer-top"><span><BookOpen size={17}/> 原文证据</span><button className="icon-button" aria-label="关闭证据" onClick={() => setSelectedEvidence(null)}><X size={18}/></button></div><div className="evidence-type">{selectedEvidence.source_type === "demo" ? "模拟来源 · 不是真实产品信息" : selectedEvidence.source_type === "document" ? "项目资料原文" : "原文快照"} <FreshnessBadge status={selectedEvidence.freshness} label={selectedEvidence.freshness_label}/></div><h2>{selectedEvidence.title}</h2><blockquote>{selectedEvidence.quote}</blockquote><dl><dt>原文位置</dt><dd>{selectedEvidence.locator}</dd>{pageFromLocator(selectedEvidence.locator) !== null && <><dt>页码</dt><dd>第 {pageFromLocator(selectedEvidence.locator)} 页</dd></>}<dt>发布日期</dt><dd>{selectedEvidence.published_at ? new Date(selectedEvidence.published_at).toLocaleString("zh-CN") : "未知 · 不会用获取时间代替"}</dd><dt>获取时间</dt><dd>{new Date(selectedEvidence.fetched_at).toLocaleString("zh-CN")}</dd><dt>证据 ID</dt><dd>{selectedEvidence.id}</dd><dt>来源</dt><dd>{selectedEvidence.url}</dd></dl>{selectedEvidence.source_type === "web" && <a className="secondary source-link" href={selectedEvidence.url} target="_blank" rel="noopener noreferrer">打开原始来源 <ArrowUpRight size={14}/></a>}<p className="footnote">引用关联经过程序检查；请核对原文是否充分支持结论。发布日期与获取时间分开记录。项目 PDF 会保留页码定位。</p></aside></div>}

    {showProject && <div className="modal-backdrop"><div className="modal" role="dialog" aria-modal="true" aria-labelledby="project-title"><div className="modal-top"><h2 id="project-title">创建研究项目</h2><button className="icon-button" aria-label="关闭" onClick={() => setShowProject(false)}><X size={18}/></button></div><p>围绕一个主题，持续积累资料、证据与成果。</p><label>项目名称<input autoFocus value={newName} onChange={e => setNewName(e.target.value)} placeholder="例如：AI 研究产品竞品分析"/></label><label>背景信息 <span>可选</span><textarea value={background} onChange={e => setBackground(e.target.value)} placeholder="研究目的、目标用户、已有判断…"/></label><button className="primary" disabled={busy || !newName.trim()} onClick={createProject}>{busy && <Loader2 size={14} className="spin"/>}创建项目</button></div></div>}
  </div>;
}
