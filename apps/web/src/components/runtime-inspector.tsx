"use client";

import {
  Activity,
  BookCheck,
  BrainCircuit,
  Check,
  ChevronRight,
  Circle,
  CircleAlert,
  Clock3,
  FileCheck2,
  Files,
  Gauge,
  ListTodo,
  MessagesSquare,
  PanelRightClose,
  RotateCcw,
  ShieldCheck,
  Wrench,
  X,
  XCircle,
} from "lucide-react";
import { FormEvent, KeyboardEvent as ReactKeyboardEvent, useState } from "react";

import { ApiError, api } from "@/lib/api";
import {
  claimDecisionPayload,
  claimTaskPayload,
  truncateClaim,
} from "@/lib/claim-actions";
import type { Artifact, Claim, ConversationSnapshot, Evidence, MemorySnapshot, Run, RunEvent, RunStep } from "@/lib/types";
import { EvidenceContent, EvidenceMeta } from "./evidence-content";

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
  onOpenCollaboration?: () => void;
}

type Tab = "runtime" | "context" | "evidence" | "memory" | "claims" | "artifacts";

const INSPECTOR_TABS: readonly Tab[] = [
  "runtime",
  "context",
  "evidence",
  "memory",
  "claims",
  "artifacts",
];

const INSPECTOR_TAB_LABELS: Record<Tab, string> = {
  runtime: "轨迹",
  context: "上下文",
  evidence: "证据",
  memory: "记忆",
  claims: "结论",
  artifacts: "产物",
};

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
  onOpenCollaboration,
}: RuntimeInspectorProps) {
  const [tab, setTab] = useState<Tab>("runtime");
  const [selectedEvidence, setSelectedEvidence] = useState<Evidence>();
  const [selectedArtifact, setSelectedArtifact] = useState<Artifact>();
  const [claimAction, setClaimAction] = useState<{ claim: Claim; index: number; mode: "task" | "decision" }>();
  const runId = run?.id;
  const [lastRunId, setLastRunId] = useState(runId);
  if (lastRunId !== runId) {
    // 渲染期间随 Run 切换丢弃上一个 Run 的详情选中态，避免跨 Run 错误归因。
    setLastRunId(runId);
    setSelectedEvidence(undefined);
    setSelectedArtifact(undefined);
    setClaimAction(undefined);
  }
  if (!open) return null;

  const handleTabKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    const currentIndex = INSPECTOR_TABS.indexOf(tab);
    let nextIndex: number | undefined;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % INSPECTOR_TABS.length;
    if (event.key === "ArrowLeft") {
      nextIndex = (currentIndex - 1 + INSPECTOR_TABS.length) % INSPECTOR_TABS.length;
    }
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = INSPECTOR_TABS.length - 1;
    if (nextIndex === undefined) return;
    event.preventDefault();
    const nextTab = INSPECTOR_TABS[nextIndex];
    setTab(nextTab);
    event.currentTarget.parentElement
      ?.querySelector<HTMLButtonElement>(`[data-runtime-tab="${nextTab}"]`)
      ?.focus();
  };
  const tabCounts: Record<Tab, number | undefined> = {
    runtime: undefined,
    context: conversation.length,
    evidence: evidence.length,
    memory: memories.length,
    claims: claims.length,
    artifacts: artifacts.length,
  };

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

      <div className="inspector-tabs" role="tablist" aria-label="运行详情页签">
        {INSPECTOR_TABS.map((item) => (
          <button
            key={item}
            type="button"
            role="tab"
            id={`runtime-tab-${item}`}
            aria-controls="runtime-panel"
            aria-selected={tab === item}
            tabIndex={tab === item ? 0 : -1}
            data-runtime-tab={item}
            className={tab === item ? "active" : ""}
            onKeyDown={handleTabKeyDown}
            onClick={() => setTab(item)}
          >
            {INSPECTOR_TAB_LABELS[item]}
            {tabCounts[item] !== undefined && <span>{tabCounts[item]}</span>}
          </button>
        ))}
      </div>

      <div
        className="inspector-content"
        role="tabpanel"
        id="runtime-panel"
        aria-labelledby={`runtime-tab-${tab}`}
        tabIndex={0}
      >
        {tab === "runtime" && (
          <RuntimeTimeline
            run={run}
            steps={steps}
            events={events}
            evidence={evidence}
            claims={claims}
            onSelectEvidence={(item) => {
              setSelectedArtifact(undefined);
              setSelectedEvidence(item);
              setTab("evidence");
            }}
            onOpenClaims={() => setTab("claims")}
          />
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
            run={run}
            onSelectEvidence={(item) => {
              setSelectedArtifact(undefined);
              setSelectedEvidence(item);
              setTab("evidence");
            }}
            onClaimAction={(claim, index, mode) => setClaimAction({ claim, index, mode })}
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
          <div className="evidence-detail-body">
            <EvidenceContent evidence={selectedEvidence} />
          </div>
          <EvidenceMeta evidence={selectedEvidence} />
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
      {claimAction && run && (
        <ClaimActionModal
          run={run}
          claim={claimAction.claim}
          index={claimAction.index}
          mode={claimAction.mode}
          evidence={evidence}
          onClose={() => setClaimAction(undefined)}
          onOpenCollaboration={onOpenCollaboration}
        />
      )}
    </aside>
  );
}

function ClaimActionModal({
  run,
  claim,
  index,
  mode,
  evidence,
  onClose,
  onOpenCollaboration,
}: {
  run: Run;
  claim: Claim;
  index: number;
  mode: "task" | "decision";
  evidence: Evidence[];
  onClose: () => void;
  onOpenCollaboration?: () => void;
}) {
  const workspaceId = run.workspace_context?.workspace_id;
  const [initial] = useState(() =>
    mode === "task"
      ? claimTaskPayload(claim, run.id, index)
      : claimDecisionPayload(claim, evidence, run.id, index),
  );
  const [title, setTitle] = useState(String(initial.title ?? ""));
  const [body, setBody] = useState(
    String(mode === "task" ? initial.description : initial.rationale),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!workspaceId || !title.trim() || !body.trim()) return;
    setSaving(true);
    setError("");
    try {
      if (mode === "task") {
        await api.collaboration.createTask(workspaceId, {
          title: title.trim(),
          description: body.trim(),
          source_run_id: run.id,
        });
        setDone("任务已创建，并带来源 Run 溯源");
      } else {
        await api.collaboration.createDecision(workspaceId, {
          title: title.trim(),
          summary: truncateClaim(claim.statement, 240),
          rationale: body.trim(),
          source_run_id: run.id,
        });
        setDone("决策已记录，并带来源 Run 溯源");
      }
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "workspace_source_run_mismatch") {
        setError("来源 Run 必须属于当前工作空间，请刷新后重试。");
      } else {
        setError(caught instanceof Error ? caught.message : "操作未能完成");
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="workspace-modal claim-action-modal" onSubmit={(event) => void submit(event)}>
        <header>
          <span className="modal-icon">{mode === "task" ? <ListTodo size={19} /> : <BookCheck size={19} />}</span>
          <div>
            <h2>{mode === "task" ? "结论转为任务" : "结论记录为决策"}</h2>
            <p>来源 Run {run.id.slice(0, 8)} 与证据链会随记录保留</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose}><X size={18} /></button>
        </header>
        {done ? (
          <>
            <div className="notice success"><Check size={15} />{done}</div>
            <footer>
              <button type="button" className="secondary-button" onClick={onClose}>关闭</button>
              {onOpenCollaboration && (
                <button type="button" className="primary-button" onClick={() => { onClose(); onOpenCollaboration(); }}>在协作中查看</button>
              )}
            </footer>
          </>
        ) : (
          <>
            <label><span>标题</span><input autoFocus value={title} onChange={(event) => setTitle(event.target.value)} maxLength={300} /></label>
            <label>
              <span>{mode === "task" ? "任务描述" : "决策理由"}</span>
              <textarea value={body} onChange={(event) => setBody(event.target.value)} rows={7} maxLength={mode === "task" ? 20000 : 40000} />
            </label>
            {error && <div className="notice error"><XCircle size={14} />{error}</div>}
            <footer>
              <button type="button" className="secondary-button" onClick={onClose}>取消</button>
              <button className="primary-button" disabled={saving || !title.trim() || !body.trim() || !workspaceId}>
                {saving ? "正在保存…" : mode === "task" ? "创建任务" : "记录决策"}
              </button>
            </footer>
          </>
        )}
      </form>
    </div>
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

const MAX_STEP_EVIDENCE_CHIPS = 6;

function stepDurationLabel(step: RunStep): string | undefined {
  if (!step.started_at || !step.completed_at) {
    return undefined;
  }
  const ms = new Date(step.completed_at).getTime() - new Date(step.started_at).getTime();
  if (!Number.isFinite(ms) || ms < 0) {
    return undefined;
  }
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function RuntimeTimeline({
  run,
  steps,
  events,
  evidence,
  claims,
  onSelectEvidence,
  onOpenClaims,
}: {
  run?: Run;
  steps: RunStep[];
  events: RunEvent[];
  evidence: Evidence[];
  claims: Claim[];
  onSelectEvidence: (item: Evidence) => void;
  onOpenClaims: () => void;
}) {
  const latestEvent = events.at(-1);
  const stepIds = new Set(steps.map((step) => step.id));
  const unattributed = evidence.filter((item) => !item.step_id || !stepIds.has(item.step_id));
  const claimIndexByEvidenceId = new Map<string, number[]>();
  claims.forEach((claim, index) => {
    claim.evidence_ids.forEach((id) => {
      const list = claimIndexByEvidenceId.get(id) ?? [];
      list.push(index);
      claimIndexByEvidenceId.set(id, list);
    });
  });
  return (
    <div className="timeline-wrap">
      <div className="plan-heading">
        <span>执行计划</span>
        <small>{steps.length ? `${steps.filter((step) => step.status === "COMPLETED").length}/${steps.length}` : "—"}</small>
      </div>
      <ol className="runtime-timeline">
        {steps.map((step) => {
          const stepEvidence = evidence.filter((item) => item.step_id === step.id);
          const stepClaims = [
            ...new Set(stepEvidence.flatMap((item) => claimIndexByEvidenceId.get(item.id) ?? [])),
          ].sort((a, b) => a - b);
          const duration = stepDurationLabel(step);
          const visibleEvidence = stepEvidence.slice(0, MAX_STEP_EVIDENCE_CHIPS);
          return (
            <li key={step.id} className={step.status.toLowerCase()}>
              <span className="timeline-state">{stepIcon(step.status)}</span>
              <div>
                <strong>{step.name}</strong>
                <small>
                  {stepKindLabel(step.kind)}
                  {duration ? ` · ${duration}` : ""}
                </small>
                {step.error_code && <em>{step.error_code}</em>}
                {stepEvidence.length > 0 && (
                  <div className="step-evidence" aria-label={`步骤「${step.name}」产生的证据`}>
                    {visibleEvidence.map((item) => (
                      <button
                        key={item.id}
                        type="button"
                        className={`step-evidence-chip type-${item.evidence_type.toLowerCase()}`}
                        onClick={() => onSelectEvidence(item)}
                        title={`${item.evidence_type} · ${item.source} · ${item.resource}`}
                      >
                        {item.evidence_type}
                      </button>
                    ))}
                    {stepEvidence.length > visibleEvidence.length && (
                      <small>+{stepEvidence.length - visibleEvidence.length}</small>
                    )}
                  </div>
                )}
                {stepClaims.length > 0 && (
                  <div className="step-claims">
                    {stepClaims.map((index) => (
                      <button
                        key={index}
                        type="button"
                        onClick={onOpenClaims}
                        title="该步骤的证据支撑了此结论，点击打开结论页"
                      >
                        结论 C{index + 1}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </li>
          );
        })}
        {!steps.length && (
          <li className="pending">
            <span className="timeline-state"><Circle size={14} /></span>
            <div><strong>等待执行计划</strong><small>发送问题后将显示实时步骤</small></div>
          </li>
        )}
      </ol>

      {unattributed.length > 0 && (
        <div className="unattributed-evidence">
          <small>未关联步骤的证据（{unattributed.length}）</small>
          <div className="step-evidence">
            {unattributed.slice(0, MAX_STEP_EVIDENCE_CHIPS).map((item) => (
              <button
                key={item.id}
                type="button"
                className={`step-evidence-chip type-${item.evidence_type.toLowerCase()}`}
                onClick={() => onSelectEvidence(item)}
                title={`${item.evidence_type} · ${item.source} · ${item.resource}`}
              >
                {item.evidence_type}
              </button>
            ))}
            {unattributed.length > MAX_STEP_EVIDENCE_CHIPS && (
              <small>+{unattributed.length - MAX_STEP_EVIDENCE_CHIPS}</small>
            )}
          </div>
        </div>
      )}

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
  run,
  onSelectEvidence,
  onClaimAction,
}: {
  claims: Claim[];
  evidence: Evidence[];
  run?: Run;
  onSelectEvidence: (item: Evidence) => void;
  onClaimAction?: (claim: Claim, index: number, mode: "task" | "decision") => void;
}) {
  if (!claims.length) {
    return <InspectorEmpty icon={<ShieldCheck size={22} />} text="经过 Critic 验证的结论会显示在这里" />;
  }
  const actionable = Boolean(
    run && run.status === "COMPLETED" && run.workspace_context?.workspace_id && onClaimAction,
  );
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
          {actionable && (
            <div className="claim-actions">
              <button type="button" onClick={() => onClaimAction?.(claim, index, "task")}>
                <ListTodo size={13} />转为任务
              </button>
              <button type="button" onClick={() => onClaimAction?.(claim, index, "decision")}>
                <BookCheck size={13} />记录决策
              </button>
            </div>
          )}
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
    ANALYTICS: "经营分析", OPERATION: "运维分析", SUPPORT: "支持诊断",
  };
  return values[route] ?? route;
}

function stepIcon(status: string) {
  if (status === "COMPLETED") return <Check size={14} />;
  if (status === "FAILED") return <CircleAlert size={14} />;
  if (status === "RUNNING") return <i className="step-spinner" />;
  return <Circle size={12} />;
}
