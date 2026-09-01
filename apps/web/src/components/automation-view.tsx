"use client";

import {
  Archive,
  Bell,
  Braces,
  CalendarClock,
  CalendarPlus,
  Check,
  ChevronRight,
  CirclePlay,
  Clock3,
  Eye,
  FileBox,
  GitBranch,
  History,
  LoaderCircle,
  Pause,
  PencilLine,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Square,
  X,
  XCircle,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import {
  CRON_PRESETS,
  DEFAULT_REVIEW_INSTRUCTIONS,
  buildSpecFromDraft,
  cronLabel,
  draftFromSpec,
  outputRefLabel,
  parseInputPayload,
  parseWorkflowSpec,
  sortedVersions,
  versionStepSummary,
  buildSchedulePayload,
  type AuthoringDraft,
} from "@/lib/automation-authoring";
import type {
  AutomationExecution,
  AutomationStatus,
  AutomationStep,
  NotificationDelivery,
  Workflow,
  WorkflowSchedule,
  WorkflowSpec,
  WorkflowVersion,
  Workspace,
} from "@/lib/types";

const ACTIVE_EXECUTIONS = new Set<AutomationStatus>(["PENDING", "RUNNING"]);

export function AutomationView({ workspace, onOpenRun }: { workspace?: Workspace; onOpenRun?: (runId: string) => void }) {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [selectedId, setSelectedId] = useState<string>();
  const [schedules, setSchedules] = useState<WorkflowSchedule[]>([]);
  const [executions, setExecutions] = useState<AutomationExecution[]>([]);
  const [execution, setExecution] = useState<AutomationExecution>();
  const [notifications, setNotifications] = useState<NotificationDelivery[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [reviewStep, setReviewStep] = useState<AutomationStep>();
  const [reviewReason, setReviewReason] = useState("");
  const [versions, setVersions] = useState<WorkflowVersion[]>([]);
  const [notice, setNotice] = useState("");
  const [specView, setSpecView] = useState<WorkflowVersion>();
  const [authorBase, setAuthorBase] = useState<WorkflowVersion>();
  const [triggerOpen, setTriggerOpen] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [retireArmed, setRetireArmed] = useState(false);

  const selected = workflows.find((item) => item.id === selectedId);

  const loadOverview = useCallback(async () => {
    if (!workspace) return;
    try {
      const [nextWorkflows, nextNotifications] = await Promise.all([
        api.automation.listWorkflows(workspace.id),
        api.automation.listNotifications(),
      ]);
      setWorkflows(nextWorkflows);
      setNotifications(nextNotifications);
      setSelectedId((current) =>
        current && nextWorkflows.some((item) => item.id === current)
          ? current
          : nextWorkflows[0]?.id,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取自动化工作区");
    } finally {
      setLoading(false);
    }
  }, [workspace]);

  const loadWorkflow = useCallback(async (workflowId: string) => {
    try {
      const [nextSchedules, nextExecutions, nextVersions] = await Promise.all([
        api.automation.listSchedules(workflowId),
        api.automation.listExecutions(workflowId),
        api.automation.listVersions(workflowId),
      ]);
      setSchedules(nextSchedules);
      setExecutions(nextExecutions);
      setVersions(nextVersions);
      setExecution((current) => {
        if (!current || current.workflow_id !== workflowId) return undefined;
        const latest = nextExecutions.find((item) => item.id === current.id);
        return latest ? { ...current, ...latest } : undefined;
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取工作流运行记录");
    }
  }, []);

  useEffect(() => {
    let active = true;
    if (!workspace) return;
    Promise.all([
      api.automation.listWorkflows(workspace.id),
      api.automation.listNotifications(),
    ])
      .then(([nextWorkflows, nextNotifications]) => {
        if (!active) return;
        setWorkflows(nextWorkflows);
        setNotifications(nextNotifications);
        setSelectedId(nextWorkflows[0]?.id);
      })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof Error ? caught.message : "无法读取自动化工作区");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [workspace]);

  useEffect(() => {
    let active = true;
    if (!selectedId) return;
    Promise.all([
      api.automation.listSchedules(selectedId),
      api.automation.listExecutions(selectedId),
      api.automation.listVersions(selectedId),
    ])
      .then(([nextSchedules, nextExecutions, nextVersions]) => {
        if (!active) return;
        setSchedules(nextSchedules);
        setExecutions(nextExecutions);
        setVersions(nextVersions);
      })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof Error ? caught.message : "无法读取工作流运行记录");
      });
    return () => {
      active = false;
    };
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId || !executions.some((item) => ACTIVE_EXECUTIONS.has(item.status))) return;
    const timer = window.setInterval(() => void loadWorkflow(selectedId), 1200);
    return () => window.clearInterval(timer);
  }, [executions, loadWorkflow, selectedId]);

  const openExecution = useCallback(async (executionId: string) => {
    setError("");
    try {
      setExecution(await api.automation.getExecution(executionId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取运行详情");
    }
  }, []);

  const act = useCallback(async (operation: () => Promise<void>) => {
    setSaving(true);
    setError("");
    setNotice("");
    try {
      await operation();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "操作未能完成");
    } finally {
      setSaving(false);
    }
  }, []);

  const publishLatest = () => {
    if (!selected) return;
    const latest = sortedVersions(versions)[0];
    if (!latest) {
      setError("工作流还没有可发布版本");
      return;
    }
    publishVersion(latest.version);
  };

  const publishVersion = (version: number) => {
    if (!selected) return;
    void act(async () => {
      await api.automation.publishVersion(selected.id, version);
      setNotice(`版本 v${version} 已发布为当前版本`);
      await loadOverview();
      await loadWorkflow(selected.id);
    });
  };

  const createVersion = (spec: WorkflowSpec) => {
    if (!selected) return;
    void act(async () => {
      const created = await api.automation.createVersion(
        selected.id,
        spec as unknown as Record<string, unknown>,
      );
      setAuthorBase(undefined);
      setNotice(`版本 v${created.version} 已创建，可在版本列表中发布`);
      await loadWorkflow(selected.id);
    });
  };

  const changeStatus = (action: "pause" | "activate" | "retire") => {
    if (!selected) return;
    void act(async () => {
      await api.automation.setStatus(selected.id, action);
      await loadOverview();
      await loadWorkflow(selected.id);
    });
  };

  const trigger = (payload: Record<string, unknown>) => {
    if (!selected) return;
    void act(async () => {
      const created = await api.automation.trigger(selected.id, payload);
      setTriggerOpen(false);
      await loadWorkflow(selected.id);
      await openExecution(created.id);
    });
  };

  const createSchedule = (payload: Record<string, unknown>) => {
    if (!selected) return;
    void act(async () => {
      await api.automation.createSchedule(selected.id, payload);
      setScheduleOpen(false);
      setNotice(`计划「${String(payload.name ?? "")}」已创建`);
      await loadWorkflow(selected.id);
    });
  };

  const cancelExecution = () => {
    if (!execution) return;
    void act(async () => {
      const cancelled = await api.automation.cancelExecution(execution.id);
      setExecution((current) => current ? { ...current, ...cancelled } : current);
      if (selectedId) await loadWorkflow(selectedId);
    });
  };

  const decideReview = (decision: "APPROVE" | "REJECT") => {
    if (!reviewStep || !reviewReason.trim()) return;
    void act(async () => {
      await api.automation.reviewStep(reviewStep.id, decision, reviewReason.trim());
      const refreshed = await api.automation.getExecution(reviewStep.execution_id);
      setExecution(refreshed);
      setReviewStep(undefined);
      setReviewReason("");
      if (selectedId) await loadWorkflow(selectedId);
    });
  };

  const unread = notifications.filter((item) => item.status === "DELIVERED").length;
  const activeCount = workflows.filter((item) => item.status === "ACTIVE").length;
  const enabledSchedules = schedules.filter((item) => item.enabled).length;

  if (!workspace) {
    return <div className="feature-page automation-page"><EmptyAutomation title="请先选择工作空间" body="自动化定义、权限和运行记录都归属于工作空间。" /></div>;
  }

  return (
    <main className="feature-page automation-page">
      <header className="feature-header automation-header">
        <div>
          <span className="eyebrow">RECURRING INTELLIGENCE</span>
          <h1>自动化</h1>
          <p>把周期分析、人工确认与通知编排成可审计的工作流。每次运行都沿用当前责任人的权限，并保留证据与产物链路。</p>
        </div>
        <div className="automation-header-actions">
          <button className="secondary-button" onClick={() => void loadOverview()} disabled={loading}>
            <RefreshCw size={14} className={loading ? "spin" : ""} />刷新
          </button>
          <button className="primary-button" onClick={() => setCreateOpen(true)}>
            <Plus size={15} />新建自动化
          </button>
        </div>
      </header>

      {error && <div className="notice error automation-notice"><XCircle size={15} />{error}<button onClick={() => setError("")}><X size={14} /></button></div>}
      {notice && <div className="notice automation-notice success"><Check size={15} />{notice}<button onClick={() => setNotice("")}><X size={14} /></button></div>}

      <section className="automation-stats" aria-label="自动化概览">
        <article><span><GitBranch size={17} /></span><div><strong>{workflows.length}</strong><small>工作流</small></div></article>
        <article><span><CirclePlay size={17} /></span><div><strong>{activeCount}</strong><small>已启用</small></div></article>
        <article><span><CalendarClock size={17} /></span><div><strong>{enabledSchedules}</strong><small>当前定时</small></div></article>
        <article className={unread ? "has-unread" : ""}><span><Bell size={17} /></span><div><strong>{unread}</strong><small>未读通知</small></div></article>
      </section>

      {loading ? (
        <div className="automation-loading"><LoaderCircle className="spin" size={22} />正在同步工作流…</div>
      ) : workflows.length === 0 ? (
        <EmptyAutomation title="建立第一个周期分析" body="从目标开始，Obsion 会创建不可变版本，并在发布后按计划可靠执行。" onCreate={() => setCreateOpen(true)} />
      ) : (
        <section className="automation-layout">
          <aside className="workflow-list-panel">
            <header><div><strong>工作流</strong><small>{workspace.name}</small></div><button className="icon-button" onClick={() => setCreateOpen(true)} aria-label="新建工作流"><Plus size={16} /></button></header>
            <div className="workflow-list">
              {workflows.map((item) => (
                <button key={item.id} className={item.id === selectedId ? "workflow-card selected" : "workflow-card"} onClick={() => { setExecution(undefined); setRetireArmed(false); setSelectedId(item.id); }}>
                  <span className={`workflow-state ${item.status.toLowerCase()}`}><GitBranch size={15} /></span>
                  <span><strong>{item.display_name}</strong><small>{item.description || "暂无说明"}</small><em><StatusDot status={item.status} />{workflowStatusLabel(item.status)} · v{item.active_version ?? "草稿"}</em></span>
                  <ChevronRight size={15} />
                </button>
              ))}
            </div>
          </aside>

          <section className="workflow-detail-panel">
            {selected && (
              <>
                <header className="workflow-detail-header">
                  <div><span className="eyebrow">{selected.name}</span><h2>{selected.display_name}</h2><p>{selected.description || "这个工作流还没有说明。"}</p></div>
                  <div className="workflow-actions">
                    {selected.status === "DRAFT" && <button className="primary-button" onClick={publishLatest} disabled={saving}><CirclePlay size={14} />发布</button>}
                    {selected.status === "ACTIVE" && <><button className="secondary-button" onClick={() => changeStatus("pause")} disabled={saving}><Pause size={14} />暂停</button><button className="primary-button" onClick={() => setTriggerOpen(true)} disabled={saving}><Play size={14} />立即运行</button></>}
                    {selected.status === "PAUSED" && <button className="primary-button" onClick={() => changeStatus("activate")} disabled={saving}><RotateCcw size={14} />恢复</button>}
                    {selected.status === "PAUSED" && (retireArmed ? (
                      <button className="secondary-button danger-button" onClick={() => { setRetireArmed(false); changeStatus("retire"); }} disabled={saving}><Archive size={14} />确认退役</button>
                    ) : (
                      <button className="secondary-button" onClick={() => setRetireArmed(true)} disabled={saving}><Archive size={14} />退役</button>
                    ))}
                    {selected.status === "RETIRED" && <span className="workflow-retired-note"><Archive size={14} />已退役 · 运行记录与审计保留</span>}
                  </div>
                </header>

                <div className="workflow-policy-strip">
                  <span><ShieldCheck size={14} />责任人权限实时校验</span>
                  <span>并发策略 <strong>{concurrencyLabel(selected.concurrency_policy)}</strong></span>
                  <span>超时 <strong>{Math.round(selected.timeout_seconds / 60)} 分钟</strong></span>
                  <span>分类 <strong>{selected.classification}</strong></span>
                </div>

                <div className="workflow-detail-grid">
                  <section className="automation-card schedule-card">
                    <header><div><CalendarClock size={16} /><strong>运行计划</strong></div><span className="card-header-actions"><button className="icon-button" onClick={() => setScheduleOpen(true)} aria-label="添加运行计划"><CalendarPlus size={15} /></button><span>{schedules.length}</span></span></header>
                    {schedules.length ? schedules.map((schedule) => (
                      <div className="schedule-row" key={schedule.id}>
                        <span className={schedule.enabled ? "schedule-icon enabled" : "schedule-icon"}><Clock3 size={15} /></span>
                        <div><strong>{schedule.name}</strong><small>{cronLabel(schedule.cron_expression)} · {schedule.timezone}</small><em>{schedule.enabled ? `下次 ${formatDate(schedule.next_fire_at)}` : schedule.last_error_code ? `已停用 · ${schedule.last_error_code}` : "已停用"}</em></div>
                        <button className={schedule.enabled ? "toggle active" : "toggle"} onClick={() => void act(async () => { await api.automation.setScheduleEnabled(schedule.id, !schedule.enabled); await loadWorkflow(selected.id); })} disabled={saving} aria-label={schedule.enabled ? "停用计划" : "启用计划"}><i /></button>
                      </div>
                    )) : <div className="card-empty"><CalendarClock size={19} /><span>没有定时计划</span><small>点击右上角添加，或在新建自动化时选择常用周期。</small></div>}
                  </section>

                  <section className="automation-card versions-card">
                    <header><div><History size={16} /><strong>版本</strong></div><span>{versions.length}</span></header>
                    {sortedVersions(versions).map((version) => (
                      <div className="version-row" key={version.id}>
                        <span className="version-index">v{version.version}</span>
                        <div>
                          <strong>{versionStepSummary(parseWorkflowSpec(version.spec))}</strong>
                          <small>{formatDate(version.created_at)} · 指纹 {version.checksum_sha256.slice(0, 12)}</small>
                          <em>{version.version === selected.active_version ? "当前发布版本" : version.published_at ? `曾于 ${formatDate(version.published_at)} 发布` : "未发布"}</em>
                        </div>
                        <span className="version-actions">
                          <button className="icon-button" onClick={() => setSpecView(version)} aria-label={`查看版本 v${version.version} 定义`}><Eye size={14} /></button>
                          <button className="icon-button" onClick={() => setAuthorBase(version)} aria-label={`基于版本 v${version.version} 新建版本`}><PencilLine size={14} /></button>
                          {version.version !== selected.active_version && selected.status !== "RETIRED" && (
                            <button className="icon-button" onClick={() => publishVersion(version.version)} disabled={saving} aria-label={`发布版本 v${version.version}`}><CirclePlay size={14} /></button>
                          )}
                        </span>
                      </div>
                    ))}
                    {!versions.length && <div className="card-empty compact"><History size={18} /><span>还没有版本</span></div>}
                  </section>

                  <section className="automation-card inbox-card">
                    <header><div><Bell size={16} /><strong>通知收件箱</strong></div><span>{unread} 未读</span></header>
                    <div className="notification-list">
                      {notifications.slice(0, 4).map((item) => (
                        <button key={item.id} className={item.status === "DELIVERED" ? "notification-row unread" : "notification-row"} onClick={() => item.status === "DELIVERED" && void act(async () => { await api.automation.markNotificationRead(item.id); await loadOverview(); })}>
                          <i /><span><strong>{item.title}</strong><small>{item.body}</small></span><time>{formatRelative(item.delivered_at)}</time>
                        </button>
                      ))}
                      {!notifications.length && <div className="card-empty compact"><Bell size={18} /><span>还没有通知</span></div>}
                    </div>
                  </section>
                </div>

                <section className="automation-card execution-card">
                  <header><div><CirclePlay size={16} /><strong>最近运行</strong></div><span>{executions.length}</span></header>
                  <div className="execution-table" role="table">
                    <div className="execution-table-head" role="row"><span>状态</span><span>触发方式</span><span>开始时间</span><span>耗时</span><span /></div>
                    {executions.map((item) => (
                      <button key={item.id} className={execution?.id === item.id ? "execution-row selected" : "execution-row"} onClick={() => void openExecution(item.id)}>
                        <span><ExecutionStatus status={item.status} /></span><span>{triggerLabel(item.trigger)}</span><time>{formatDate(item.started_at ?? item.created_at)}</time><span>{durationLabel(item)}</span><ChevronRight size={14} />
                      </button>
                    ))}
                    {!executions.length && <div className="card-empty"><CirclePlay size={19} /><span>还没有运行记录</span><small>发布后点击“立即运行”进行首次验证。</small></div>}
                  </div>
                </section>

                {execution && <ExecutionDrawer execution={execution} saving={saving} onClose={() => setExecution(undefined)} onCancel={cancelExecution} onReview={(step) => { setReviewStep(step); setReviewReason(""); }} onOpenRun={onOpenRun} />}
              </>
            )}
          </section>
        </section>
      )}

      {createOpen && <CreateAutomationModal workspace={workspace} saving={saving} onClose={() => setCreateOpen(false)} onCreate={(definition) => void act(async () => { const created = await api.automation.createWorkflow(workspace.id, definition.payload); const published = await api.automation.publishVersion(created.workflow.id, 1); setCreateOpen(false); setWorkflows((items) => [published.workflow, ...items]); setSelectedId(published.workflow.id); if (definition.cron) await api.automation.createSchedule(created.workflow.id, { name: "默认计划", cron_expression: definition.cron, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", misfire_policy: "FIRE_ONCE", misfire_grace_seconds: 300, input_payload: {}, enabled: true }); await loadOverview(); await loadWorkflow(published.workflow.id); })} />}

      {reviewStep && <ReviewModal step={reviewStep} reason={reviewReason} saving={saving} onReason={setReviewReason} onClose={() => setReviewStep(undefined)} onDecision={decideReview} />}

      {specView && <SpecModal version={specView} onClose={() => setSpecView(undefined)} />}
      {authorBase && <AuthoringModal base={authorBase} workflowName={selected?.display_name ?? ""} saving={saving} onClose={() => setAuthorBase(undefined)} onCreate={createVersion} />}
      {triggerOpen && selected && <TriggerModal workflowName={selected.display_name} saving={saving} onClose={() => setTriggerOpen(false)} onTrigger={trigger} />}
      {scheduleOpen && selected && <ScheduleModal versions={versions} saving={saving} onClose={() => setScheduleOpen(false)} onCreate={createSchedule} />}
    </main>
  );
}

function SpecModal({ version, onClose }: { version: WorkflowVersion; onClose: () => void }) {
  const spec = parseWorkflowSpec(version.spec);
  return (
    <div className="modal-backdrop" role="presentation">
      <div className="workspace-modal automation-modal spec-modal">
        <header><span className="modal-icon"><History size={19} /></span><div><h2>版本 v{version.version} 定义</h2><p>不可变快照 · 指纹 {version.checksum_sha256.slice(0, 20)}</p></div><button type="button" className="icon-button" onClick={onClose}><X size={18} /></button></header>
        <div className="spec-step-list">
          {spec ? spec.steps.map((step, index) => (
            <article className="spec-step" key={step.id}>
              <span className="timeline-index">{index + 1}</span>
              <div>
                <header><strong>{step.name}</strong><code>{step.id}</code></header>
                <small>{stepTypeLabel(step.type)}{step.depends_on.length ? ` · 依赖 ${step.depends_on.join("、")}` : ""}</small>
                {step.prompt && <p>{step.prompt}</p>}
                {step.review_instructions && <p>确认要点：{step.review_instructions}{step.disallow_self_review ? "（禁止自审）" : ""}</p>}
                {step.title && <p>通知：{step.title} — {step.body}</p>}
              </div>
            </article>
          )) : <div className="card-empty"><History size={18} /><span>该版本的步骤定义无法解析</span></div>}
        </div>
        <footer><button type="button" className="primary-button" onClick={onClose}>关闭</button></footer>
      </div>
    </div>
  );
}

function AuthoringModal({ base, workflowName, saving, onClose, onCreate }: { base: WorkflowVersion; workflowName: string; saving: boolean; onClose: () => void; onCreate: (spec: WorkflowSpec) => void }) {
  const initial = useMemo(() => draftFromSpec(parseWorkflowSpec(base.spec) ?? { steps: [] }), [base]);
  const [draft, setDraft] = useState<AuthoringDraft>(initial);
  const patch = (changes: Partial<AuthoringDraft>) => setDraft((current) => ({ ...current, ...changes }));

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!draft.prompt.trim()) return;
    onCreate(buildSpecFromDraft(draft, workflowName));
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="workspace-modal automation-modal" onSubmit={submit}>
        <header><span className="modal-icon"><PencilLine size={19} /></span><div><h2>基于 v{base.version} 新建版本</h2><p>版本不可原地修改，保存后生成新的不可变版本</p></div><button type="button" className="icon-button" onClick={onClose}><X size={18} /></button></header>
        <label><span>分析目标</span><textarea autoFocus value={draft.prompt} onChange={(event) => patch({ prompt: event.target.value })} maxLength={100000} rows={5} placeholder="这次运行要回答什么业务问题？" /></label>
        <div className="automation-form-grid">
          <label className="review-option"><span>人工确认</span><button type="button" className={draft.review ? "choice active" : "choice"} onClick={() => patch({ review: !draft.review })}><ShieldCheck size={15} />{draft.review ? "通知前需要确认" : "无需人工确认"}</button></label>
          {draft.review && <label className="review-option"><span>自审限制</span><button type="button" className={draft.disallowSelfReview ? "choice active" : "choice"} onClick={() => patch({ disallowSelfReview: !draft.disallowSelfReview })}><ShieldCheck size={15} />{draft.disallowSelfReview ? "禁止责任人自审" : "允许责任人自审"}</button></label>}
        </div>
        {draft.review && <label><span>确认要点</span><textarea value={draft.reviewInstructions} onChange={(event) => patch({ reviewInstructions: event.target.value })} maxLength={20000} rows={2} placeholder={DEFAULT_REVIEW_INSTRUCTIONS} /></label>}
        <label><span>通知标题</span><input value={draft.notifyTitle} onChange={(event) => patch({ notifyTitle: event.target.value })} maxLength={300} placeholder={`${workflowName} 已完成`} /></label>
        <label><span>通知内容</span><textarea value={draft.notifyBody} onChange={(event) => patch({ notifyBody: event.target.value })} maxLength={20000} rows={2} placeholder="周期分析已完成，请查看运行详情中的证据与产物。" /></label>
        <footer><button type="button" className="secondary-button" onClick={onClose}>取消</button><button className="primary-button" disabled={saving || !draft.prompt.trim()}>{saving ? "正在保存…" : "保存为新版本"}</button></footer>
      </form>
    </div>
  );
}

function TriggerModal({ workflowName, saving, onClose, onTrigger }: { workflowName: string; saving: boolean; onClose: () => void; onTrigger: (payload: Record<string, unknown>) => void }) {
  const [text, setText] = useState("");
  const [formError, setFormError] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const result = parseInputPayload(text);
    if (!result.ok) {
      setFormError(result.message);
      return;
    }
    onTrigger(result.payload);
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="workspace-modal automation-modal" onSubmit={submit}>
        <header><span className="modal-icon"><Play size={19} /></span><div><h2>立即运行</h2><p>{workflowName} · 沿用当前责任人的权限与当前发布版本</p></div><button type="button" className="icon-button" onClick={onClose}><X size={18} /></button></header>
        <label><span>运行参数（JSON，可留空）</span><textarea autoFocus value={text} onChange={(event) => { setText(event.target.value); setFormError(""); }} rows={4} placeholder='{"day": "2026-09-01"}' /></label>
        {formError && <div className="notice error"><XCircle size={14} />{formError}</div>}
        <div className="automation-form-note"><Braces size={15} /><span>参数会进入运行记录与 Harness 输入；每次点击生成新的幂等键，重复提交不会重复执行。</span></div>
        <footer><button type="button" className="secondary-button" onClick={onClose}>取消</button><button className="primary-button" disabled={saving}>{saving ? "正在启动…" : "启动运行"}</button></footer>
      </form>
    </div>
  );
}

function ScheduleModal({ versions, saving, onClose, onCreate }: { versions: WorkflowVersion[]; saving: boolean; onClose: () => void; onCreate: (payload: Record<string, unknown>) => void }) {
  const [name, setName] = useState("");
  const [preset, setPreset] = useState("daily");
  const [customCron, setCustomCron] = useState("");
  const [misfire, setMisfire] = useState<"SKIP" | "FIRE_ONCE">("FIRE_ONCE");
  const [pin, setPin] = useState("active");
  const [payloadText, setPayloadText] = useState("");
  const [formError, setFormError] = useState("");
  const timezone = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC", []);
  const published = sortedVersions(versions).filter((version) => version.published_at);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const parsed = parseInputPayload(payloadText);
    if (!parsed.ok) {
      setFormError(parsed.message);
      return;
    }
    const cron = preset === "custom" ? customCron : CRON_PRESETS.find((item) => item.id === preset)?.cron ?? "";
    const result = buildSchedulePayload({
      name,
      cron,
      timezone,
      misfirePolicy: misfire,
      workflowVersion: pin === "active" ? null : Number(pin),
      inputPayload: parsed.payload,
    });
    if (!result.ok) {
      setFormError(result.message);
      return;
    }
    onCreate(result.payload);
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="workspace-modal automation-modal" onSubmit={submit}>
        <header><span className="modal-icon"><CalendarPlus size={19} /></span><div><h2>添加运行计划</h2><p>时区 {timezone} · 失火补跑策略可随时停用</p></div><button type="button" className="icon-button" onClick={onClose}><X size={18} /></button></header>
        <label><span>计划名称</span><input autoFocus value={name} onChange={(event) => setName(event.target.value)} maxLength={200} placeholder="例如：每工作日晨报" /></label>
        <div className="automation-form-grid">
          <label><span>运行周期</span><select value={preset} onChange={(event) => setPreset(event.target.value)}>{CRON_PRESETS.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}<option value="custom">自定义 Cron</option></select></label>
          {preset === "custom" && <label><span>Cron 表达式</span><input value={customCron} onChange={(event) => setCustomCron(event.target.value)} maxLength={100} placeholder="0 9 * * 1-5" /></label>}
          <label><span>失火补跑</span><select value={misfire} onChange={(event) => setMisfire(event.target.value as "SKIP" | "FIRE_ONCE")}><option value="FIRE_ONCE">补跑一次</option><option value="SKIP">跳过本次</option></select></label>
          <label><span>固定版本</span><select value={pin} onChange={(event) => setPin(event.target.value)}><option value="active">跟随当前发布版本</option>{published.map((version) => <option key={version.id} value={version.version}>固定 v{version.version}</option>)}</select></label>
        </div>
        <label><span>运行参数（JSON，可留空）</span><textarea value={payloadText} onChange={(event) => { setPayloadText(event.target.value); setFormError(""); }} rows={3} placeholder='{"window": "24h"}' /></label>
        {formError && <div className="notice error"><XCircle size={14} />{formError}</div>}
        <footer><button type="button" className="secondary-button" onClick={onClose}>取消</button><button className="primary-button" disabled={saving || !name.trim()}>{saving ? "正在创建…" : "创建计划"}</button></footer>
      </form>
    </div>
  );
}

function ExecutionDrawer({ execution, saving, onClose, onCancel, onReview, onOpenRun }: { execution: AutomationExecution; saving: boolean; onClose: () => void; onCancel: () => void; onReview: (step: AutomationStep) => void; onOpenRun?: (runId: string) => void }) {
  return (
    <div className="execution-drawer-backdrop" role="presentation" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
      <aside className="execution-drawer" aria-label="自动化运行详情">
        <header><div><ExecutionStatus status={execution.status} /><h2>运行详情</h2><code>{execution.id}</code></div><button className="icon-button" onClick={onClose}><X size={18} /></button></header>
        <section className="execution-meta"><span>触发<strong>{triggerLabel(execution.trigger)}</strong></span><span>开始<strong>{formatDate(execution.started_at ?? execution.created_at)}</strong></span><span>截止<strong>{formatDate(execution.deadline_at)}</strong></span></section>
        {Object.keys(execution.input_payload ?? {}).length > 0 && (
          <section className="execution-input"><span><Braces size={13} />运行参数</span><code>{JSON.stringify(execution.input_payload)}</code></section>
        )}
        {execution.error_message && <div className="execution-error"><XCircle size={16} /><span><strong>{execution.error_code}</strong>{execution.error_message}</span></div>}
        <div className="step-timeline">
          {(execution.steps ?? []).map((step, index) => (
            <article key={step.id} className={`timeline-step ${step.status.toLowerCase()}`}>
              <span className="timeline-index">{step.status === "COMPLETED" ? <Check size={13} /> : index + 1}</span>
              <div>
                <header><strong>{step.name}</strong><ExecutionStatus status={step.status} compact /></header>
                <small>{stepTypeLabel(step.step_type)}</small>
                {step.run_id && (onOpenRun ? (
                  <button className="task-source-run step-run-link" onClick={() => onOpenRun(step.run_id!)}>Harness {step.run_id.slice(0, 8)}</button>
                ) : (
                  <small>Harness {step.run_id.slice(0, 8)}</small>
                ))}
                {step.error_message && <p>{step.error_message}</p>}
                {step.output_refs.length > 0 && (
                  <div className="step-outputs">{step.output_refs.map((ref, refIndex) => <span key={refIndex} className="output-ref-chip"><FileBox size={11} />{outputRefLabel(ref)}</span>)}</div>
                )}
                {step.status === "WAITING_REVIEW" && <button className="primary-button review-button" onClick={() => onReview(step)}><ShieldCheck size={13} />进行人工确认</button>}
              </div>
            </article>
          ))}
        </div>
        <footer>{ACTIVE_EXECUTIONS.has(execution.status) && <button className="secondary-button danger-button" onClick={onCancel} disabled={saving}><Square size={12} />停止运行</button>}<span>版本已固定 · 审计记录已开启</span></footer>
      </aside>
    </div>
  );
}

interface CreateDefinition { payload: Record<string, unknown>; cron?: string }

function CreateAutomationModal({ workspace, saving, onClose, onCreate }: { workspace: Workspace; saving: boolean; onClose: () => void; onCreate: (definition: CreateDefinition) => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [prompt, setPrompt] = useState("");
  const [review, setReview] = useState(false);
  const [frequency, setFrequency] = useState("none");
  const slug = useMemo(() => slugify(name), [name]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim() || !prompt.trim()) return;
    const spec = buildSpecFromDraft(
      { prompt, review, reviewInstructions: "", disallowSelfReview: false, notifyTitle: "", notifyBody: "" },
      name,
    );
    onCreate({
      payload: { name: slug, display_name: name.trim(), description: description.trim(), concurrency_policy: "FORBID", max_concurrency: 1, timeout_seconds: 3600, notify_on_failure: true, classification: workspace.classification || "INTERNAL", spec },
      cron: frequency === "daily" ? "0 9 * * *" : frequency === "weekday" ? "0 9 * * 1-5" : frequency === "hourly" ? "0 * * * *" : undefined,
    });
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="workspace-modal automation-modal" onSubmit={submit}>
        <header><span className="modal-icon"><Sparkles size={19} /></span><div><h2>新建自动化</h2><p>用业务目标创建可审计的周期智能分析</p></div><button type="button" className="icon-button" onClick={onClose}><X size={18} /></button></header>
        <label><span>名称</span><input autoFocus value={name} onChange={(event) => setName(event.target.value)} maxLength={80} placeholder="例如：每日支付异常分析" /><small>标识：{slug}</small></label>
        <label><span>说明</span><input value={description} onChange={(event) => setDescription(event.target.value)} maxLength={4000} placeholder="谁使用这个结果，它解决什么问题？" /></label>
        <label><span>分析目标</span><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} maxLength={100000} rows={5} placeholder="例如：分析过去 24 小时支付成功率、错误码和渠道差异；发现异常时给出证据、可能原因与建议动作。" /></label>
        <div className="automation-form-grid"><label><span>运行周期</span><select value={frequency} onChange={(event) => setFrequency(event.target.value)}><option value="none">暂不定时</option><option value="daily">每天 09:00</option><option value="weekday">工作日 09:00</option><option value="hourly">每小时</option></select></label><label className="review-option"><span>人工确认</span><button type="button" className={review ? "choice active" : "choice"} onClick={() => setReview((value) => !value)}><ShieldCheck size={15} />{review ? "通知前需要确认" : "无需人工确认"}</button></label></div>
        <div className="automation-form-note"><GitBranch size={15} /><span>创建后将发布版本 1。工作流内容不可原地修改，后续调整会生成新版本。</span></div>
        <footer><button type="button" className="secondary-button" onClick={onClose}>取消</button><button className="primary-button" disabled={saving || !name.trim() || !prompt.trim()}>{saving ? "正在建立…" : "创建并发布"}</button></footer>
      </form>
    </div>
  );
}

function ReviewModal({ step, reason, saving, onReason, onClose, onDecision }: { step: AutomationStep; reason: string; saving: boolean; onReason: (value: string) => void; onClose: () => void; onDecision: (decision: "APPROVE" | "REJECT") => void }) {
  return <div className="modal-backdrop" role="presentation"><div className="workspace-modal review-modal"><header><span className="modal-icon"><ShieldCheck size={19} /></span><div><h2>{step.name}</h2><p>你的决定和理由将进入不可变审计记录</p></div><button className="icon-button" onClick={onClose}><X size={18} /></button></header><label><span>审核意见</span><textarea autoFocus rows={4} value={reason} onChange={(event) => onReason(event.target.value)} placeholder="说明检查了哪些证据，以及批准或拒绝的原因。" /></label><footer><button className="secondary-button danger-button" onClick={() => onDecision("REJECT")} disabled={saving || reason.trim().length < 3}>拒绝</button><button className="primary-button" onClick={() => onDecision("APPROVE")} disabled={saving || reason.trim().length < 3}>批准并继续</button></footer></div></div>;
}

function EmptyAutomation({ title, body, onCreate }: { title: string; body: string; onCreate?: () => void }) {
  return <div className="automation-empty"><span><GitBranch size={24} /></span><h2>{title}</h2><p>{body}</p>{onCreate && <button className="primary-button" onClick={onCreate}><Plus size={15} />新建自动化</button>}</div>;
}

function StatusDot({ status }: { status: Workflow["status"] }) { return <i className={`status-dot ${status.toLowerCase()}`} />; }

function ExecutionStatus({ status, compact = false }: { status: AutomationStatus; compact?: boolean }) {
  return <span className={`automation-status ${status.toLowerCase()} ${compact ? "compact" : ""}`}>{ACTIVE_EXECUTIONS.has(status) && <LoaderCircle size={10} className="spin" />}{status === "WAITING_REVIEW" && <ShieldCheck size={10} />}{status === "COMPLETED" && <Check size={10} />}{statusLabel(status)}</span>;
}

function workflowStatusLabel(status: Workflow["status"]) { return { DRAFT: "草稿", ACTIVE: "运行中", PAUSED: "已暂停", RETIRED: "已退役" }[status]; }
function triggerLabel(trigger: AutomationExecution["trigger"]) { return { MANUAL: "手动运行", SCHEDULE: "定时计划", CAPABILITY: "能力网关" }[trigger]; }
function statusLabel(status: AutomationStatus) { return { PENDING: "等待", RUNNING: "运行中", WAITING_REVIEW: "待确认", COMPLETED: "完成", FAILED: "失败", CANCELLED: "已停止", SKIPPED: "已跳过" }[status]; }
function stepTypeLabel(type: AutomationStep["step_type"]) { return { ANALYSIS: "Harness 智能分析", HUMAN_REVIEW: "人工确认门", NOTIFICATION: "责任人通知" }[type]; }
function concurrencyLabel(value: Workflow["concurrency_policy"]) { return { FORBID: "禁止重叠", ALLOW: "允许并行", REPLACE: "新运行替换旧运行" }[value]; }
function slugify(value: string) { const normalized = value.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 70); return normalized && /^[a-z]/.test(normalized) ? normalized : `workflow-${Date.now().toString(36)}`; }
function formatDate(value: string) { return new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
function formatRelative(value: string) { const distance = Date.now() - new Date(value).getTime(); const minutes = Math.max(0, Math.floor(distance / 60000)); return minutes < 1 ? "刚刚" : minutes < 60 ? `${minutes} 分钟前` : minutes < 1440 ? `${Math.floor(minutes / 60)} 小时前` : formatDate(value); }
function durationLabel(execution: AutomationExecution) { if (!execution.started_at) return "—"; const end = execution.completed_at ? new Date(execution.completed_at).getTime() : Date.now(); const seconds = Math.max(0, Math.floor((end - new Date(execution.started_at).getTime()) / 1000)); return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`; }
