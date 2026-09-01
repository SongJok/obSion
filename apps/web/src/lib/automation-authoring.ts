import type { WorkflowSpec, WorkflowStepSpec, WorkflowVersion } from "./types";

/**
 * Automation authoring helpers: build and inspect immutable workflow specs,
 * validate trigger/schedule payloads, and label step output references.
 * Pure functions so the Workbench behaviour suite can pin them directly.
 */

export interface AuthoringDraft {
  prompt: string;
  review: boolean;
  reviewInstructions: string;
  disallowSelfReview: boolean;
  notifyTitle: string;
  notifyBody: string;
}

export const DEFAULT_REVIEW_INSTRUCTIONS =
  "检查分析结论、证据覆盖和通知范围。";

export const DEFAULT_NOTIFY_BODY =
  "周期分析已完成，请查看运行详情中的证据与产物。";

export function buildSpecFromDraft(
  draft: AuthoringDraft,
  workflowName: string,
): WorkflowSpec {
  const steps: WorkflowStepSpec[] = [
    {
      id: "analyze",
      name: "智能分析",
      type: "ANALYSIS",
      depends_on: [],
      prompt: draft.prompt.trim(),
    },
  ];
  if (draft.review) {
    steps.push({
      id: "review",
      name: "人工确认",
      type: "HUMAN_REVIEW",
      depends_on: ["analyze"],
      review_instructions:
        draft.reviewInstructions.trim() || DEFAULT_REVIEW_INSTRUCTIONS,
      disallow_self_review: draft.disallowSelfReview,
    });
  }
  steps.push({
    id: "notify",
    name: "通知责任人",
    type: "NOTIFICATION",
    depends_on: [draft.review ? "review" : "analyze"],
    title: draft.notifyTitle.trim() || `${workflowName.trim()} 已完成`,
    body: draft.notifyBody.trim() || DEFAULT_NOTIFY_BODY,
  });
  return { steps };
}

export function draftFromSpec(spec: WorkflowSpec): AuthoringDraft {
  const analysis = spec.steps.find((step) => step.type === "ANALYSIS");
  const review = spec.steps.find((step) => step.type === "HUMAN_REVIEW");
  const notification = spec.steps.find((step) => step.type === "NOTIFICATION");
  return {
    prompt: analysis?.prompt ?? "",
    review: Boolean(review),
    reviewInstructions: review?.review_instructions ?? "",
    disallowSelfReview: review?.disallow_self_review ?? false,
    notifyTitle: notification?.title ?? "",
    notifyBody: notification?.body ?? "",
  };
}

const STEP_TYPES = new Set(["ANALYSIS", "HUMAN_REVIEW", "NOTIFICATION"]);

export function parseWorkflowSpec(raw: unknown): WorkflowSpec | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const steps = (raw as { steps?: unknown }).steps;
  if (!Array.isArray(steps) || steps.length === 0) return null;
  const parsed: WorkflowStepSpec[] = [];
  for (const entry of steps) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) return null;
    const step = entry as Record<string, unknown>;
    if (
      typeof step.id !== "string" ||
      typeof step.name !== "string" ||
      typeof step.type !== "string" ||
      !STEP_TYPES.has(step.type) ||
      !Array.isArray(step.depends_on)
    ) {
      return null;
    }
    parsed.push(step as unknown as WorkflowStepSpec);
  }
  return { steps: parsed };
}

export function versionStepSummary(spec: WorkflowSpec | null): string {
  if (!spec) return "步骤定义不可用";
  const labels: Record<string, string> = {
    ANALYSIS: "分析",
    HUMAN_REVIEW: "人工确认",
    NOTIFICATION: "通知",
  };
  return spec.steps.map((step) => labels[step.type] ?? step.type).join(" → ");
}

export type PayloadResult =
  | { ok: true; payload: Record<string, unknown> }
  | { ok: false; message: string };

export function parseInputPayload(text: string): PayloadResult {
  const trimmed = text.trim();
  if (!trimmed) return { ok: true, payload: {} };
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { ok: false, message: "运行参数必须是 JSON 对象，例如 {\"day\": \"2026-09-01\"}" };
    }
    return { ok: true, payload: parsed as Record<string, unknown> };
  } catch {
    return { ok: false, message: "运行参数不是合法的 JSON，请检查逗号、引号与括号。" };
  }
}

export interface ScheduleDraft {
  name: string;
  cron: string;
  timezone: string;
  misfirePolicy: "SKIP" | "FIRE_ONCE";
  workflowVersion: number | null;
  inputPayload: Record<string, unknown>;
}

export const CRON_PRESETS: Array<{ id: string; label: string; cron: string }> = [
  { id: "daily", label: "每天 09:00", cron: "0 9 * * *" },
  { id: "weekday", label: "工作日 09:00", cron: "0 9 * * 1-5" },
  { id: "hourly", label: "每小时", cron: "0 * * * *" },
  { id: "weekly", label: "每周一 09:00", cron: "0 9 * * 1" },
];

export function cronLabel(cron: string): string {
  return CRON_PRESETS.find((preset) => preset.cron === cron)?.label ?? cron;
}

export function cronIsValid(cron: string): boolean {
  const fields = cron.trim().split(/\s+/);
  return fields.length === 5 && fields.every((field) => field.length > 0);
}

export type ScheduleResult =
  | { ok: true; payload: Record<string, unknown> }
  | { ok: false; message: string };

export function buildSchedulePayload(draft: ScheduleDraft): ScheduleResult {
  if (!draft.name.trim()) {
    return { ok: false, message: "请为计划命名，例如：每工作日晨报。" };
  }
  if (!cronIsValid(draft.cron)) {
    return { ok: false, message: "Cron 表达式需要 5 个字段（分 时 日 月 周）。" };
  }
  return {
    ok: true,
    payload: {
      name: draft.name.trim(),
      cron_expression: draft.cron.trim(),
      timezone: draft.timezone.trim() || "UTC",
      misfire_policy: draft.misfirePolicy,
      misfire_grace_seconds: 300,
      input_payload: draft.inputPayload,
      ...(draft.workflowVersion !== null
        ? { workflow_version: draft.workflowVersion }
        : {}),
      enabled: true,
    },
  };
}

export function sortedVersions(versions: WorkflowVersion[]): WorkflowVersion[] {
  return [...versions].sort((left, right) => right.version - left.version);
}

export function outputRefLabel(ref: Record<string, unknown>): string {
  if (ref.type === "artifact") {
    const kind = typeof ref.kind === "string" ? ref.kind : "ARTIFACT";
    return `${kind} 产物`;
  }
  if (ref.type === "notification") return "通知投递";
  return "输出引用";
}

export function artifactOutputRefs(
  refs: Array<Record<string, unknown>>,
): Array<Record<string, unknown>> {
  return refs.filter((ref) => ref.type === "artifact");
}
