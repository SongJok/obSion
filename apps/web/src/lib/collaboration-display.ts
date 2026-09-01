import type {
  Run,
  Thread,
  WorkspaceMember,
  WorkspaceTask,
  WorkspaceTaskPriority,
} from "./types";

export interface SourceRunOption {
  runId: string;
  threadId: string;
  label: string;
  createdAt: string;
}

export interface TaskDraft {
  title: string;
  description: string;
  priority: WorkspaceTaskPriority;
  /** Empty string means the task is unassigned. */
  assigneeId: string;
  /** datetime-local input value; empty string means no deadline. */
  dueAt: string;
}

/** Source-Run options stay bounded so the selector never grows with history. */
export const MAX_SOURCE_RUN_OPTIONS = 50;

export function memberDisplayName(
  members: WorkspaceMember[],
  userId: string | null | undefined,
): string {
  if (!userId) return "";
  const member = members.find((item) => item.user_id === userId);
  return member?.display_name ?? `成员 ${userId.slice(0, 8)}`;
}

export function buildSourceRunOptions(
  threads: Thread[],
  runsByThread: Array<{ threadId: string; runs: Run[] }>,
): SourceRunOption[] {
  const titleByThread = new Map(threads.map((thread) => [thread.id, thread.title]));
  const options: SourceRunOption[] = [];
  for (const { threadId, runs } of runsByThread) {
    const title = titleByThread.get(threadId) ?? `任务 ${threadId.slice(0, 8)}`;
    for (const run of runs) {
      options.push({
        runId: run.id,
        threadId,
        label: `${title} · Run ${run.id.slice(0, 8)}`,
        createdAt: run.created_at,
      });
    }
  }
  return options
    .sort((left, right) => right.createdAt.localeCompare(left.createdAt))
    .slice(0, MAX_SOURCE_RUN_OPTIONS);
}

/** Labels a persisted source_run_id even when the Run fell out of the option window. */
export function sourceRunLabel(
  options: SourceRunOption[],
  runId: string | null | undefined,
): string {
  if (!runId) return "";
  return options.find((option) => option.runId === runId)?.label ?? `Run ${runId.slice(0, 8)}`;
}

export function toDateTimeLocalValue(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

function normalizeDue(dueAt: string): string | null {
  if (!dueAt) return null;
  const date = new Date(dueAt);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

export function taskCreatePayload(
  draft: TaskDraft,
  sourceRunId: string,
): Record<string, unknown> {
  return {
    title: draft.title.trim(),
    description: draft.description.trim(),
    priority: draft.priority,
    ...(draft.assigneeId ? { assignee_id: draft.assigneeId } : {}),
    ...(sourceRunId ? { source_run_id: sourceRunId } : {}),
    ...(normalizeDue(draft.dueAt) ? { due_at: normalizeDue(draft.dueAt) } : {}),
  };
}

/**
 * Builds the optimistic-concurrency update payload. Only changed fields are
 * sent; clearing the assignee is an explicit `assignee_id: null`, which the
 * control plane distinguishes from "field not sent" via model_fields_set.
 */
export function taskUpdatePayload(
  task: WorkspaceTask,
  draft: TaskDraft,
): Record<string, unknown> {
  const payload: Record<string, unknown> = { expected_version: task.version };
  const title = draft.title.trim();
  if (title && title !== task.title) payload.title = title;
  const description = draft.description.trim();
  if (description !== task.description) payload.description = description;
  if (draft.priority !== task.priority) payload.priority = draft.priority;
  const assigneeId = draft.assigneeId || null;
  if (assigneeId !== task.assignee_id) payload.assignee_id = assigneeId;
  const dueAt = normalizeDue(draft.dueAt);
  const currentDue = task.due_at ? new Date(task.due_at).toISOString() : null;
  if (dueAt !== currentDue) payload.due_at = dueAt;
  return payload;
}

/** The control plane rejects no-op updates, so the form must stay disabled. */
export function taskUpdateHasChanges(payload: Record<string, unknown>): boolean {
  return Object.keys(payload).some((key) => key !== "expected_version");
}
