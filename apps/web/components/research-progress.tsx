"use client";

import { AlertCircle, Check, Clock, Loader2, PauseCircle } from "lucide-react";

export type StageState = "pending" | "current" | "waiting" | "done" | "failed" | "skipped";
export type Stage = {id: string; label: string; node?: string; state: StageState};
export type Progress = {
  stages: Stage[];
  current: string | null;
  activity: string;
  elapsed_seconds: number;
  waiting?: boolean;
  paused?: boolean;
  failed?: boolean;
  evidence_count?: number;
  round?: number;
};

export function formatElapsed(seconds: number) {
  const value = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(value / 60);
  const rest = value % 60;
  if (minutes <= 0) return `${rest} 秒`;
  return `${minutes} 分 ${rest} 秒`;
}

function StageIcon({state}: {state: StageState}) {
  if (state === "done") return <Check size={12}/>;
  if (state === "failed") return <AlertCircle size={12}/>;
  if (state === "waiting") return <PauseCircle size={12}/>;
  if (state === "current") return <Loader2 size={12} className="spin"/>;
  return <span className="stage-dot"/>;
}

export default function ResearchProgress({progress, onSelectStage, selectedStage}: {progress: Progress; onSelectStage?: (id: string) => void; selectedStage?: string | null}) {
  return <section className="progress-board" aria-label="研究进度">
    <div className="progress-board-top">
      <strong>研究进度</strong>
      <span><Clock size={13}/> 已用时 {formatElapsed(progress.elapsed_seconds)}</span>
    </div>
    <ol className="stage-rail">
      {progress.stages.map(stage => (
        <li key={stage.id}>
          <button type="button" className={`stage-item ${stage.state}${selectedStage === stage.id ? " selected" : ""}`} onClick={() => onSelectStage?.(stage.id)} disabled={stage.state === "skipped"}>
            <span className="stage-icon"><StageIcon state={stage.state}/></span>
            <span className="stage-label">{stage.label}</span>
            <small>{stage.state === "done" ? "完成" : stage.state === "current" ? "进行中" : stage.state === "waiting" ? "等待你" : stage.state === "failed" ? "失败" : stage.state === "skipped" ? "跳过" : "未开始"}</small>
          </button>
        </li>
      ))}
    </ol>
    <p className="progress-activity" aria-live="polite">{progress.activity || "任务已进入后台"}</p>
  </section>;
}
