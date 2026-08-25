"use client";

import {
  Activity,
  Check,
  ChevronRight,
  Circle,
  CircleAlert,
  Clock3,
  FileCheck2,
  Files,
  Gauge,
  PanelRightClose,
  RotateCcw,
  ShieldCheck,
  Wrench,
  X,
} from "lucide-react";
import { useState } from "react";

import type { Artifact, Claim, Evidence, Run, RunEvent, RunStep } from "@/lib/types";

interface RuntimeInspectorProps {
  open: boolean;
  mobileVisible?: boolean;
  onClose: () => void;
  onReplay: () => void;
  replaying?: boolean;
  run?: Run;
  events: RunEvent[];
  steps: RunStep[];
  evidence: Evidence[];
  claims: Claim[];
  artifacts: Artifact[];
}

type Tab = "runtime" | "evidence" | "claims" | "artifacts";

export function RuntimeInspector({
  open,
  mobileVisible,
  onClose,
  onReplay,
  replaying,
  run,
  events,
  steps,
  evidence,
  claims,
  artifacts,
}: RuntimeInspectorProps) {
  const [tab, setTab] = useState<Tab>("runtime");
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence>();
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact>();
  if (!open) return null;

  return (
    <aside className={`runtime-inspector ${mobileVisible ? "mobile-visible" : ""}`}>
      <div className="inspector-header">
        <div>
          <Activity size={17} />
          <strong>运行详情</strong>
        </div>
        <div className="inspector-header-actions">
          {run && ["COMPLETED", "FAILED", "CANCELLED"].includes(run.status) && (
            <button
              className="icon-button"
              onClick={onReplay}
              disabled={replaying}
              aria-label="回放此运行快照"
              title="回放固定证据与产物，不重新访问外部系统"
            >
              <RotateCcw size={17} />
            </button>
          )}
          <button className="icon-button" onClick={onClose} aria-label="关闭运行面板">
            <PanelRightClose size={18} />
          </button>
        </div>
      </div>

      <div className="inspector-summary">
        <StatusBadge status={run?.status ?? "IDLE"} />
        <span>{run?.plan.route ? routeName(run.plan.route) : "等待任务"}</span>
        {run?.replay_of_run_id && <span className="replay-chip">历史快照</span>}
        {run?.cost_amount && <small>¥ / ${Number(run.cost_amount).toFixed(4)}</small>}
      </div>

      <div className="inspector-tabs" role="tablist">
        <button className={tab === "runtime" ? "active" : ""} onClick={() => setTab("runtime")}>
          轨迹
        </button>
        <button className={tab === "evidence" ? "active" : ""} onClick={() => setTab("evidence")}>
          证据 <span>{evidence.length}</span>
        </button>
        <button className={tab === "claims" ? "active" : ""} onClick={() => setTab("claims")}>
          结论 <span>{claims.length}</span>
        </button>
        <button className={tab === "artifacts" ? "active" : ""} onClick={() => setTab("artifacts")}>
          产物 <span>{artifacts.length}</span>
        </button>
      </div>

      <div className="inspector-content">
        {tab === "runtime" && (
          <RuntimeTimeline run={run} steps={steps} events={events} />
        )}
        {tab === "evidence" && (
          <EvidenceList evidence={evidence} onSelect={(item) => { setSelectedArtifact(undefined); setSelectedEvidence(item); }} />
        )}
        {tab === "claims" && <ClaimList claims={claims} evidence={evidence} />}
        {tab === "artifacts" && (
          <ArtifactList artifacts={artifacts} onSelect={(item) => { setSelectedEvidence(undefined); setSelectedArtifact(item); }} />
        )}
      </div>

      {selectedEvidence && (
        <div className="evidence-detail">
          <div className="detail-header">
            <div>
              <span className="evidence-kind">{selectedEvidence.evidence_type}</span>
              <strong>{selectedEvidence.source}</strong>
            </div>
            <button className="icon-button" onClick={() => setSelectedEvidence(undefined)}>
              <X size={17} />
            </button>
          </div>
          <p className="resource-path">{selectedEvidence.resource}</p>
          <pre>{JSON.stringify(selectedEvidence.content, null, 2)}</pre>
          <div className="detail-footer">
            <span>{new Date(selectedEvidence.observed_at).toLocaleString("zh-CN")}</span>
            <span>{Math.round(Number(selectedEvidence.confidence) * 100)}% confidence</span>
          </div>
        </div>
      )}
      {selectedArtifact && (
        <div className="evidence-detail">
          <div className="detail-header">
            <div>
              <span className="evidence-kind">{selectedArtifact.kind}</span>
              <strong>{selectedArtifact.title}</strong>
            </div>
            <button className="icon-button" onClick={() => setSelectedArtifact(undefined)} aria-label="关闭产物详情">
              <X size={17} />
            </button>
          </div>
          <p className="resource-path">{selectedArtifact.media_type} · {selectedArtifact.classification}</p>
          <pre>{JSON.stringify(selectedArtifact.inline_content ?? selectedArtifact.lineage, null, 2)}</pre>
          <div className="detail-footer">
            <span>{new Date(selectedArtifact.created_at).toLocaleString("zh-CN")}</span>
            <span>Artifact</span>
          </div>
        </div>
      )}
    </aside>
  );
}

function ArtifactList({ artifacts, onSelect }: { artifacts: Artifact[]; onSelect: (item: Artifact) => void }) {
  if (!artifacts.length) {
    return <InspectorEmpty icon={<Files size={22} />} text="回答、表格、图表和 SQL 等运行产物会显示在这里" />;
  }
  return (
    <div className="evidence-list">
      {artifacts.map((item) => (
        <button key={item.id} onClick={() => onSelect(item)}>
          <span className="evidence-type">{item.kind.slice(0, 1)}</span>
          <span><strong>{item.title}</strong><small>{item.kind} · {item.media_type}</small></span>
          <ChevronRight size={15} />
        </button>
      ))}
    </div>
  );
}

function RuntimeTimeline({ run, steps, events }: { run?: Run; steps: RunStep[]; events: RunEvent[] }) {
  const latestEvent = events.at(-1);
  return (
    <div className="timeline-wrap">
      <div className="plan-heading">
        <span>执行计划</span>
        <small>{steps.length ? `${steps.filter((step) => step.status === "COMPLETED").length}/${steps.length}` : "—"}</small>
      </div>
      <ol className="runtime-timeline">
        {steps.map((step) => (
          <li key={step.id} className={step.status.toLowerCase()}>
            <span className="timeline-state">{stepIcon(step.status)}</span>
            <div>
              <strong>{step.name}</strong>
              <small>{step.kind === "CAPABILITY" ? "经 Capability Gateway" : step.kind}</small>
              {step.error_code && <em>{step.error_code}</em>}
            </div>
          </li>
        ))}
        {!steps.length && (
          <li className="pending">
            <span className="timeline-state"><Circle size={14} /></span>
            <div><strong>等待执行计划</strong><small>发送问题后将显示实时步骤</small></div>
          </li>
        )}
      </ol>

      {run && (
        <div className="run-metrics">
          <div><Clock3 size={15} /><span>状态</span><strong>{run.status}</strong></div>
          <div><Gauge size={15} /><span>步骤</span><strong>{run.step_count}</strong></div>
          <div><Wrench size={15} /><span>最新事件</span><strong>{latestEvent?.name ?? "—"}</strong></div>
        </div>
      )}
    </div>
  );
}

function EvidenceList({ evidence, onSelect }: { evidence: Evidence[]; onSelect: (item: Evidence) => void }) {
  if (!evidence.length) {
    return <InspectorEmpty icon={<FileCheck2 size={22} />} text="运行产生证据后会显示在这里" />;
  }
  return (
    <div className="evidence-list">
      {evidence.map((item) => (
        <button key={item.id} onClick={() => onSelect(item)}>
          <span className={`evidence-type type-${item.evidence_type.toLowerCase()}`}>{item.evidence_type.slice(0, 1)}</span>
          <span>
            <strong>{item.source}</strong>
            <small>{item.resource}</small>
          </span>
          <ChevronRight size={15} />
        </button>
      ))}
    </div>
  );
}

function ClaimList({ claims, evidence }: { claims: Claim[]; evidence: Evidence[] }) {
  if (!claims.length) {
    return <InspectorEmpty icon={<ShieldCheck size={22} />} text="经过 Critic 验证的结论会显示在这里" />;
  }
  return (
    <div className="claim-list">
      {claims.map((claim, index) => (
        <article key={claim.id}>
          <div className="claim-index">C{index + 1}</div>
          <p>{claim.statement}</p>
          <div>
            <span className={claim.verification_status === "VERIFIED" ? "claim-verified" : "claim-partial"}>
              {claim.verification_status === "VERIFIED" ? <Check size={13} /> : <CircleAlert size={13} />}
              {claim.verification_status}
            </span>
            <small>{claim.evidence_ids.length} 项证据</small>
          </div>
          <ul>
            {claim.evidence_ids.map((id) => {
              const item = evidence.find((entry) => entry.id === id);
              return item ? <li key={id}>{item.evidence_type} · {item.source}</li> : null;
            })}
          </ul>
        </article>
      ))}
    </div>
  );
}

function InspectorEmpty({ icon, text }: { icon: React.ReactNode; text: string }) {
  return <div className="inspector-empty">{icon}<p>{text}</p></div>;
}

function StatusBadge({ status }: { status: string }) {
  const active = ["RUNNING", "PENDING", "REPLANNING"].includes(status);
  return <span className={`status-badge ${status.toLowerCase()}`}>{active && <i />}{statusName(status)}</span>;
}

function statusName(status: string) {
  const values: Record<string, string> = {
    IDLE: "就绪", PENDING: "排队中", RUNNING: "运行中", WAITING_APPROVAL: "待审批",
    WAITING_USER: "等待输入", REPLANNING: "重新规划", COMPLETED: "已完成",
    FAILED: "失败", CANCELLED: "已取消",
  };
  return values[status] ?? status;
}

function routeName(route: string) {
  const values: Record<string, string> = {
    KNOWLEDGE: "知识研究", DATA: "数据分析", INCIDENT: "故障调查", ENGINEERING: "工程分析",
  };
  return values[route] ?? route;
}

function stepIcon(status: string) {
  if (status === "COMPLETED") return <Check size={14} />;
  if (status === "FAILED") return <CircleAlert size={14} />;
  if (status === "RUNNING") return <i className="step-spinner" />;
  return <Circle size={12} />;
}
