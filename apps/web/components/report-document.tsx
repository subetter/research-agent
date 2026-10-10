"use client";

import { useMemo, useState } from "react";
import { BookOpen, Download, Globe } from "lucide-react";

export type ReportSection = {id: string; title: string; body: string; evidence_ids?: string[]};
export type ReportOutline = {id: string; title: string};
export type ReportChart = {id: string; title: string; kind?: string; labels: string[]; values: number[]};
export type ReportTable = {id: string; title?: string; headers: string[]; rows: string[][]};
export type Evidence = {id: string; title: string; url: string; quote: string; locator: string; source_type: string; fetched_at?: string};
export type LongformArtifact = {
  title: string;
  summary: string;
  outline?: ReportOutline[];
  sections?: ReportSection[];
  open_questions?: string[];
  sources?: {id: string; title: string; url: string; quote?: string}[];
  charts?: ReportChart[];
  tables?: ReportTable[];
  verification_note?: string;
};

const CITE = /\[\[([A-Za-z0-9_-]+)\]\]/g;

export function CitedText({body, evidence, onOpen}: {body: string; evidence: Evidence[]; onOpen: (item: Evidence) => void}) {
  const parts = useMemo(() => {
    const out: {type: "text" | "cite"; value: string}[] = [];
    let cursor = 0;
    const text = body || "";
    for (const match of text.matchAll(CITE)) {
      const index = match.index ?? 0;
      if (index > cursor) out.push({type: "text", value: text.slice(cursor, index)});
      out.push({type: "cite", value: match[1]});
      cursor = index + match[0].length;
    }
    if (cursor < text.length) out.push({type: "text", value: text.slice(cursor)});
    return out;
  }, [body]);
  const byId = Object.fromEntries(evidence.map(item => [item.id, item]));
  return <div className="report-prose">{parts.map((part, index) => {
    if (part.type === "text") return <span key={index}>{part.value}</span>;
    const item = byId[part.value];
    if (!item) return <sup key={index} className="cite missing">{part.value}</sup>;
    return <span key={index} className="cite-wrap">
      <button type="button" className="cite" onClick={() => onOpen(item)} aria-label={`引用 ${item.title}`}>[{index + 1}]</button>
      <span className="cite-card"><strong>{item.title}</strong><p>{item.quote}</p><small>{item.url}</small></span>
    </span>;
  })}</div>;
}

export function ReportDocument({artifact, evidence, onOpen}: {artifact: LongformArtifact; evidence: Evidence[]; onOpen: (item: Evidence) => void}) {
  const outline = artifact.outline?.length ? artifact.outline : (artifact.sections || []).map(item => ({id: item.id, title: item.title}));
  const sections = artifact.sections || [];
  return <article className="report-document">
    {outline.length > 0 && <nav className="report-toc" aria-label="目录"><strong>目录</strong>{outline.map(item => <a key={item.id} href={`#sec-${item.id}`}>{item.title}</a>)}</nav>}
    <p className="report-lede">{artifact.summary}</p>
    {sections.map(section => <section key={section.id} id={`sec-${section.id}`} className="report-block">
      <h2>{section.title}</h2>
      <CitedText body={section.body} evidence={evidence} onOpen={onOpen}/>
    </section>)}
    {!!artifact.open_questions?.length && <section className="report-block"><h2>未解问题</h2><ul className="open-questions">{artifact.open_questions.map(item => <li key={item}>{item}</li>)}</ul></section>}
    {!!artifact.sources?.length && <section className="report-block"><h2>来源</h2><ol className="source-index">{artifact.sources.map(item => <li key={item.id}><strong>{item.title}</strong><span>{item.url}</span></li>)}</ol></section>}
    {artifact.verification_note && <p className="footnote"><BookOpen size={14}/>{artifact.verification_note}</p>}
  </article>;
}

export function WebsitePreview({runId, version}: {runId: string; version?: number | null}) {
  const [open, setOpen] = useState(false);
  const query = version ? `&version=${version}` : "";
  const src = `/api/runs/${runId}/export?format=website${query}`;
  return <div className="website-panel">
    <div className="report-toolbar">
      <span>静态站点只在浏览器里预览，不会部署。</span>
      <div className="website-actions">
        <button className="secondary small" onClick={() => setOpen(true)}><Globe size={14}/> 预览网站</button>
        <a href={src} download><Download size={14}/> 下载 HTML</a>
        <a href={`/api/runs/${runId}/export?format=website-zip${query}`}><Download size={14}/> 下载 ZIP</a>
      </div>
    </div>
    {open && <iframe className="website-frame" title="网站预览" src={src} sandbox="allow-same-origin"/>}
  </div>;
}
