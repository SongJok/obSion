import type {
  Artifact,
  Claim,
  Evidence,
  Metric,
  Run,
  RunEvent,
  RunStep,
  Thread,
  Turn,
  Workspace,
  AutomationExecution,
  AutomationStep,
  ActionApproval,
  ActionDetail,
  ActionRequest,
  ActionStatus,
  NotificationDelivery,
  Workflow,
  WorkflowSchedule,
  WorkflowVersion,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_OBSION_API_URL ?? "http://localhost:8080/api/v1";

export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly correlationId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    cache: "no-store",
    credentials: "include",
    headers: {
      Accept: "application/json",
      ...(init?.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as Record<string, string>;
    throw new ApiError(
      body.code ?? "request_failed",
      body.message ?? "请求未能完成",
      body.correlation_id,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  listWorkspaces: () => request<Workspace[]>("/workspaces"),
  createWorkspace: (name: string, description: string) =>
    request<Workspace>("/workspaces", {
      method: "POST",
      body: JSON.stringify({ name, description }),
    }),
  listThreads: (workspaceId: string) =>
    request<Thread[]>(`/workspaces/${workspaceId}/threads`),
  createThread: (workspaceId: string, title: string) =>
    request<Thread>("/threads", {
      method: "POST",
      body: JSON.stringify({ workspace_id: workspaceId, title }),
    }),
  listTurns: (threadId: string) => request<Turn[]>(`/threads/${threadId}/turns`),
  listThreadRuns: (threadId: string) => request<Run[]>(`/threads/${threadId}/runs`),
  createTurn: (threadId: string, input: string, attachmentRefs: Array<Record<string, unknown>> = []) =>
    request<{ turn: Turn; run: Run }>(`/threads/${threadId}/turns`, {
      method: "POST",
      body: JSON.stringify({ input, context_refs: [], attachment_refs: attachmentRefs }),
    }),
  getRun: (runId: string) => request<Run>(`/runs/${runId}`),
  cancelRun: (runId: string) => request<Run>(`/runs/${runId}/cancel`, { method: "POST" }),
  replayRun: (runId: string) => request<Run>(`/runs/${runId}/replay`, { method: "POST" }),
  listEvents: (runId: string, after = 0) =>
    request<RunEvent[]>(`/runs/${runId}/events?after=${after}`),
  listSteps: (runId: string) => request<RunStep[]>(`/runs/${runId}/steps`),
  listEvidence: (runId: string) => request<Evidence[]>(`/runs/${runId}/evidence`),
  listClaims: (runId: string) => request<Claim[]>(`/runs/${runId}/claims`),
  listArtifacts: (runId: string) => request<Artifact[]>(`/runs/${runId}/artifacts`),
  listWorkspaceArtifacts: (workspaceId: string) =>
    request<Artifact[]>(`/workspaces/${workspaceId}/artifacts`),
  uploadArtifact: (workspaceId: string, form: FormData) =>
    request<Artifact>(`/workspaces/${workspaceId}/artifacts`, {
      method: "POST",
      body: form,
    }),
  downloadArtifact: async (artifactId: string) => {
    const response = await fetch(`${API_URL}/artifacts/${artifactId}/content`, {
      cache: "no-store",
      credentials: "include",
      headers: { Accept: "*/*" },
    });
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as Record<string, string>;
      throw new ApiError(
        body.code ?? "artifact_download_failed",
        body.message ?? "产物下载失败",
        body.correlation_id,
      );
    }
    return response.blob();
  },
  listMetrics: () => request<Metric[]>("/data/metrics"),
  uploadDocument: (form: FormData) =>
    request<{ document: { id: string; title: string }; chunk_count: number }>(
      "/knowledge/documents",
      { method: "POST", body: form },
    ),
  knowledgeSearch: (query: string) =>
    request<Array<Record<string, unknown>>>("/knowledge/search", {
      method: "POST",
      body: JSON.stringify({ query, limit: 12 }),
    }),
  automation: {
    listWorkflows: (workspaceId: string) =>
      request<Workflow[]>(`/workspaces/${workspaceId}/workflows`),
    createWorkflow: (workspaceId: string, definition: Record<string, unknown>) =>
      request<{ workflow: Workflow; version: WorkflowVersion }>(
        `/workspaces/${workspaceId}/workflows`,
        { method: "POST", body: JSON.stringify(definition) },
      ),
    listVersions: (workflowId: string) =>
      request<WorkflowVersion[]>(`/workflows/${workflowId}/versions`),
    publishVersion: (workflowId: string, version: number) =>
      request<{ workflow: Workflow; version: WorkflowVersion }>(
        `/workflows/${workflowId}/versions/${version}/publish`,
        { method: "POST" },
      ),
    setStatus: (workflowId: string, action: "pause" | "activate" | "retire") =>
      request<Workflow>(`/workflows/${workflowId}/${action}`, { method: "POST" }),
    listSchedules: (workflowId: string) =>
      request<WorkflowSchedule[]>(`/workflows/${workflowId}/schedules`),
    createSchedule: (workflowId: string, schedule: Record<string, unknown>) =>
      request<WorkflowSchedule>(`/workflows/${workflowId}/schedules`, {
        method: "POST",
        body: JSON.stringify(schedule),
      }),
    setScheduleEnabled: (scheduleId: string, enabled: boolean) =>
      request<WorkflowSchedule>(`/automation/schedules/${scheduleId}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      }),
    trigger: (workflowId: string, inputPayload: Record<string, unknown> = {}) =>
      request<AutomationExecution>(`/workflows/${workflowId}/trigger`, {
        method: "POST",
        body: JSON.stringify({
          input_payload: inputPayload,
          idempotency_key: `web-${crypto.randomUUID()}`,
        }),
      }),
    listExecutions: (workflowId: string) =>
      request<AutomationExecution[]>(`/workflows/${workflowId}/executions?limit=100`),
    getExecution: (executionId: string) =>
      request<AutomationExecution>(`/automation/executions/${executionId}`),
    cancelExecution: (executionId: string) =>
      request<AutomationExecution>(`/automation/executions/${executionId}/cancel`, {
        method: "POST",
      }),
    reviewStep: (stepId: string, decision: "APPROVE" | "REJECT", reason: string) =>
      request<AutomationStep>(`/automation/steps/${stepId}/review`, {
        method: "POST",
        body: JSON.stringify({ decision, reason }),
      }),
    listNotifications: (unreadOnly = false) =>
      request<NotificationDelivery[]>(`/notifications?unread_only=${unreadOnly}&limit=100`),
    markNotificationRead: (notificationId: string) =>
      request<NotificationDelivery>(`/notifications/${notificationId}/read`, {
        method: "POST",
      }),
  },
  actions: {
    list: (workspaceId: string, status?: ActionStatus) =>
      request<ActionRequest[]>(
        `/workspaces/${workspaceId}/actions?limit=200${status ? `&status=${status}` : ""}`,
      ),
    create: (workspaceId: string, definition: Record<string, unknown>) =>
      request<ActionRequest>(`/workspaces/${workspaceId}/actions`, {
        method: "POST",
        body: JSON.stringify(definition),
      }),
    get: (actionId: string) => request<ActionDetail>(`/actions/${actionId}`),
    preflight: (actionId: string, reason: string) =>
      request<ActionDetail>(`/actions/${actionId}/preflight`, {
        method: "POST",
        body: JSON.stringify({ reason, approval_ttl_minutes: 60 }),
      }),
    approvals: (status?: ActionApproval["status"]) =>
      request<ActionApproval[]>(
        `/action-approvals?limit=200${status ? `&status=${status}` : ""}`,
      ),
    decide: (approvalId: string, approve: boolean, reason: string) =>
      request<ActionApproval>(
        `/action-approvals/${approvalId}/${approve ? "approve" : "reject"}`,
        { method: "POST", body: JSON.stringify({ reason }) },
      ),
    rollback: (actionId: string, reason: string) =>
      request<ActionRequest>(`/actions/${actionId}/rollback`, {
        method: "POST",
        body: JSON.stringify({ reason, approval_ttl_minutes: 60 }),
      }),
    cancel: (actionId: string) =>
      request<ActionRequest>(`/actions/${actionId}/cancel`, { method: "POST" }),
  },
  admin: {
    users: () => request<Array<Record<string, unknown>>>("/admin/users"),
    roles: () => request<Array<Record<string, unknown>>>("/admin/roles"),
    departments: () => request<Array<Record<string, unknown>>>("/admin/departments"),
    connectors: () => request<Array<Record<string, unknown>>>("/admin/connectors"),
    capabilities: () => request<Array<Record<string, unknown>>>("/admin/capabilities"),
    modelProfiles: () => request<Array<Record<string, unknown>>>("/admin/models/profiles"),
    agents: () => request<Array<Record<string, unknown>>>("/admin/agents"),
    skills: () => request<Array<Record<string, unknown>>>("/admin/skills"),
    dataSources: () => request<Array<Record<string, unknown>>>("/admin/data/sources"),
    dataCatalog: () => request<Record<string, number>>("/admin/data/catalog"),
    policies: () => request<Array<Record<string, unknown>>>("/admin/policies"),
    approvals: () => request<Array<Record<string, unknown>>>("/approvals"),
    evaluations: () => request<Array<Record<string, unknown>>>("/admin/evaluations/runs"),
    costs: () => request<Array<Record<string, unknown>>>("/admin/costs"),
    prompts: () => request<Array<Record<string, unknown>>>("/admin/prompts"),
    knowledge: () => request<Array<Record<string, unknown>>>("/admin/knowledge"),
    secrets: () => request<Array<Record<string, unknown>>>("/admin/secrets"),
    audit: () => request<Array<Record<string, unknown>>>("/admin/audit?limit=30"),
  },
};
