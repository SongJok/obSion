"use client";

import {
  AlertTriangle,
  Check,
  CheckCircle2,
  CircleDashed,
  Clock3,
  GitBranch,
  History,
  ListChecks,
  LoaderCircle,
  LockKeyhole,
  PanelRightOpen,
  Pencil,
  Plus,
  RefreshCw,
  Scale,
  ShieldCheck,
  UserRound,
  X,
  XCircle,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "@/lib/api";
import {
  buildSourceRunOptions,
  memberDisplayName,
  sourceRunLabel,
  sourceRunThreadId,
  taskCreatePayload,
  taskUpdateHasChanges,
  taskUpdatePayload,
  toDateTimeLocalValue,
  type SourceRunOption,
  type TaskDraft,
} from "@/lib/collaboration-display";
import type {
  Workspace,
  WorkspaceDecision,
  WorkspaceDecisionVersion,
  WorkspaceMember,
  WorkspaceTask,
  WorkspaceTaskPriority,
  WorkspaceTaskStatus,
} from "@/lib/types";

const CLOSED_TASKS = new Set<WorkspaceTaskStatus>(["COMPLETED", "CANCELLED"]);

async function fetchSourceRunOptions(workspaceId: string): Promise<SourceRunOption[]> {
  const threads = await api.listThreads(workspaceId);
  const runsByThread = await Promise.all(
    threads.map(async (thread) => ({
      threadId: thread.id,
      runs: await api.listThreadRuns(thread.id),
    })),
  );
  return buildSourceRunOptions(threads, runsByThread);
}

export function CollaborationView({
  workspace,
  onOpenRun,
}: {
  workspace?: Workspace;
  onOpenRun?: (runId: string, threadId?: string) => void;
}) {
  const [tasks, setTasks] = useState<WorkspaceTask[]>([]);
  const [decisions, setDecisions] = useState<WorkspaceDecision[]>([]);
  const [versions, setVersions] = useState<WorkspaceDecisionVersion[]>([]);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [sourceRuns, setSourceRuns] = useState<SourceRunOption[]>([]);
  const [selectedDecisionId, setSelectedDecisionId] = useState<string>();
  const [taskFilter, setTaskFilter] = useState<"ACTIVE" | "ALL" | "CLOSED">("ACTIVE");
  const [loading, setLoading] = useState(Boolean(workspace));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [taskModalOpen, setTaskModalOpen] = useState(false);
  const [decisionModalOpen, setDecisionModalOpen] = useState(false);
  const [editingTask, setEditingTask] = useState<WorkspaceTask>();
  const [editingDecision, setEditingDecision] = useState<WorkspaceDecision>();
  const [supersedesDecisionId, setSupersedesDecisionId] = useState<string>();

  const selectedDecision = decisions.find((item) => item.id === selectedDecisionId);

  const load = useCallback(async () => {
    if (!workspace) {
      setTasks([]);
      setDecisions([]);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const [nextTasks, nextDecisions, nextMembers, nextSourceRuns] = await Promise.all([
        api.collaboration.listTasks(workspace.id),
        api.collaboration.listDecisions(workspace.id),
        api.listWorkspaceMembers(workspace.id),
        fetchSourceRunOptions(workspace.id),
      ]);
      setTasks(nextTasks);
      setDecisions(nextDecisions);
      setMembers(nextMembers);
      setSourceRuns(nextSourceRuns);
      setSelectedDecisionId((current) =>
        current && nextDecisions.some((item) => item.id === current)
          ? current
          : nextDecisions[0]?.id,
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取任务与决策");
    } finally {
      setLoading(false);
    }
  }, [workspace]);

  useEffect(() => {
    if (!workspace) return;
    let active = true;
    Promise.all([
      api.collaboration.listTasks(workspace.id),
      api.collaboration.listDecisions(workspace.id),
      api.listWorkspaceMembers(workspace.id),
      fetchSourceRunOptions(workspace.id),
    ])
      .then(([nextTasks, nextDecisions, nextMembers, nextSourceRuns]) => {
        if (!active) return;
        setTasks(nextTasks);
        setDecisions(nextDecisions);
        setMembers(nextMembers);
        setSourceRuns(nextSourceRuns);
        setSelectedDecisionId(nextDecisions[0]?.id);
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(caught instanceof Error ? caught.message : "无法读取任务与决策");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [workspace]);

  useEffect(() => {
    if (!selectedDecisionId) return;
    let active = true;
    api.collaboration
      .versions(selectedDecisionId)
      .then((items) => {
        if (active) setVersions(items);
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(caught instanceof Error ? caught.message : "无法读取决策版本");
        }
      });
    return () => {
      active = false;
    };
  }, [selectedDecisionId]);

  const mutate = useCallback(
    async (operation: () => Promise<void>) => {
      setSaving(true);
      setError("");
      try {
        await operation();
      } catch (caught) {
        // load() clears the notice at its start, so refresh first and then
        // surface the actionable message — otherwise the guidance flashes
        // and disappears before the operator can read it.
        if (caught instanceof ApiError && caught.code.endsWith("_version_conflict")) {
          await load();
          setError("记录已被其他成员更新，已为你刷新到最新版本。请确认后重试。");
        } else if (caught instanceof ApiError && caught.code === "workspace_task_assignee_invalid") {
          await load();
          setError("指派的成员必须是该工作空间的在职成员，请刷新成员列表后重试。");
        } else if (caught instanceof ApiError && caught.code === "workspace_source_run_mismatch") {
          await load();
          setError("来源 Run 必须属于当前工作空间，请刷新后重新选择。");
        } else {
          setError(caught instanceof Error ? caught.message : "操作未能完成");
        }
      } finally {
        setSaving(false);
      }
    },
    [load],
  );

  const transitionTask = (task: WorkspaceTask, status: WorkspaceTaskStatus) => {
    void mutate(async () => {
      const updated = await api.collaboration.updateTask(task.id, {
        expected_version: task.version,
        status,
      });
      setTasks((items) => items.map((item) => (item.id === updated.id ? updated : item)));
    });
  };

  const decide = (decision: WorkspaceDecision, approve: boolean) => {
    void mutate(async () => {
      const updated = await api.collaboration.decide(
        decision.id,
        approve,
        decision.current_version,
      );
      setDecisions((items) =>
        items.map((item) => (item.id === updated.id ? updated : item)),
      );
      await load();
    });
  };

  const visibleTasks = useMemo(
    () =>
      tasks.filter((task) => {
        if (taskFilter === "ALL") return true;
        return taskFilter === "CLOSED" ? CLOSED_TASKS.has(task.status) : !CLOSED_TASKS.has(task.status);
      }),
    [taskFilter, tasks],
  );
  const activeTasks = tasks.filter((item) => !CLOSED_TASKS.has(item.status)).length;
  const blockedTasks = tasks.filter((item) => item.status === "BLOCKED").length;
  const proposedDecisions = decisions.filter((item) => item.status === "PROPOSED").length;
  const acceptedDecisions = decisions.filter((item) => item.status === "ACCEPTED").length;

  if (!workspace) {
    return (
      <main className="feature-page collaboration-page">
        <CollaborationEmpty
          icon={<ListChecks size={28} />}
          title="请先选择工作空间"
          body="任务、决策版本和审计历史都严格归属于工作空间。"
        />
      </main>
    );
  }

  return (
    <main className="feature-page collaboration-page">
      <header className="feature-header collaboration-header">
        <div>
          <span className="eyebrow">GOVERNED COLLABORATION</span>
          <h1>任务与决策</h1>
          <p>将调查结论转成可推进的任务与可追溯的团队决策。每次修改都带版本校验，已形成的决策内容不会被覆盖。</p>
        </div>
        <div className="collaboration-header-actions">
          <button className="secondary-button" onClick={() => void load()} disabled={loading}>
            <RefreshCw className={loading ? "spin" : ""} size={14} />刷新
          </button>
          <button className="secondary-button" onClick={() => { setEditingTask(undefined); setTaskModalOpen(true); }}>
            <Plus size={14} />新建任务
          </button>
          <button
            className="primary-button"
            onClick={() => {
              setEditingDecision(undefined);
              setSupersedesDecisionId(undefined);
              setDecisionModalOpen(true);
            }}
          >
            <Scale size={14} />记录决策
          </button>
        </div>
      </header>

      <div className="collaboration-trust-strip">
        <LockKeyhole size={16} />
        <span><strong>治理记录已启用</strong> 决策内容按版本封存；任务和决策不能直接删除；所有状态变化写入事件与审计日志。</span>
      </div>

      {error && (
        <div className="notice error collaboration-notice">
          <XCircle size={15} /><span>{error}</span>
          <button onClick={() => setError("")} aria-label="关闭提示"><X size={14} /></button>
        </div>
      )}

      <section className="collaboration-stats" aria-label="协作概览">
        <article><span><ListChecks size={17} /></span><div><strong>{activeTasks}</strong><small>进行中任务</small></div></article>
        <article className={blockedTasks ? "attention" : ""}><span><AlertTriangle size={17} /></span><div><strong>{blockedTasks}</strong><small>阻塞任务</small></div></article>
        <article className={proposedDecisions ? "attention" : ""}><span><CircleDashed size={17} /></span><div><strong>{proposedDecisions}</strong><small>待定决策</small></div></article>
        <article><span><ShieldCheck size={17} /></span><div><strong>{acceptedDecisions}</strong><small>当前有效决策</small></div></article>
      </section>

      {loading && !tasks.length && !decisions.length ? (
        <div className="collaboration-loading"><LoaderCircle className="spin" size={22} />正在同步协作记录…</div>
      ) : (
        <section className="collaboration-grid">
          <section className="collaboration-panel task-panel">
            <header>
              <div><ListChecks size={17} /><span><strong>工作任务</strong><small>{workspace.name}</small></span></div>
              <button className="icon-button" onClick={() => { setEditingTask(undefined); setTaskModalOpen(true); }} aria-label="新建任务"><Plus size={16} /></button>
            </header>
            <div className="collaboration-tabs" aria-label="任务筛选">
              {(["ACTIVE", "ALL", "CLOSED"] as const).map((filter) => (
                <button key={filter} className={taskFilter === filter ? "active" : ""} onClick={() => setTaskFilter(filter)}>
                  {filter === "ACTIVE" ? "待推进" : filter === "ALL" ? "全部" : "已关闭"}
                </button>
              ))}
            </div>
            <div className="task-record-list">
              {visibleTasks.map((task) => (
                <TaskRecord
                  key={task.id}
                  task={task}
                  members={members}
                  sourceRuns={sourceRuns}
                  saving={saving}
                  onTransition={transitionTask}
                  onEdit={(target) => {
                    setEditingTask(target);
                    setTaskModalOpen(true);
                  }}
                  onOpenRun={onOpenRun}
                />
              ))}
              {!visibleTasks.length && (
                <CollaborationEmpty
                  compact
                  icon={<CheckCircle2 size={22} />}
                  title={tasks.length ? "当前筛选没有任务" : "还没有工作任务"}
                  body="把需要跟进的结论登记为任务，团队会共享同一状态。"
                  action="新建任务"
                  onAction={() => { setEditingTask(undefined); setTaskModalOpen(true); }}
                />
              )}
            </div>
          </section>

          <section className="collaboration-panel decision-panel">
            <header>
              <div><Scale size={17} /><span><strong>决策记录</strong><small>不可变版本与替代谱系</small></span></div>
              <button
                className="icon-button"
                onClick={() => {
                  setEditingDecision(undefined);
                  setSupersedesDecisionId(undefined);
                  setDecisionModalOpen(true);
                }}
                aria-label="记录决策"
              ><Plus size={16} /></button>
            </header>
            {!decisions.length ? (
              <CollaborationEmpty
                icon={<Scale size={25} />}
                title="记录第一项团队决策"
                body="写清结论、依据和备选方案，后续修订会形成新版本。"
                action="记录决策"
                onAction={() => setDecisionModalOpen(true)}
              />
            ) : (
              <div className="decision-workspace">
                <nav className="decision-record-list" aria-label="决策记录">
                  {decisions.map((decision) => (
                    <button
                      key={decision.id}
                      className={decision.id === selectedDecisionId ? "selected" : ""}
                      onClick={() => setSelectedDecisionId(decision.id)}
                    >
                      <span className={`decision-state ${decision.status.toLowerCase()}`}>
                        {decision.status === "ACCEPTED" ? <Check size={13} /> : <GitBranch size={13} />}
                      </span>
                      <span><strong>{decision.title}</strong><small>v{decision.current_version} · {decisionStatusLabel(decision.status)}</small></span>
                    </button>
                  ))}
                </nav>
                {selectedDecision && (
                  <DecisionDetail
                    decision={selectedDecision}
                    versions={versions}
                    sourceRuns={sourceRuns}
                    saving={saving}
                    onOpenRun={onOpenRun}
                    onEdit={() => {
                      setEditingDecision(selectedDecision);
                      setDecisionModalOpen(true);
                    }}
                    onDecide={(approve) => decide(selectedDecision, approve)}
                    onSupersede={() => {
                      setEditingDecision(undefined);
                      setSupersedesDecisionId(selectedDecision.id);
                      setDecisionModalOpen(true);
                    }}
                  />
                )}
              </div>
            )}
          </section>
        </section>
      )}

      {taskModalOpen && (
        <TaskModal
          workspace={workspace}
          task={editingTask}
          members={members}
          runOptions={sourceRuns}
          saving={saving}
          onClose={() => setTaskModalOpen(false)}
          onSave={(definition) =>
            void mutate(async () => {
              if (editingTask) {
                const updated = await api.collaboration.updateTask(editingTask.id, definition);
                setTasks((items) => items.map((item) => (item.id === updated.id ? updated : item)));
              } else {
                const created = await api.collaboration.createTask(workspace.id, definition);
                setTasks((items) => [created, ...items]);
              }
              setTaskModalOpen(false);
            })
          }
        />
      )}
      {decisionModalOpen && (
        <DecisionModal
          workspace={workspace}
          decision={editingDecision}
          acceptedDecisions={decisions.filter((item) => item.status === "ACCEPTED")}
          supersedesDecisionId={supersedesDecisionId}
          runOptions={sourceRuns}
          saving={saving}
          onClose={() => setDecisionModalOpen(false)}
          onSave={(definition) =>
            void mutate(async () => {
              const saved = editingDecision
                ? await api.collaboration.reviseDecision(editingDecision.id, {
                    ...definition,
                    expected_version: editingDecision.current_version,
                  })
                : await api.collaboration.createDecision(workspace.id, definition);
              setDecisions((items) => [saved, ...items.filter((item) => item.id !== saved.id)]);
              setSelectedDecisionId(saved.id);
              setVersions(await api.collaboration.versions(saved.id));
              setDecisionModalOpen(false);
            })
          }
        />
      )}
    </main>
  );
}

function TaskRecord({
  task,
  members,
  sourceRuns,
  saving,
  onTransition,
  onEdit,
  onOpenRun,
}: {
  task: WorkspaceTask;
  members: WorkspaceMember[];
  sourceRuns: SourceRunOption[];
  saving: boolean;
  onTransition: (task: WorkspaceTask, status: WorkspaceTaskStatus) => void;
  onEdit: (task: WorkspaceTask) => void;
  onOpenRun?: (runId: string, threadId?: string) => void;
}) {
  const transitions = taskTransitions(task.status);
  const overdue = Boolean(task.due_at && !CLOSED_TASKS.has(task.status) && new Date(task.due_at) < new Date());
  return (
    <article className={`task-record ${overdue ? "overdue" : ""}`}>
      <div className="task-record-topline">
        <span className={`task-priority ${task.priority.toLowerCase()}`}>{priorityLabel(task.priority)}</span>
        <TaskStatus status={task.status} />
        <code>v{task.version}</code>
        <button className="icon-button task-edit-button" onClick={() => onEdit(task)} aria-label="编辑任务" title="编辑任务"><Pencil size={13} /></button>
      </div>
      <h3>{task.title}</h3>
      {task.description && <p>{task.description}</p>}
      <div className="task-record-meta">
        <span className={overdue ? "overdue" : ""}><Clock3 size={12} />{task.due_at ? formatDate(task.due_at) : "未设置截止时间"}</span>
        {task.assignee_id && (
          <span className="task-assignee"><UserRound size={12} />{memberDisplayName(members, task.assignee_id)}</span>
        )}
        {task.source_run_id && (
          <button
            className="task-source-run"
            onClick={() => onOpenRun?.(
              task.source_run_id!,
              sourceRunThreadId(sourceRuns, task.source_run_id),
            )}
            disabled={!onOpenRun}
            title="在 Runtime 面板中查看来源 Run"
          >
            <PanelRightOpen size={12} />{sourceRunLabel(sourceRuns, task.source_run_id)}
          </button>
        )}
      </div>
      <footer>
        {transitions.map((transition, index) => (
          <button
            key={transition.status}
            className={index === 0 ? "task-primary-action" : ""}
            disabled={saving}
            onClick={() => onTransition(task, transition.status)}
          >
            {transition.label}
          </button>
        ))}
      </footer>
    </article>
  );
}

function DecisionDetail({
  decision,
  versions,
  sourceRuns,
  saving,
  onOpenRun,
  onEdit,
  onDecide,
  onSupersede,
}: {
  decision: WorkspaceDecision;
  versions: WorkspaceDecisionVersion[];
  sourceRuns: SourceRunOption[];
  saving: boolean;
  onOpenRun?: (runId: string, threadId?: string) => void;
  onEdit: () => void;
  onDecide: (approve: boolean) => void;
  onSupersede: () => void;
}) {
  return (
    <article className="decision-detail">
      <header>
        <div>
          <span className={`decision-status ${decision.status.toLowerCase()}`}>{decisionStatusLabel(decision.status)}</span>
          <h2>{decision.title}</h2>
          <p>{decision.summary}</p>
        </div>
        <code>v{decision.current_version}</code>
      </header>
      <section className="decision-rationale">
        <strong>决策依据</strong>
        <p>{decision.rationale}</p>
      </section>
      {decision.alternatives.length > 0 && (
        <section className="decision-alternatives">
          <strong>评估过的备选方案</strong>
          <ul>{decision.alternatives.map((item) => <li key={item}>{item}</li>)}</ul>
        </section>
      )}
      {decision.supersedes_decision_id && (
        <div className="decision-lineage"><GitBranch size={14} />替代决策 {decision.supersedes_decision_id.slice(0, 8)}</div>
      )}
      {decision.source_run_id && (
        <div className="decision-lineage">
          <PanelRightOpen size={14} />
          来源 Run
          <button
            className="task-source-run"
            onClick={() => onOpenRun?.(
              decision.source_run_id!,
              sourceRunThreadId(sourceRuns, decision.source_run_id),
            )}
            disabled={!onOpenRun}
            title="在 Runtime 面板中查看来源 Run"
          >
            {sourceRunLabel(sourceRuns, decision.source_run_id)}
          </button>
        </div>
      )}
      <div className="decision-fingerprint">
        <LockKeyhole size={13} /><span>内容指纹</span><code>{decision.checksum_sha256.slice(0, 20)}</code>
      </div>
      <footer className="decision-actions">
        {decision.status === "PROPOSED" && (
          <>
            <button className="secondary-button" onClick={onEdit} disabled={saving}><Pencil size={13} />修订</button>
            <button className="secondary-button danger-button" onClick={() => onDecide(false)} disabled={saving}>拒绝</button>
            <button className="primary-button" onClick={() => onDecide(true)} disabled={saving}><Check size={14} />接受</button>
          </>
        )}
        {decision.status === "ACCEPTED" && (
          <button className="secondary-button" onClick={onSupersede} disabled={saving}><GitBranch size={14} />提出替代决策</button>
        )}
      </footer>
      <section className="decision-version-history">
        <header><History size={14} /><strong>版本历史</strong><span>{versions.length}</span></header>
        {versions.map((version) => (
          <div key={version.id}>
            <span>v{version.version}</span>
            <p>{version.summary}</p>
            <time dateTime={version.created_at}>{formatDate(version.created_at)}</time>
          </div>
        ))}
      </section>
    </article>
  );
}

function TaskModal({
  workspace,
  task,
  members,
  runOptions,
  saving,
  onClose,
  onSave,
}: {
  workspace: Workspace;
  task?: WorkspaceTask;
  members: WorkspaceMember[];
  runOptions: SourceRunOption[];
  saving: boolean;
  onClose: () => void;
  onSave: (definition: Record<string, unknown>) => void;
}) {
  const [title, setTitle] = useState(task?.title ?? "");
  const [description, setDescription] = useState(task?.description ?? "");
  const [priority, setPriority] = useState<WorkspaceTaskPriority>(task?.priority ?? "NORMAL");
  const [assigneeId, setAssigneeId] = useState(task?.assignee_id ?? "");
  const [sourceRunId, setSourceRunId] = useState(task?.source_run_id ?? "");
  const [dueAt, setDueAt] = useState(toDateTimeLocalValue(task?.due_at));
  const draft: TaskDraft = { title, description, priority, assigneeId, dueAt };
  const updatePayload = task ? taskUpdatePayload(task, draft) : undefined;
  const unchanged = Boolean(task && updatePayload && !taskUpdateHasChanges(updatePayload));
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!title.trim()) return;
    onSave(task && updatePayload ? updatePayload : taskCreatePayload(draft, sourceRunId));
  };
  return (
    <div className="modal-backdrop" role="presentation">
      <form className="workspace-modal collaboration-modal" onSubmit={submit}>
        <header><span className="modal-icon"><ListChecks size={19} /></span><div><h2>{task ? `编辑任务 v${task.version}` : "新建工作任务"}</h2><p>{workspace.name} · 状态变化会同步到审计记录</p></div><button type="button" className="icon-button" onClick={onClose}><X size={18} /></button></header>
        <label><span>任务名称</span><input autoFocus value={title} onChange={(event) => setTitle(event.target.value)} maxLength={300} placeholder="例如：验证支付超时的客户影响" /></label>
        <label><span>说明</span><textarea value={description} onChange={(event) => setDescription(event.target.value)} maxLength={20000} rows={3} placeholder="写清完成标准、依据和需要协作的事项" /></label>
        <div className="collaboration-form-grid">
          <label><span>优先级</span><select value={priority} onChange={(event) => setPriority(event.target.value as WorkspaceTaskPriority)}><option value="LOW">低</option><option value="NORMAL">普通</option><option value="HIGH">高</option><option value="CRITICAL">紧急</option></select></label>
          <label><span>截止时间</span><input type="datetime-local" value={dueAt} onChange={(event) => setDueAt(event.target.value)} /></label>
        </div>
        <div className="collaboration-form-grid">
          <label>
            <span>指派给</span>
            <select value={assigneeId} onChange={(event) => setAssigneeId(event.target.value)}>
              <option value="">未指派</option>
              {members.map((member) => (
                <option key={member.user_id} value={member.user_id}>{member.display_name}</option>
              ))}
            </select>
          </label>
          {!task && (
            <label>
              <span>来源 Run（可选）</span>
              <select value={sourceRunId} onChange={(event) => setSourceRunId(event.target.value)}>
                <option value="">无来源 Run</option>
                {runOptions.map((option) => (
                  <option key={option.runId} value={option.runId}>{option.label}</option>
                ))}
              </select>
            </label>
          )}
        </div>
        <footer><button type="button" className="secondary-button" onClick={onClose}>取消</button><button className="primary-button" disabled={saving || !title.trim() || unchanged}>{saving ? "正在保存…" : task ? "保存修改" : "创建任务"}</button></footer>
      </form>
    </div>
  );
}

function DecisionModal({
  workspace,
  decision,
  acceptedDecisions,
  supersedesDecisionId,
  runOptions,
  saving,
  onClose,
  onSave,
}: {
  workspace: Workspace;
  decision?: WorkspaceDecision;
  acceptedDecisions: WorkspaceDecision[];
  supersedesDecisionId?: string;
  runOptions: SourceRunOption[];
  saving: boolean;
  onClose: () => void;
  onSave: (definition: Record<string, unknown>) => void;
}) {
  const [title, setTitle] = useState(decision?.title ?? "");
  const [summary, setSummary] = useState(decision?.summary ?? "");
  const [rationale, setRationale] = useState(decision?.rationale ?? "");
  const [alternatives, setAlternatives] = useState(decision?.alternatives.join("\n") ?? "");
  const [supersedes, setSupersedes] = useState(supersedesDecisionId ?? "");
  const [sourceRunId, setSourceRunId] = useState("");
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!title.trim() || !summary.trim() || !rationale.trim()) return;
    onSave({
      title: title.trim(),
      summary: summary.trim(),
      rationale: rationale.trim(),
      alternatives: alternatives.split("\n").map((item) => item.trim()).filter(Boolean),
      ...(!decision && supersedes ? { supersedes_decision_id: supersedes } : {}),
      ...(!decision && sourceRunId ? { source_run_id: sourceRunId } : {}),
    });
  };
  return (
    <div className="modal-backdrop" role="presentation">
      <form className="workspace-modal collaboration-modal decision-modal" onSubmit={submit}>
        <header><span className="modal-icon"><Scale size={19} /></span><div><h2>{decision ? `修订决策 v${decision.current_version}` : "记录团队决策"}</h2><p>{workspace.name} · 保存后生成内容指纹和不可变版本</p></div><button type="button" className="icon-button" onClick={onClose}><X size={18} /></button></header>
        <label><span>决策标题</span><input autoFocus value={title} onChange={(event) => setTitle(event.target.value)} maxLength={300} placeholder="例如：采用追加写入的证据记录" /></label>
        <label><span>结论摘要</span><textarea value={summary} onChange={(event) => setSummary(event.target.value)} rows={2} maxLength={20000} placeholder="团队最终选择了什么？" /></label>
        <label><span>决策依据</span><textarea value={rationale} onChange={(event) => setRationale(event.target.value)} rows={4} maxLength={40000} placeholder="依据、约束、证据和预期影响" /></label>
        <label><span>备选方案（每行一个）</span><textarea value={alternatives} onChange={(event) => setAlternatives(event.target.value)} rows={3} placeholder="保留当前方案&#10;采用另一种实施路径" /></label>
        {!decision && acceptedDecisions.length > 0 && <label><span>替代现有决策（可选）</span><select value={supersedes} onChange={(event) => setSupersedes(event.target.value)}><option value="">不替代现有决策</option>{acceptedDecisions.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>}
        {!decision && (
          <label>
            <span>来源 Run（可选）</span>
            <select value={sourceRunId} onChange={(event) => setSourceRunId(event.target.value)}>
              <option value="">无来源 Run</option>
              {runOptions.map((option) => (
                <option key={option.runId} value={option.runId}>{option.label}</option>
              ))}
            </select>
          </label>
        )}
        <div className="decision-modal-note"><ShieldCheck size={14} />{decision ? "原版本会永久保留，本次保存将生成下一版本。" : "决策在接受或拒绝前可以修订；形成结论后内容不再改变。"}</div>
        <footer><button type="button" className="secondary-button" onClick={onClose}>取消</button><button className="primary-button" disabled={saving || !title.trim() || !summary.trim() || !rationale.trim()}>{saving ? "正在封存…" : decision ? "保存新版本" : "保存为待定决策"}</button></footer>
      </form>
    </div>
  );
}

function CollaborationEmpty({ icon, title, body, compact, action, onAction }: { icon: React.ReactNode; title: string; body: string; compact?: boolean; action?: string; onAction?: () => void }) {
  return <div className={`collaboration-empty ${compact ? "compact" : ""}`}>{icon}<h2>{title}</h2><p>{body}</p>{action && onAction && <button className="secondary-button" onClick={onAction}><Plus size={13} />{action}</button>}</div>;
}

function TaskStatus({ status }: { status: WorkspaceTaskStatus }) {
  return <span className={`task-status ${status.toLowerCase()}`}>{taskStatusLabel(status)}</span>;
}

function taskTransitions(status: WorkspaceTaskStatus): Array<{ status: WorkspaceTaskStatus; label: string }> {
  if (status === "OPEN") return [{ status: "IN_PROGRESS", label: "开始处理" }, { status: "BLOCKED", label: "标记阻塞" }, { status: "CANCELLED", label: "取消" }];
  if (status === "IN_PROGRESS") return [{ status: "COMPLETED", label: "完成" }, { status: "BLOCKED", label: "阻塞" }, { status: "OPEN", label: "退回待办" }];
  if (status === "BLOCKED") return [{ status: "IN_PROGRESS", label: "继续处理" }, { status: "COMPLETED", label: "完成" }, { status: "OPEN", label: "退回待办" }];
  return [{ status: "OPEN", label: "重新打开" }];
}

function taskStatusLabel(status: WorkspaceTaskStatus) {
  return { OPEN: "待办", IN_PROGRESS: "处理中", BLOCKED: "阻塞", COMPLETED: "已完成", CANCELLED: "已取消" }[status];
}

function priorityLabel(priority: WorkspaceTaskPriority) {
  return { LOW: "低", NORMAL: "普通", HIGH: "高", CRITICAL: "紧急" }[priority];
}

function decisionStatusLabel(status: WorkspaceDecision["status"]) {
  return { PROPOSED: "待定", ACCEPTED: "已接受", REJECTED: "已拒绝", SUPERSEDED: "已替代" }[status];
}

function formatDate(value: string) {
  return new Date(value).toLocaleString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
