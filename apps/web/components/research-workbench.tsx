"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUpRight, ArrowUp, BookOpen, ChevronRight, Compass, Download, FileText, FlaskConical, Layers, Loader2, Pause, Play, Plus, Search, ShieldCheck, Square, Upload, X, Activity, Check, Settings2 } from "lucide-react";

import { api } from "./api";

type Project = {id: string; name: string; background: string; runs_count: number};
type Plan = {goal: string; subjects?: string[]; dimensions?: string[]; questions?: string[]; max_search_calls?: number; max_gap_rounds?: number; as_of?: string; regions?: string[]; waiting?: string; clarify_question?: string; request?: {subjects?: string[]; dimensions?: string[]}};
type Claim = {id: string; subject: string; dimension: string; text: string; evidence_ids: string[]; kind: string; verification: string};
type Evidence = {id: string; title: string; url: string; quote: string; locator: string; source_type: string; fetched_at: string; dimensions?: string[]; citation_role?: string};
type CoverageCell = {subject: string; dimension: string; evidence_ids: string[]; covered: boolean};
type Coverage = {subjects: string[]; dimensions: string[]; cells: CoverageCell[]; covered: number; total: number; gaps: {subject: string; dimension: string}[]};
type Run = {budget?: {model_limit: number; report_reserved: number; search_limit: number}; coverage?: Coverage | null; waiting?: string | null; id: string; question: string; status: string; revision: number; mode: string; plan: Plan; error: string | null; artifact: {title: string; summary: string; claims: Claim[]; verification_note: string; coverage?: Coverage; metrics: {claims: number; evidence: number; unknown: number; with_references: number; cells_covered?: number; cells_total?: number}} | null; usage?: {kind: string; calls: number; tokens: number}[]};
type Event = {seq: number; type: string; payload: Record<string, unknown>; created_at: string};
type Doc = {id: string; name: string; characters: number; citable?: boolean};
const terminal = new Set(["completed", "partial", "cancelled", "failed"]);
const statusText: Record<string, string> = {planning: "正在规划", waiting_input: "等待确认", running: "研究进行中", pause_requested: "正在暂停", paused: "已暂停", cancel_requested: "正在取消", cancelled: "已取消", completed: "已完成", partial: "部分交付", failed: "执行失败"};
const eventText: Record<string, string> = {"budget.reached": "研究预算已达上限，转入报告阶段", "run.created": "创建研究", "plan.started": "拆解研究目标", "plan.proposed": "研究计划已生成", "run.waiting_input": "等待你确认", "run.running": "研究已启动", "task.started": "研究员开始调查", "task.finished": "研究员完成调查", "task.reused": "复用已有研究证据", "evidence.created": "保存原文证据", "tool.started": "调用研究工具", "tool.finished": "工具调用完成", "coverage.cell_filled": "覆盖格子已填上", "coverage.checked": "检查研究覆盖", "report.started": "综合分析与写作", "verify.started": "对照原文核验引用", "verify.finished": "引用核验完成", "model.finished": "模型调用完成", "run.completed": "成果已交付", "run.partial": "交付已有研究结果", "run.paused": "已保存进度", "task.failed": "子任务失败", "run.failed": "任务执行失败"};
const toolText: Record<string, string> = {search_web: "搜索网页", read_source: "阅读原文", search_project: "检索项目资料", cite_project: "选用项目段落"};
const verificationText: Record<string, string> = {reference_checked: "引用已核对", fully: "原文支持", partial: "部分支持", contradicted: "原文矛盾", unrelated: "原文无关", unconfirmed: "待确认"};

export default function Workbench() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [runs, setRuns] = useState<Run[]>([]);
  const [runId, setRunId] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [health, setHealth] = useState<{mode: string; live_ready: boolean} | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [question, setQuestion] = useState("");
  const [subjects, setSubjects] = useState("");
  const [dimensions, setDimensions] = useState("");
  const [docCitable, setDocCitable] = useState(false);
  const [events, setEvents] = useState<Event[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence | null>(null);
  const [tab, setTab] = useState<"report" | "table" | "sources" | "trace">("report");
  const [docs, setDocs] = useState<Doc[]>([]);
  const [showProject, setShowProject] = useState(false);
  const [newName, setNewName] = useState("");
  const [background, setBackground] = useState("");
  const [showScope, setShowScope] = useState(false);
  const [showDocs, setShowDocs] = useState(false);
  const [streamEpoch, setStreamEpoch] = useState(0);
  const [draftSubjects, setDraftSubjects] = useState("");
  const [draftDimensions, setDraftDimensions] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const lastSeqRef = useRef(0);
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
    Promise.all([loadProjects(), api<{mode: string; live_ready: boolean}>("/health")]).then(([items, h]) => {
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

  const refreshRun = useCallback(async (id: string) => {
    const [current, sources] = await Promise.all([api<Run>(`/runs/${id}`), api<Evidence[]>(`/runs/${id}/evidence`)]);
    if (selectedRunRef.current !== id) return;
    setRun(current); setEvidence(sources);
    setRuns(items => items.map(r => r.id === id ? current : r));
    return current;
  }, []);

  useEffect(() => {
    lastSeqRef.current = 0;
    setRun(null); setEvidence([]); setEvents([]); setSelectedEvidence(null);
  }, [runId]);

  useEffect(() => {
    if (!runId) return;
    let stopped = false;
    let source: EventSource | null = null;
    refreshRun(runId).then(current => {
      if (!current || stopped) return;
      if (terminal.has(current.status)) {
        api<Event[]>(`/runs/${runId}/trace`).then(list => {if (!stopped) {setEvents(list); lastSeqRef.current = list.length ? list[list.length - 1].seq : 0;}}).catch(e => {if (!stopped) setError(e.message);});
      } else {
        source = new EventSource(`/api/runs/${runId}/events?after=${lastSeqRef.current}`);
        source.onmessage = event => {
          const item = JSON.parse(event.data) as Event;
          lastSeqRef.current = Math.max(lastSeqRef.current, item.seq);
          setEvents(prev => prev.some(e => e.seq === item.seq) ? prev : [...prev, item]);
          refreshRun(runId).then(r => {if (r && terminal.has(r.status)) source?.close();}).catch(e => {if (!stopped) setError(e.message);});
        };
      }
    }).catch(e => {if (!stopped) setError(e.message);});
    return () => {stopped = true; source?.close();};
  }, [runId, refreshRun, streamEpoch]);

  useEffect(() => {
    if (run?.status !== "waiting_input") return;
    if (run.waiting === "clarify" || run.plan.waiting === "clarify") {
      setDraftSubjects((run.plan.request?.subjects || []).join(", "));
      setDraftDimensions((run.plan.request?.dimensions || []).join(", "));
    } else if (run.plan.subjects) {
      setDraftSubjects(run.plan.subjects.join(", "));
      setDraftDimensions((run.plan.dimensions || []).join(", "));
    }
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
      setQuestion(""); setTab("report");
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
          latest = await api<Run>(`/runs/${currentId}/plan`, {method: "PUT", body: JSON.stringify({expected_revision: latest.revision, plan: {...latest.plan, subjects: split(draftSubjects), dimensions: split(draftDimensions)}})});
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

  const running = run && ["running", "planning", "pause_requested", "cancel_requested"].includes(run.status);
  const clarifying = run?.status === "waiting_input" && (run.waiting === "clarify" || run.plan.waiting === "clarify");
  const taskDone = new Set(events.filter(e => e.type === "task.finished" || e.type === "task.reused").map(e => String(e.payload.subject))).size;
  const coverage = run?.coverage || run?.artifact?.coverage || null;
  const planSubjects = run?.plan.subjects || [];
  const eventDetail = (item: Event) => {
    const payload = item.payload;
    const tool = typeof payload.tool === "string" ? (toolText[payload.tool] || payload.tool) : "";
    const target = typeof payload.target === "string" ? payload.target : typeof payload.query === "string" ? payload.query : typeof payload.url === "string" ? payload.url : "";
    if (item.type === "coverage.cell_filled" && typeof payload.subject === "string") return `${payload.subject} · ${payload.dimension}（${payload.covered}/${payload.total}）`;
    if (item.type.startsWith("tool.") && (tool || target)) return [tool, target].filter(Boolean).join(" · ");
    if (typeof payload.subject === "string" && payload.subject) return payload.subject;
    if (typeof payload.title === "string" && payload.title) return payload.title;
    if (typeof payload.operation === "string" && payload.operation) return payload.operation;
    if (typeof payload.reason === "string" && payload.reason) return payload.reason;
    if (typeof payload.question === "string" && payload.question) return payload.question;
    if (typeof payload.cells_covered === "number" && typeof payload.cells_total === "number") return `覆盖 ${payload.cells_covered}/${payload.cells_total} 个对象×维度`;
    return "";
  };
  const groupedTrail = planSubjects.map(subject => ({
    subject,
    items: events.filter(item => item.payload.subject === subject),
  }));
  const generalTrail = events.filter(item => typeof item.payload.subject !== "string" || !item.payload.subject);

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
        {showDocs ? <><div className="section-heading"><div><p className="eyebrow">PROJECT LIBRARY</p><h1>项目资料</h1><p>检索命中只是候选。未标记为可引用事实的资料只能支撑分析。</p></div><button className="primary small" disabled={busy || !projectId} onClick={() => fileInput.current?.click()}><Upload size={15}/> 上传资料</button></div><label className="citable-toggle"><input type="checkbox" checked={docCitable} onChange={e => setDocCitable(e.target.checked)}/> 新上传的资料可作为事实引用（默认仅分析）</label><div className="library-grid">{docs.map(d => <div className="document-card" key={d.id}><FileText size={25}/><h3>{d.name}</h3><p>{d.characters.toLocaleString()} 字符 · {d.citable ? "可作事实引用" : "仅作分析"}</p><button className="secondary small" disabled={busy} onClick={() => action(async () => {const updated = await api<Doc>(`/projects/${projectId}/documents/${d.id}`, {method: "PATCH", body: JSON.stringify({citable: !d.citable})}); setDocs(items => items.map(item => item.id === d.id ? {...item, ...updated} : item));})}>{d.citable ? "改为仅分析" : "标为可引用事实"}</button></div>)}{!docs.length && <div className="empty-library"><BookOpen size={30}/><h3>为研究补充背景</h3><p>支持 UTF-8 TXT、Markdown、CSV，单个文件最大 1 MB。关键词命中不会自动成为证据。</p><button className="secondary" onClick={() => fileInput.current?.click()}>选择文件</button></div>}</div><p className="footnote">首版使用本地关键词检索；向量检索、PDF 解析将在下一阶段加入。</p></> : <>
          {!run ? <div className="welcome"><div className="eyebrow"><span/> YOUR NEXT DISCOVERY</div><h1>让好问题，<br/><span>成为有依据的洞察。</span></h1><p className="welcome-copy">规划、探索、验证，再交付。<br/>一个沉淀资料与证据的研究空间。</p><div className="welcome-features"><span><Search size={15}/> 多源探索</span><span><ShieldCheck size={15}/> 证据溯源</span><span><FileText size={15}/> 成果交付</span></div><div className="sample-grid">{[{title: "理解产品格局", q: "调研 ChatGPT、Gemini 和 Claude 的深度研究产品形态，比较研究流程与交付方式。", icon: <Compass size={20}/>}, {title: "发现差异化机会", q: "分析 AI 编程工具的产品定位、核心能力和用户场景，输出竞品对比。", icon: <FlaskConical size={20}/>}, {title: "整理研究证据", q: "结合项目资料，梳理当前行业观点、支持证据与尚待验证的问题。", icon: <BookOpen size={20}/>}].map(s => <button key={s.title} className="sample-card" onClick={() => setQuestion(s.q)}>{s.icon}<strong>{s.title}</strong><p>{s.q}</p><ArrowUpRight size={16}/></button>)}</div></div> : <div className="research-detail">
            <div className="section-heading"><div><p className="eyebrow">RESEARCH SESSION</p><h1>{run.question}</h1><div className="session-meta"><span className={`status ${run.status}`}>{running && <Loader2 size={12} className="spin"/>}{statusText[run.status]}</span><span>{run.mode === "demo" ? "模拟研究 · 不包含真实事实" : "联网研究"}</span><span>{run.id.slice(-8)}</span></div></div><div className="run-actions">{["running", "planning"].includes(run.status) && <button className="icon-button" aria-label="暂停研究" disabled={busy} onClick={() => command("pause")}><Pause size={16}/></button>}{run.status === "paused" && <button className="secondary" disabled={busy} onClick={() => command("resume")}><Play size={14}/> 恢复研究</button>}{run.status === "failed" && <button className="secondary" disabled={busy} onClick={() => command("retry")}>重试</button>}{!terminal.has(run.status) && <button className="icon-button" aria-label="取消研究" disabled={busy} onClick={() => command("cancel")}><Square size={14}/></button>}</div></div>
            {run.error && <div className="inline-warning" role="alert"><strong>本次研究未完成</strong><p>{run.error}</p><small>已保留 {evidence.length} 条证据。重试会从失败步骤继续，复用已完成的调用。</small></div>}
            {run.budget && <div className="budget-panel"><div className="budget-heading"><strong>研究预算</strong><span>调用次数上限 · 不代表账户余额</span></div><div className="budget-grid">{[{kind: "model", label: "模型调用", limit: run.budget.model_limit}, {kind: "search", label: "联网搜索", limit: run.budget.search_limit}].map(item => {const used = (run.usage || []).filter(u => u.kind === item.kind).reduce((n, u) => n + u.calls, 0); return <div key={item.kind}><span>{item.label}</span><strong>{used} / {item.limit}</strong><progress value={Math.min(used, item.limit)} max={item.limit}/></div>;})}<div><span>原文证据</span><strong>{evidence.length} 条</strong><small>已保存，可用于后续报告</small></div></div><p>模型总预算中预留 {run.budget.report_reserved} 次用于报告生成；达到研究额度后会基于已有证据交付，资料缺失项标为待确认。</p></div>}
            {coverage && coverage.total > 0 && <div className="coverage-panel"><div className="coverage-heading"><strong>覆盖矩阵</strong><span>已覆盖 {coverage.covered} / {coverage.total} 个对象×维度</span></div><div className="table-scroll"><table className="coverage-table"><thead><tr><th>对象</th>{coverage.dimensions.map(d => <th key={d}>{d}</th>)}</tr></thead><tbody>{coverage.subjects.map(s => <tr key={s}><th>{s}</th>{coverage.dimensions.map(d => {const cell = coverage.cells.find(c => c.subject === s && c.dimension === d); const fresh = events.some(e => e.type === "coverage.cell_filled" && e.payload.subject === s && e.payload.dimension === d); return <td key={d} className={`${cell?.covered ? "covered" : "missing"}${fresh && cell?.covered ? " fresh" : ""}`}>{cell?.covered ? "有原文" : "缺失"}</td>;})}</tr>)}</tbody></table></div></div>}
            {clarifying && <div className="plan-card"><div className="plan-header"><div className="plan-icon"><Settings2 size={19}/></div><div><h3>先补全研究范围</h3><p>{run.plan.clarify_question || "问题里还看不出比较对象或维度。补全后才会生成计划。"}</p></div></div><label>研究对象<input value={draftSubjects} onChange={e => setDraftSubjects(e.target.value)} aria-label="澄清研究对象"/></label><label>比较维度<input value={draftDimensions} onChange={e => setDraftDimensions(e.target.value)} aria-label="澄清比较维度"/></label><div className="plan-footer"><span>只问这一次；时间与地区会写在计划卡上</span><button className="primary" disabled={busy || !split(draftSubjects).length || !split(draftDimensions).length} onClick={() => command("start")}><Play size={14}/> 生成研究计划</button></div></div>}
            {run.status === "waiting_input" && !clarifying && <div className="plan-card"><div className="plan-header"><div className="plan-icon"><Settings2 size={19}/></div><div><h3>先对齐研究方向</h3><p>确认对象与比较维度后，研究员将开始并行探索。</p></div></div><label>研究对象<input value={draftSubjects} onChange={e => setDraftSubjects(e.target.value)} aria-label="计划研究对象"/></label><label>比较维度<input value={draftDimensions} onChange={e => setDraftDimensions(e.target.value)} aria-label="计划比较维度"/></label><div className="plan-defaults">默认范围：截至 {run.plan.as_of || "提交日"} · 地区 {(run.plan.regions || ["未限定"]).join("、")}</div><div className="plan-footer"><span>最多 {run.plan.max_search_calls} 次搜索 · {run.plan.max_gap_rounds} 轮补充研究</span><button className="primary" disabled={busy} onClick={() => command("start")}><Play size={14}/> 确认并开始研究</button></div></div>}
            {running && <div className="progress-card"><div className="progress-top"><span className="pulse-dot"/><strong>{run.status === "planning" ? "正在制定研究计划" : events.some(e => e.type === "verify.started") ? "正在对照原文核验引用" : events.some(e => e.type === "report.started") ? "正在综合证据与生成报告" : "正在收集与验证研究证据"}</strong><span>{evidence.length} 条证据</span></div><div className="progress-track"><span style={{width: `${Math.min(100, (taskDone / Math.max(1, planSubjects.length || 1)) * 100)}%`}}/></div><p>{events.length ? `${eventText[events[events.length - 1].type] || events[events.length - 1].type}${eventDetail(events[events.length - 1]) ? ` · ${eventDetail(events[events.length - 1])}` : ""}` : "任务已进入后台"} · 完成 {taskDone} 个研究子任务</p><div className="subject-trail">{groupedTrail.map(group => {const last = group.items[group.items.length - 1]; return <div key={group.subject}><strong>{group.subject}</strong><p>{last ? `${eventText[last.type] || last.type}${eventDetail(last) ? ` · ${eventDetail(last)}` : ""}` : "等待开始"}</p><ol>{group.items.filter(item => item.type.startsWith("tool.") || item.type === "coverage.cell_filled" || item.type === "evidence.created").slice(-5).map(item => <li key={item.seq}>{eventText[item.type] || item.type}{eventDetail(item) ? ` · ${eventDetail(item)}` : ""}</li>)}</ol></div>;})}</div></div>}
            <div className="tabs">{[{id: "report", text: "研究报告", icon: <FileText size={15}/>}, {id: "table", text: "对比表", icon: <Layers size={15}/>}, {id: "sources", text: `证据来源 ${evidence.length}`, icon: <BookOpen size={15}/>}, {id: "trace", text: "执行轨迹", icon: <Activity size={15}/>}].map(t => <button key={t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id as typeof tab)}>{t.icon}{t.text}</button>)}</div>
            {tab === "report" && (run.artifact ? <div className="report"><div className="report-toolbar"><span><Check size={14}/> 成果已保存</span><a href={`/api/runs/${run.id}/export?format=markdown`}><Download size={14}/> 导出报告</a></div><p className="report-summary">{run.artifact.summary}</p><div className="metric-row"><div><strong>{run.artifact.metrics.claims}</strong><span>研究条目</span></div><div><strong>{run.artifact.metrics.evidence}</strong><span>原文证据</span></div><div><strong>{run.artifact.metrics.unknown}</strong><span>待确认项</span></div></div>{planSubjects.map(subject => <section className="report-subject" key={subject}><h2>{subject}</h2>{run.artifact?.claims.filter(c => c.subject === subject).map(c => <div className="claim" key={c.id}><h3>{c.dimension}<span className={c.kind}>{c.kind === "fact" ? "事实" : c.kind === "analysis" ? "分析" : "待确认"}</span><span className={`verify ${c.verification}`}>{verificationText[c.verification] || c.verification}</span></h3><p>{c.text}</p><div className="references">{c.evidence_ids.map((id, i) => <button key={id} onClick={() => setSelectedEvidence(evidence.find(e => e.id === id) || null)}><BookOpen size={12}/> 原文证据 {i + 1}</button>)}</div></div>)}</section>)}<p className="footnote"><ShieldCheck size={14}/>{run.artifact.verification_note}</p></div> : <div className="pending-output"><FileText size={27}/><h3>研究完成后，洞察将在这里汇集</h3><p>你可以先查看证据来源与执行轨迹。</p></div>)}
            {tab === "table" && (run.artifact ? <div className="comparison"><div className="report-toolbar"><span>与报告共享同一批结论</span><a href={`/api/runs/${run.id}/export?format=csv`}><Download size={14}/> 导出 CSV</a></div><div className="table-scroll"><table><thead><tr><th>比较维度</th>{planSubjects.map(s => <th key={s}>{s}</th>)}</tr></thead><tbody>{(run.plan.dimensions || []).map(d => <tr key={d}><th>{d}</th>{planSubjects.map(s => {const c = run.artifact?.claims.find(c => c.subject === s && c.dimension === d); return <td key={s}>{c?.text || "未确认"}{c?.evidence_ids.map(id => <button className="table-reference" key={id} onClick={() => setSelectedEvidence(evidence.find(e => e.id === id) || null)}>查看证据 ↗</button>)}</td>;})}</tr>)}</tbody></table></div></div> : <div className="pending-output">报告生成后可查看结构化对比表</div>)}
            {tab === "sources" && <div className="source-list">{evidence.map((e, i) => <button key={e.id} className="source-card" onClick={() => setSelectedEvidence(e)}><span className="source-index">{String(i + 1).padStart(2, "0")}</span><div><h3>{e.title}</h3><p>{e.quote.slice(0, 110)}</p><small>{e.source_type === "demo" ? "模拟来源" : e.source_type === "web" ? "互联网来源" : e.citation_role === "fact" ? "项目资料 · 可引用事实" : "项目资料 · 分析口径"} · {e.locator}</small></div><ArrowUpRight size={16}/></button>)}{!evidence.length && <div className="pending-output">研究员读取原文或选用项目段落后，证据会保存在这里。</div>}</div>}
            {tab === "trace" && <div className="timeline">{groupedTrail.map(group => <div className="trail-group" key={group.subject}><h3>{group.subject}</h3>{group.items.map(e => <div className="timeline-item" key={e.seq}><span className="timeline-dot"/><div><strong>{eventText[e.type] || statusText[e.type.replace("run.", "")] || e.type}</strong><p>{eventDetail(e)}</p><code>{e.type}</code></div><time>{new Date(e.created_at).toLocaleTimeString("zh-CN", {hour12: false})}</time></div>)}{!group.items.length && <p className="nav-empty">该对象尚无事件</p>}</div>)}{!!generalTrail.length && <div className="trail-group"><h3>任务</h3>{generalTrail.map(e => <div className="timeline-item" key={e.seq}><span className="timeline-dot"/><div><strong>{eventText[e.type] || statusText[e.type.replace("run.", "")] || e.type}</strong><p>{eventDetail(e)}</p><code>{e.type}</code></div><time>{new Date(e.created_at).toLocaleTimeString("zh-CN", {hour12: false})}</time></div>)}</div>}{!events.length && <p>暂无执行事件</p>}</div>}
          </div>}
        </>}
      </section>
      {!showDocs && <div className="composer-wrap">{health?.mode === "demo" && <div className="demo-note"><FlaskConical size={13}/> 当前使用模拟资料演示流程；配置密钥后可切换联网研究。</div>}<div className="composer">{showScope && <div className="scope-fields"><label>研究对象<input aria-label="研究对象" value={subjects} onChange={e => setSubjects(e.target.value)} placeholder="逗号分隔，最多 6 个"/></label><label>比较维度<input aria-label="比较维度" value={dimensions} onChange={e => setDimensions(e.target.value)} placeholder="逗号分隔，最多 8 个"/></label></div>}<textarea aria-label="研究目标" placeholder={run?.artifact ? "继续研究：补充问题或新增对象…" : "描述你想深入研究的问题…"} value={question} onChange={e => setQuestion(e.target.value)} onKeyDown={e => {if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {e.preventDefault(); submitResearch();}}}/><div className="composer-bottom"><div><button onClick={() => setShowScope(v => !v)} className={showScope ? "active" : ""}><Settings2 size={14}/> 研究范围</button><button disabled={!projectId || busy} onClick={() => fileInput.current?.click()}><Plus size={14}/> 添加资料</button>{run?.artifact && <span className="followup-badge">基于当前研究追加</span>}</div><button className="send-button" aria-label="提交研究" disabled={busy || !!running} onClick={submitResearch}>{busy ? <Loader2 size={18} className="spin"/> : <ArrowUp size={19}/>}</button></div></div><p className="composer-hint">问题写清对象和维度会直接出计划；缺了才问一次。⌘ / Ctrl + Enter 提交</p></div>}
      <input type="file" hidden ref={fileInput} accept=".md,.txt,.csv" onChange={e => {const f = e.target.files?.[0]; if (f) upload(f);}}/>
    </main>

    {selectedEvidence && <div className="drawer-backdrop" onClick={() => setSelectedEvidence(null)}><aside className="evidence-drawer" onClick={e => e.stopPropagation()}><div className="drawer-top"><span><BookOpen size={17}/> 原文证据</span><button className="icon-button" aria-label="关闭证据" onClick={() => setSelectedEvidence(null)}><X size={18}/></button></div><div className="evidence-type">{selectedEvidence.source_type === "demo" ? "模拟来源 · 不是真实产品信息" : "原文快照"}</div><h2>{selectedEvidence.title}</h2><blockquote>{selectedEvidence.quote}</blockquote><dl><dt>原文位置</dt><dd>{selectedEvidence.locator}</dd><dt>获取时间</dt><dd>{new Date(selectedEvidence.fetched_at).toLocaleString("zh-CN")}</dd><dt>证据 ID</dt><dd>{selectedEvidence.id}</dd><dt>来源</dt><dd>{selectedEvidence.url}</dd></dl>{selectedEvidence.source_type === "web" && <a className="secondary source-link" href={selectedEvidence.url} target="_blank" rel="noopener noreferrer">打开原始来源 <ArrowUpRight size={14}/></a>}<p className="footnote">引用关联经过程序检查；请核对原文是否充分支持结论。</p></aside></div>}

    {showProject && <div className="modal-backdrop"><div className="modal" role="dialog" aria-modal="true" aria-labelledby="project-title"><div className="modal-top"><h2 id="project-title">创建研究项目</h2><button className="icon-button" aria-label="关闭" onClick={() => setShowProject(false)}><X size={18}/></button></div><p>围绕一个主题，持续积累资料、证据与成果。</p><label>项目名称<input autoFocus value={newName} onChange={e => setNewName(e.target.value)} placeholder="例如：AI 研究产品竞品分析"/></label><label>背景信息 <span>可选</span><textarea value={background} onChange={e => setBackground(e.target.value)} placeholder="研究目的、目标用户、已有判断…"/></label><button className="primary" disabled={busy || !newName.trim()} onClick={createProject}>{busy && <Loader2 size={14} className="spin"/>}创建项目</button></div></div>}
  </div>;
}
