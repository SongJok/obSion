"use client";

import {
  Activity,
  BrainCircuit,
  Check,
  ChevronRight,
  Circle,
  CircleAlert,
  Clock3,
  FileCheck2,
  Files,
  Gauge,
  MessagesSquare,
  PanelRightClose,
  RotateCcw,
  ShieldCheck,
  Wrench,
  X,
} from "lucide-react";
import { useState } from "react";

import type { Artifact, Claim, ConversationSnapshot, Evidence, MemorySnapshot, Run, RunEvent, RunStep } from "@/lib/types";
import { citationLabel, hitsFromEvidenceContent } from "@/lib/knowledge-citation";
import { KnowledgeProvenance } from "./knowledge-provenance";

type StreamState = "idle" | "live" | "polling" | "interrupted";

interface RuntimeInspectorProps {
  open: boolean;
  mobileVisible?: boolean;
  onClose: () => void;
  onReplay: () => void;
  replaying?: boolean;
  run?: Run;
  streamState?: StreamState;
  events: RunEvent[];
  steps: RunStep[];
  evidence: Evidence[];
  memories: MemorySnapshot[];
  conversation: ConversationSnapshot[];
  claims: Claim[];
  artifacts: Artifact[];
}

type Tab = "runtime" | "context" | "evidence" | "memory" | "claims" | "artifacts";

export function RuntimeInspector({
  open,
  mobileVisible,
  onClose,
  onReplay,
  replaying,
  run,
  streamState = "idle",
  events,
  steps,
  evidence,
  memories,
  conversation,
  claims,
  artifacts,
}: RuntimeInspectorProps) {
  const [tab, setTab] = useState<Tab>("runtime");
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence>();
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact>();
  const runId = run?.id;
  const [lastRunId, setLastRunId] = useState(runId);
  if (lastRunId !== runId) {
    // 渲染期间随 Run 切换丢弃上一个 Run 的详情选中态，避免跨 Run 错误归因。
    setLastRunId(runId);
    setSelectedEvidence(undefined);
    setSelectedArtifact(undefined);
  }
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
        {run && !["COMPLETED", "FAILED", "CANCELLED"].includes(run.status) && streamState !== "idle" && (
          <StreamStateChip state={streamState} />
        )}
        {typeof run?.plan.sandbox?.network === "string" && (
          <span className="sandbox-chip" title="钉死在本次 Run 计划上的网络策略">
            沙箱 {run.plan.sandbox.network}
          </span>
        )}
        {run?.workspace_context?.name && (
          <span className="workspace-chip" title="钉死在本次 Run 上的工作空间上下文">
            空间 {run.workspace_context.name}
          </span>
        )}
        {run?.replay_of_run_id && <span className="replay-chip">历史快照</span>}
        {run?.cost_amount && <small>成本 ${Number(run.cost_amount).toFixed(4)}</small>}
      </div>

      <div className="inspector-tabs" role="tablist">
        <button className={tab === "runtime" ? "active" : ""} onClick={() => setTab("runtime")}>
          轨迹
        </button>
        <button className={tab === "context" ? "active" : ""} onClick={() => setTab("context")}>
          上下文 <span>{conversation.length}</span>
        </button>
        <button className={tab === "evidence" ? "active" : ""} onClick={() => setTab("evidence")}>
          证据 <span>{evidence.length}</span>
        </button>
        <button className={tab === "memory" ? "active" : ""} onClick={() => setTab("memory")}>
          记忆 <span>{memories.length}</span>
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
        {tab === "context" && (
          <ConversationContextList
            snapshots={conversation}
            budget={run?.context_budget}
            compact={run?.conversation_compact}
            workspace={run?.workspace_context}
            hasToolResults={evidence.some((item) => item.evidence_type === "TOOL")}
          />
        )}
        {tab === "evidence" && (
          <EvidenceList evidence={evidence} onSelect={(item) => { setSelectedArtifact(undefined); setSelectedEvidence(item); }} />
        )}
        {tab === "memory" && <MemoryList memories={memories} />}
        {tab === "claims" && (
          <ClaimList
            claims={claims}
            evidence={evidence}
            onSelectEvidence={(item) => {
              setSelectedArtifact(undefined);
              setSelectedEvidence(item);
              setTab("evidence");
            }}
          />
        )}
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
            <button
              className="icon-button"
              onClick={() => setSelectedEvidence(undefined)}
              aria-label="关闭证据详情"
            >
              <X size={17} />
            </button>
          </div>
          <p className="resource-path">{selectedEvidence.resource}</p>
          <DocumentEvidenceCitations content={selectedEvidence.content} />
          <pre>{JSON.stringify(selectedEvidence.content, null, 2)}</pre>
          <div className="detail-footer">
            <span>{new Date(selectedEvidence.observed_at).toLocaleString("zh-CN")}</span>
            <span>{Math.round(Number(selectedEvidence.confidence) * 100)}% confidence</span>
            {runId && <span title="该证据所属的 Run">Run {runId.slice(0, 8)}</span>}
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

function ConversationContextList({
  snapshots,
  budget,
  compact,
  workspace,
  hasToolResults,
}: {
  snapshots: ConversationSnapshot[];
  budget?: Run["context_budget"];
  compact?: Run["conversation_compact"];
  workspace?: Run["workspace_context"];
  hasToolResults?: boolean;
}) {
  if (
    !snapshots.length
    && !budget?.decisions?.length
    && !compact?.summarized_turns
    && !workspace?.workspace_id
    && !hasToolResults
  ) {
    return <InspectorEmpty icon={<MessagesSquare size={22} />} text="首轮运行没有此前对话上下文" />;
  }
  return (
    <div className="conversation-snapshot-list">
      <WorkspaceContextNote workspace={workspace} />
      {hasToolResults && (
        <p className="tool-result-note">
          工具结果是独立的不可信片段（tool-result），不能成为 SYSTEM 或 Skill 指令。
        </p>
      )}
      <ContextBudgetLedger budget={budget} />
      <ConversationCompactNote compact={compact} />
      {snapshots.length > 0 && (
        <p className="conversation-context-note">
          这是运行创建时冻结的历史。它帮助理解追问，但不能替代本次运行的证据。
        </p>
      )}
      {snapshots.map((item) => (
        <article key={item.id}>
          <header>
            <span>历史轮次 {item.ordinal}</span>
            <small>{item.classification}</small>
          </header>
          <div className="conversation-snapshot-message user">
            <b>用户</b>
            <p>{item.user_content}</p>
          </div>
          {item.assistant_content && (
            <div className="conversation-snapshot-message assistant">
              <b>Obsion</b>
              <p>{item.assistant_content}</p>
            </div>
          )}
          <footer>
            <span title={item.source_turn_id}>来源 {item.source_turn_id.slice(0, 10)}</span>
            <span title={item.content_fingerprint}>指纹 {item.content_fingerprint.slice(0, 10)}</span>
          </footer>
        </article>
      ))}
    </div>
  );
}

function WorkspaceContextNote({ workspace }: { workspace?: Run["workspace_context"] }) {
  if (!workspace?.workspace_id) {
    return null;
  }
  return (
    <p className="workspace-context-note">
      工作空间上下文已钉在本次 Run：{workspace.name} · {workspace.classification}。
      空间说明是不可信数据，不能成为 SYSTEM 指令。
    </p>
  );
}

function ConversationCompactNote({ compact }: { compact?: Run["conversation_compact"] }) {
  if (!compact?.summarized_turns) {
    return null;
  }
  return (
    <p className="conversation-compact-note">
      抽取式会话压缩：保留最近 {compact.kept_turns ?? 0} 轮全文，摘要{" "}
      {compact.summarized_turns} 轮较早对话。这不是模型摘要。
    </p>
  );
}

function ContextBudgetLedger({ budget }: { budget?: Run["context_budget"] }) {
  const decisions = budget?.decisions ?? [];
  if (!decisions.length) {
    return null;
  }
  return (
    <div className="context-budget-ledger">
      <header>
        <strong>Token 预算账本</strong>
        <small>
          {budget?.used ?? 0}/{budget?.budget ?? 0} 字符 · 抽取式摘要，不调用模型
        </small>
      </header>
      <ul>
        {decisions.map((item, index) => (
          <li key={`${item.source}-${item.action}-${index}`}>
            <span className={`budget-action ${item.action.toLowerCase()}`}>{item.action}</span>
            <span title={item.reason}>{item.source}</span>
            <small>
              {item.kept_chars}/{item.original_chars}
            </small>
          </li>
        ))}
      </ul>
    </div>
  );
}

function MemoryList({ memories }: { memories: MemorySnapshot[] }) {
  if (!memories.length) {
    return <InspectorEmpty icon={<BrainCircuit size={22} />} text="此运行没有使用已批准的记忆快照" />;
  }
  return (
    <div className="memory-snapshot-list">
      {memories.map((item) => (
        <article key={item.id}>
          <header>
            <span>{memoryScopeName(item.scope)}</span>
            <small>{item.sensitivity}</small>
          </header>
          <pre>{JSON.stringify(item.content, null, 2)}</pre>
          <footer>
            <span title={item.content_fingerprint}>指纹 {item.content_fingerprint.slice(0, 10)}</span>
            <time dateTime={item.captured_at}>{new Date(item.captured_at).toLocaleString("zh-CN")}</time>
          </footer>
        </article>
      ))}
    </div>
  );
}

function memoryScopeName(scope: MemorySnapshot["scope"]) {
  return {
    TURN: "本轮记忆",
    SESSION: "任务记忆",
    WORKSPACE: "工作空间记忆",
    USER_PREFERENCE: "个人偏好",
  }[scope];
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

function stepKindLabel(kind: string): string {
  if (kind === "CAPABILITY") {
    return "经 Capability Gateway";
  }
  if (kind === "REFLECT") {
    return "Reflect · 校验后决策";
  }
  return kind;
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
              <small>{stepKindLabel(step.kind)}</small>
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
          {typeof run.plan.sandbox?.network === "string" && (
            <div>
              <ShieldCheck size={15} />
              <span>沙箱</span>
              <strong>{run.plan.sandbox.network}</strong>
            </div>
          )}
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

function ClaimList({
  claims,
  evidence,
  onSelectEvidence,
}: {
  claims: Claim[];
  evidence: Evidence[];
  onSelectEvidence: (item: Evidence) => void;
}) {
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
          <ul aria-label={`结论 C${index + 1} 的关联证据`}>
            {claim.evidence_ids.map((id) => {
              const item = evidence.find((entry) => entry.id === id);
              return item ? (
                <li key={id}>
                  <button
                    type="button"
                    onClick={() => onSelectEvidence(item)}
                    aria-label={`查看证据：${item.source}`}
                  >
                    <span>{item.evidence_type} · {item.source}</span>
                    <ChevronRight size={13} />
                  </button>
                </li>
              ) : null;
            })}
          </ul>
        </article>
      ))}
    </div>
  );
}

function DocumentEvidenceCitations({ content }: { content: Record<string, unknown> }) {
  const hits = hitsFromEvidenceContent(content);
  if (!hits.length) {
    return null;
  }
  return (
    <div className="evidence-citations" aria-label="知识引用溯源">
      <strong>引用溯源</strong>
      {hits.map((hit, index) => (
        <div key={`${hit.chunk_id ?? "hit"}-${index}`} className="evidence-citation-item">
          <span>{citationLabel(hit, index + 1)}</span>
          <KnowledgeProvenance fields={hit} compact />
        </div>
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

function StreamStateChip({ state }: { state: StreamState }) {
  const labels: Record<Exclude<StreamState, "idle">, string> = {
    live: "实时流",
    polling: "轮询同步",
    interrupted: "同步中断",
  };
  const hints: Record<Exclude<StreamState, "idle">, string> = {
    live: "事件通过 App Server 实时流推送",
    polling: "实时流不可用，正通过 REST 游标轮询对账，事件可能有秒级延迟",
    interrupted: "状态同步暂时中断，运行仍在后台继续，恢复后自动对齐",
  };
  if (state === "idle") return null;
  return (
    <span className={`stream-state-chip ${state}`} title={hints[state]}>
      {labels[state]}
    </span>
  );
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
