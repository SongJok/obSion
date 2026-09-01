"use client";

import {
  AlertTriangle,
  Check,
  ChevronRight,
  CircleDot,
  GitPullRequest,
  History,
  LoaderCircle,
  LockKeyhole,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  TicketCheck,
  Undo2,
  X,
  XCircle,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import type {
  ActionApproval,
  ActionDetail,
  ActionRequest,
  ActionStatus,
  ActionType,
  Workspace,
} from "@/lib/types";

const ACTIVE = new Set<ActionStatus>([
  "APPROVED",
  "EXECUTING",
  "ROLLBACK_APPROVED",
  "ROLLING_BACK",
]);
const ROLLBACKABLE = new Set<ActionStatus>([
  "COMPLETED",
  "FAILED",
  "ROLLBACK_FAILED",
  "ROLLBACK_REJECTED",
]);

export function ActionsView({ workspace }: { workspace?: Workspace }) {
  const [actions, setActions] = useState<ActionRequest[]>([]);
  const [approvals, setApprovals] = useState<ActionApproval[]>([]);
  const [selectedId, setSelectedId] = useState<string>();
  const [detail, setDetail] = useState<ActionDetail>();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [decision, setDecision] = useState<ActionApproval>();
  const [decisionReason, setDecisionReason] = useState("");
  const [rollbackOpen, setRollbackOpen] = useState(false);
  const [rollbackReason, setRollbackReason] = useState("");
  const [preflightOpen, setPreflightOpen] = useState(false);
  const [preflightReason, setPreflightReason] = useState("");

  const loadOverview = useCallback(async () => {
    if (!workspace) return;
    try {
      const [nextActions, nextApprovals] = await Promise.all([
        api.actions.list(workspace.id),
        api.actions.approvals(),
      ]);
      setActions(nextActions);
      setApprovals(nextApprovals);
      setSelectedId((current) =>
        current && nextActions.some((item) => item.id === current)
          ? current
          : nextActions[0]?.id,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取受控动作");
    } finally {
      setLoading(false);
    }
  }, [workspace]);

  const loadDetail = useCallback(async (actionId: string) => {
    try {
      const next = await api.actions.get(actionId);
      setDetail(next);
      setActions((items) =>
        items.map((item) => (item.id === next.action.id ? next.action : item)),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取动作详情");
    }
  }, []);

  useEffect(() => {
    if (!workspace) return;
    let active = true;
    Promise.all([api.actions.list(workspace.id), api.actions.approvals()])
      .then(([nextActions, nextApprovals]) => {
        if (!active) return;
        setActions(nextActions);
        setApprovals(nextApprovals);
        setSelectedId(nextActions[0]?.id);
      })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof Error ? caught.message : "无法读取受控动作");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [workspace]);

  useEffect(() => {
    if (!selectedId) return;
    let active = true;
    api.actions.get(selectedId)
      .then((next) => {
        if (!active) return;
        setDetail(next);
        setActions((items) => items.map((item) => item.id === next.action.id ? next.action : item));
      })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof Error ? caught.message : "无法读取动作详情");
      });
    return () => { active = false; };
  }, [selectedId]);

  useEffect(() => {
    const status = detail?.action.status;
    if (!selectedId || !status || !ACTIVE.has(status)) return;
    const timer = window.setInterval(() => void loadDetail(selectedId), 900);
    return () => window.clearInterval(timer);
  }, [detail?.action.status, loadDetail, selectedId]);

  const act = useCallback(async (operation: () => Promise<void>) => {
    setSaving(true);
    setError("");
    try {
      await operation();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "操作未能完成");
    } finally {
      setSaving(false);
    }
  }, []);

  const runPreflight = () => {
    if (!detail || preflightReason.trim().length < 10) return;
    const declaration = preflightReason.trim();
    void act(async () => {
      const checked = await api.actions.preflight(detail.action.id, declaration);
      setPreflightOpen(false);
      setPreflightReason("");
      setDetail(checked);
      await loadOverview();
    });
  };

  const decideApproval = (approve: boolean) => {
    if (!decision || decisionReason.trim().length < 3) return;
    void act(async () => {
      await api.actions.decide(decision.id, approve, decisionReason.trim());
      setDecision(undefined);
      setDecisionReason("");
      if (selectedId) await loadDetail(selectedId);
      await loadOverview();
    });
  };

  const requestRollback = () => {
    if (!detail || rollbackReason.trim().length < 10) return;
    void act(async () => {
      await api.actions.rollback(detail.action.id, rollbackReason.trim());
      setRollbackOpen(false);
      setRollbackReason("");
      await loadDetail(detail.action.id);
      await loadOverview();
    });
  };

  const cancel = () => {
    if (!detail) return;
    void act(async () => {
      await api.actions.cancel(detail.action.id);
      await loadDetail(detail.action.id);
      await loadOverview();
    });
  };

  const pendingApprovals = approvals.filter((item) => item.status === "PENDING").length;
  const completed = actions.filter((item) => item.status === "COMPLETED").length;
  const rolledBack = actions.filter((item) => item.status === "ROLLED_BACK").length;

  if (!workspace) {
    return (
      <main className="feature-page actions-page">
        <ActionEmpty title="请先选择工作空间" body="动作计划、审批和审计记录都归属于工作空间。" />
      </main>
    );
  }

  return (
    <main className="feature-page actions-page">
      <header className="feature-header action-header">
        <div>
          <span className="eyebrow">GOVERNED CHANGE CONTROL</span>
          <h1>受控动作</h1>
          <p>把调查结论转成真实变更。每个动作先预检、再由他人审批，并固定执行与回滚计划。</p>
        </div>
        <div className="automation-header-actions">
          <button className="secondary-button" onClick={() => void loadOverview()} disabled={loading}>
            <RefreshCw size={14} className={loading ? "spin" : ""} />刷新
          </button>
          <button className="primary-button" onClick={() => setCreateOpen(true)}>
            <Plus size={15} />发起动作
          </button>
        </div>
      </header>

      <div className="action-boundary">
        <LockKeyhole size={17} />
        <div><strong>第一阶段安全边界</strong><span>仅开放开发与预发环境的创建 PR、创建工单。配置修改、服务重启、部署和全部生产操作保持硬禁用。</span></div>
      </div>

      {error && <div className="notice error automation-notice"><XCircle size={15} />{error}<button onClick={() => setError("")}><X size={14} /></button></div>}

      <section className="automation-stats action-stats" aria-label="动作概览">
        <article><span><History size={17} /></span><div><strong>{actions.length}</strong><small>动作记录</small></div></article>
        <article className={pendingApprovals ? "has-unread" : ""}><span><ShieldCheck size={17} /></span><div><strong>{pendingApprovals}</strong><small>待我审批</small></div></article>
        <article><span><Check size={17} /></span><div><strong>{completed}</strong><small>执行完成</small></div></article>
        <article><span><Undo2 size={17} /></span><div><strong>{rolledBack}</strong><small>已回滚</small></div></article>
      </section>

      {loading ? (
        <div className="automation-loading"><LoaderCircle className="spin" size={22} />正在同步动作记录…</div>
      ) : actions.length === 0 ? (
        <ActionEmpty title="发起第一个受控动作" body="从创建 PR 或工单开始。没有真实连接器时，预检会安全失败，不会产生模拟成功。" onCreate={() => setCreateOpen(true)} />
      ) : (
        <section className="automation-layout action-layout">
          <aside className="workflow-list-panel action-list-panel">
            <header><div><strong>动作记录</strong><small>{workspace.name}</small></div><button className="icon-button" onClick={() => setCreateOpen(true)}><Plus size={16} /></button></header>
            <div className="workflow-list action-list">
              {actions.map((item) => (
                <button key={item.id} className={item.id === selectedId ? "workflow-card selected" : "workflow-card"} onClick={() => setSelectedId(item.id)}>
                  <span className={`workflow-state action-${item.action_type.toLowerCase()}`}>{item.action_type === "GENERATE_PR" ? <GitPullRequest size={15} /> : <TicketCheck size={15} />}</span>
                  <span><strong>{item.title}</strong><small>{actionTypeLabel(item.action_type)} · {environmentLabel(item.environment)}</small><em><ActionStatusBadge status={item.status} /></em></span>
                  <ChevronRight size={15} />
                </button>
              ))}
            </div>
          </aside>

          <section className="workflow-detail-panel action-detail-panel">
            {detail && <ActionDetailPanel detail={detail} saving={saving} onPreflight={() => { setPreflightOpen(true); setPreflightReason(""); }} onCancel={cancel} onReview={(approval) => { setDecision(approval); setDecisionReason(""); }} onRollback={() => { setRollbackOpen(true); setRollbackReason(""); }} />}
          </section>
        </section>
      )}

      {createOpen && <CreateActionModal workspace={workspace} saving={saving} onClose={() => setCreateOpen(false)} onCreate={(definition) => void act(async () => { const created = await api.actions.create(workspace.id, definition); setCreateOpen(false); setActions((items) => [created, ...items.filter((item) => item.id !== created.id)]); setSelectedId(created.id); await loadDetail(created.id); })} />}
      {decision && <DecisionModal approval={decision} reason={decisionReason} saving={saving} onReason={setDecisionReason} onClose={() => setDecision(undefined)} onDecision={decideApproval} />}
      {rollbackOpen && <ReasonModal title="申请回滚" body="回滚也需要独立审批，并调用计划中已经固定的补偿能力。" reason={rollbackReason} saving={saving} onReason={setRollbackReason} onClose={() => setRollbackOpen(false)} onSubmit={requestRollback} />}
      {preflightOpen && (
        <ReasonModal
          title="预检并提交审批"
          body="预检会真实核对连接器、权限与回滚能力。请亲自填写你已完成的核对内容，该声明会进入审批与审计记录。"
          label="核对声明"
          placeholder="例如：已核对目标仓库与分支、变更内容、影响范围和补偿方案，确认无误，申请独立审批。"
          submitLabel="预检并提交"
          reason={preflightReason}
          saving={saving}
          onReason={setPreflightReason}
          onClose={() => setPreflightOpen(false)}
          onSubmit={runPreflight}
        />
      )}
    </main>
  );
}

function ActionDetailPanel({ detail, saving, onPreflight, onCancel, onReview, onRollback }: { detail: ActionDetail; saving: boolean; onPreflight: () => void; onCancel: () => void; onReview: (approval: ActionApproval) => void; onRollback: () => void }) {
  const action = detail.action;
  const pending = detail.approvals.find((item) => item.status === "PENDING");
  const executeRef = detail.plan?.spec.execute as Record<string, unknown> | undefined;
  const rollbackRef = detail.plan?.spec.rollback as Record<string, unknown> | undefined;
  const canCancel = new Set<ActionStatus>(["DRAFT", "PREFLIGHT_FAILED", "WAITING_APPROVAL", "APPROVED", "WAITING_ROLLBACK_APPROVAL", "ROLLBACK_APPROVED"]).has(action.status);
  return (
    <>
      <header className="workflow-detail-header action-detail-header">
        <div><span className="eyebrow">{actionTypeLabel(action.action_type)} · {environmentLabel(action.environment)}</span><h2>{action.title}</h2><p>{action.description || targetLabel(action)}</p></div>
        <div className="workflow-actions">
          {(action.status === "DRAFT" || action.status === "PREFLIGHT_FAILED") && <button className="primary-button" onClick={onPreflight} disabled={saving}><ShieldCheck size={14} />预检并提交</button>}
          {pending && <button className="primary-button" onClick={() => onReview(pending)} disabled={saving}><ShieldCheck size={14} />进行审批</button>}
          {ROLLBACKABLE.has(action.status) && <button className="secondary-button" onClick={onRollback} disabled={saving}><Undo2 size={14} />申请回滚</button>}
          {canCancel && <button className="secondary-button danger-button" onClick={onCancel} disabled={saving}>取消</button>}
        </div>
      </header>

      <div className="workflow-policy-strip action-policy-strip">
        <span><ActionStatusBadge status={action.status} /></span>
        <span>风险等级 <strong>L3</strong></span>
        <span>目标 <strong>{targetLabel(action)}</strong></span>
        <span>回滚 <strong>{detail.plan ? "已固定" : "待预检"}</strong></span>
      </div>

      {action.error_message && <div className="execution-error action-error"><AlertTriangle size={16} /><span><strong>{action.error_code}</strong>{friendlyError(action.error_code, action.error_message)}</span></div>}

      <div className="action-detail-grid">
        <section className="automation-card action-plan-card">
          <header><div><LockKeyhole size={16} /><strong>封存计划</strong></div><span>{detail.plan ? "不可修改" : "尚未生成"}</span></header>
          {detail.plan ? <div className="action-plan-body">
            <PlanRow label="执行能力" value={String(executeRef?.capability_name ?? "—")} />
            <PlanRow label="回滚能力" value={String(rollbackRef?.capability_name ?? "—")} />
            <PlanRow label="计划指纹" value={detail.plan.checksum_sha256.slice(0, 16)} mono />
            <PlanRow label="执行超时" value={`${Math.round(action.timeout_seconds / 60)} 分钟`} />
          </div> : <div className="card-empty"><LockKeyhole size={19} /><span>等待预检</span><small>真实连接器、权限和回滚能力通过检查后才会封存。</small></div>}
        </section>

        <section className="automation-card action-approval-card">
          <header><div><ShieldCheck size={16} /><strong>审批记录</strong></div><span>{detail.approvals.length}</span></header>
          <div className="action-approval-list">
            {detail.approvals.map((approval) => <article key={approval.id}><span className={`approval-mark ${approval.status.toLowerCase()}`}>{approval.status === "APPROVED" ? <Check size={12} /> : <CircleDot size={12} />}</span><div><strong>{approval.purpose === "EXECUTE" ? "执行审批" : "回滚审批"} · 第 {approval.revision} 次</strong><small>{approval.status === "PENDING" ? `截止 ${formatDate(approval.expires_at)}` : approval.decision_reason || approval.status}</small></div>{approval.status === "PENDING" && <button onClick={() => onReview(approval)}>审核</button>}</article>)}
            {!detail.approvals.length && <div className="card-empty compact"><ShieldCheck size={18} /><span>预检后创建审批</span></div>}
          </div>
        </section>
      </div>

      <section className="automation-card action-history-card">
        <header><div><History size={16} /><strong>执行与回滚</strong></div><span>{detail.attempts.length}</span></header>
        <div className="action-attempts">
          {detail.attempts.map((attempt) => <article key={attempt.id}><span className={`attempt-icon ${attempt.status.toLowerCase()}`}>{attempt.status === "COMPLETED" ? <Check size={13} /> : attempt.status === "FAILED" ? <X size={13} /> : <LoaderCircle className={attempt.status === "RUNNING" ? "spin" : ""} size={13} />}</span><div><strong>{attempt.purpose === "EXECUTE" ? "执行动作" : "执行回滚"}</strong><small>幂等键 {attempt.idempotency_key.slice(-18)}</small></div><ActionAttemptStatus status={attempt.status} /></article>)}
          {!detail.attempts.length && <div className="card-empty"><History size={19} /><span>还没有调用外部系统</span><small>动作获批后，Worker 会按封存计划调用一次真实能力。</small></div>}
        </div>
      </section>
    </>
  );
}

function CreateActionModal({ workspace, saving, onClose, onCreate }: { workspace: Workspace; saving: boolean; onClose: () => void; onCreate: (definition: Record<string, unknown>) => void }) {
  const [type, setType] = useState<ActionType>("GENERATE_PR");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [environment, setEnvironment] = useState("development");
  const [target, setTarget] = useState("");
  const [summary, setSummary] = useState("");
  const [detail, setDetail] = useState("");
  const [secondary, setSecondary] = useState("");
  const idempotency = useMemo(() => `web-action-${crypto.randomUUID()}`, []);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!title.trim() || !target.trim() || !summary.trim() || !detail.trim()) return;
    const definition = type === "GENERATE_PR" ? {
      action_type: type, title: title.trim(), description: description.trim(), environment,
      target: { repository: target.trim() },
      parameters: { title: summary.trim(), head: detail.trim(), base: secondary.trim() || "main" },
      rollback_parameters: { reason: "Obsion governed rollback" },
      idempotency_key: idempotency,
    } : {
      action_type: type, title: title.trim(), description: description.trim(), environment,
      target: { project_key: target.trim() },
      parameters: { summary: summary.trim(), description: detail.trim(), issue_type: secondary.trim() || "Task" },
      rollback_parameters: { resolution: "Cancelled by Obsion governed rollback" },
      idempotency_key: idempotency,
    };
    onCreate(definition);
  };
  return <div className="modal-backdrop" role="presentation"><form className="workspace-modal automation-modal action-modal" onSubmit={submit}><header><span className="modal-icon"><ShieldCheck size={19} /></span><div><h2>发起受控动作</h2><p>{workspace.name} · 先保存草稿，再预检真实连接器并提交他人审批</p></div><button type="button" className="icon-button" onClick={onClose}><X size={18} /></button></header><div className="action-type-picker"><button type="button" className={type === "GENERATE_PR" ? "active" : ""} onClick={() => setType("GENERATE_PR")}><GitPullRequest size={17} /><span><strong>创建 PR</strong><small>提交已有分支</small></span></button><button type="button" className={type === "CREATE_TICKET" ? "active" : ""} onClick={() => setType("CREATE_TICKET")}><TicketCheck size={17} /><span><strong>创建工单</strong><small>记录跟进事项</small></span></button></div><label><span>动作名称</span><input autoFocus value={title} onChange={(event) => setTitle(event.target.value)} placeholder={type === "GENERATE_PR" ? "例如：提交支付超时修复" : "例如：建立支付故障跟进工单"} /></label><label><span>说明</span><input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="说明依据、影响范围和期望结果" /></label><div className="automation-form-grid"><label><span>环境</span><select value={environment} onChange={(event) => setEnvironment(event.target.value)}><option value="development">开发环境</option><option value="staging">预发环境</option></select></label><label><span>{type === "GENERATE_PR" ? "代码仓库" : "项目标识"}</span><input value={target} onChange={(event) => setTarget(event.target.value)} placeholder={type === "GENERATE_PR" ? "organization/repository" : "例如：OPS"} /></label></div><label><span>{type === "GENERATE_PR" ? "PR 标题" : "工单标题"}</span><input value={summary} onChange={(event) => setSummary(event.target.value)} /></label><div className="automation-form-grid"><label><span>{type === "GENERATE_PR" ? "来源分支" : "工单说明"}</span><input value={detail} onChange={(event) => setDetail(event.target.value)} placeholder={type === "GENERATE_PR" ? "fix/payment-timeout" : "描述需要跟进的问题"} /></label><label><span>{type === "GENERATE_PR" ? "目标分支" : "工单类型"}</span><input value={secondary} onChange={(event) => setSecondary(event.target.value)} placeholder={type === "GENERATE_PR" ? "main" : "Task"} /></label></div><div className="automation-form-note"><LockKeyhole size={15} /><span>表单不会接收 Token 或密码。凭据由连接器在受信边界内解析，动作内容不会进入模型上下文。</span></div><footer><button type="button" className="secondary-button" onClick={onClose}>取消</button><button className="primary-button" disabled={saving || !title.trim() || !target.trim() || !summary.trim() || !detail.trim()}>{saving ? "正在保存…" : "保存动作草稿"}</button></footer></form></div>;
}

function DecisionModal({ approval, reason, saving, onReason, onClose, onDecision }: { approval: ActionApproval; reason: string; saving: boolean; onReason: (value: string) => void; onClose: () => void; onDecision: (approve: boolean) => void }) {
  return <div className="modal-backdrop" role="presentation"><div className="workspace-modal review-modal"><header><span className="modal-icon"><ShieldCheck size={19} /></span><div><h2>{approval.purpose === "EXECUTE" ? "审批动作执行" : "审批动作回滚"}</h2><p>申请人不能审批自己的动作；你的决定会绑定当前计划指纹</p></div><button className="icon-button" onClick={onClose}><X size={18} /></button></header><div className="approval-reason"><strong>申请理由</strong><p>{approval.reason}</p></div><label><span>审批意见</span><textarea autoFocus rows={4} value={reason} onChange={(event) => onReason(event.target.value)} placeholder="说明核对了哪些目标、影响和回滚条件。" /></label><footer><button className="secondary-button danger-button" onClick={() => onDecision(false)} disabled={saving || reason.trim().length < 3}>拒绝</button><button className="primary-button" onClick={() => onDecision(true)} disabled={saving || reason.trim().length < 3}>批准</button></footer></div></div>;
}

function ReasonModal({ title, body, label = "回滚原因", placeholder = "说明为什么需要撤销原动作，以及已经确认的影响。", submitLabel = "提交回滚审批", reason, saving, onReason, onClose, onSubmit }: { title: string; body: string; label?: string; placeholder?: string; submitLabel?: string; reason: string; saving: boolean; onReason: (value: string) => void; onClose: () => void; onSubmit: () => void }) { return <div className="modal-backdrop"><div className="workspace-modal review-modal"><header><span className="modal-icon"><RotateCcw size={19} /></span><div><h2>{title}</h2><p>{body}</p></div><button className="icon-button" onClick={onClose}><X size={18} /></button></header><label><span>{label}</span><textarea autoFocus rows={4} value={reason} onChange={(event) => onReason(event.target.value)} placeholder={placeholder} /></label><footer><button className="secondary-button" onClick={onClose}>取消</button><button className="primary-button" onClick={onSubmit} disabled={saving || reason.trim().length < 10}>{submitLabel}</button></footer></div></div>; }

function ActionStatusBadge({ status }: { status: ActionStatus }) { const running = ACTIVE.has(status); return <span className={`action-status ${status.toLowerCase()}`}>{running && <LoaderCircle className="spin" size={10} />}{status === "COMPLETED" || status === "ROLLED_BACK" ? <Check size={10} /> : null}{actionStatusLabel(status)}</span>; }
function ActionAttemptStatus({ status }: { status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" }) { return <span className={`action-status ${status.toLowerCase()}`}>{status === "RUNNING" && <LoaderCircle className="spin" size={10} />}{({ PENDING: "等待", RUNNING: "调用中", COMPLETED: "已完成", FAILED: "失败" } as const)[status]}</span>; }
function PlanRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) { return <div><span>{label}</span><strong className={mono ? "mono" : ""}>{value}</strong></div>; }
function ActionEmpty({ title, body, onCreate }: { title: string; body: string; onCreate?: () => void }) { return <div className="automation-empty"><span><ShieldCheck size={24} /></span><h2>{title}</h2><p>{body}</p>{onCreate && <button className="primary-button" onClick={onCreate}><Plus size={15} />发起受控动作</button>}</div>; }
function actionTypeLabel(type: ActionType) { return ({ GENERATE_PR: "创建 PR", CREATE_TICKET: "创建工单", MODIFY_CONFIG: "修改配置", RESTART_SERVICE: "重启服务", DEPLOY: "部署" } as const)[type]; }
function actionStatusLabel(status: ActionStatus) { return ({ DRAFT: "草稿", PREFLIGHT_FAILED: "预检未通过", WAITING_APPROVAL: "等待执行审批", APPROVED: "已批准", EXECUTING: "执行中", COMPLETED: "已完成", FAILED: "执行失败", WAITING_ROLLBACK_APPROVAL: "等待回滚审批", ROLLBACK_APPROVED: "回滚已批准", ROLLING_BACK: "回滚中", ROLLED_BACK: "已回滚", ROLLBACK_FAILED: "回滚失败", ROLLBACK_REJECTED: "回滚被拒绝", REJECTED: "执行被拒绝", EXPIRED: "审批已过期", CANCELLED: "已取消" } as const)[status]; }
function environmentLabel(value: string) { return value === "development" ? "开发环境" : value === "staging" ? "预发环境" : "生产环境"; }
function targetLabel(action: ActionRequest) { return String(action.target.repository ?? action.target.project_key ?? action.target.service ?? "已定义目标"); }
function friendlyError(code: string | null, fallback: string) { if (code === "resource_not_found") return "没有找到匹配的真实动作连接器。请先由管理员配置并启用对应能力。"; if (code === "v1_production_action_boundary") return "第一阶段禁止全部生产动作。"; if (code === "v1_action_type_boundary") return "该动作类型将在后续阶段开放，目前不能提交审批。"; return fallback; }
function formatDate(value: string) { return new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
