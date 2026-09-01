import type {
  Artifact,
  Claim,
  CodeRepository,
  CodeSymbolHit,
  ConversationSnapshot,
  Evidence,
  FeedbackSummary,
  ImBinding,
  RuntimeSlo,
  EvalCatalog,
  EvalCase,
  EvalCompare,
  EvalDataset,
  EvalResult,
  EvalRun,
  StudioCatalog,
  StudioCompare,
  StudioValidateResult,
  StudioVersion,
  Metric,
  MetricLineage,
  MemorySnapshot,
  RunFeedback,
  RunFeedbackRating,
  Run,
  RunEvent,
  RunStep,
  SessionPrincipal,
  Thread,
  ThreadEvent,
  Turn,
  Workspace,
  WorkspaceMember,
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
  WorkspaceDecision,
  WorkspaceDecisionVersion,
  WorkspaceTask,
  WorkspaceTaskStatus,
} from "./types";
import { notifyAuthenticationRequired } from "./auth-events";

export const API_URL =
  process.env.NEXT_PUBLIC_OBSION_API_URL ?? "http://localhost:8080/api/v1";

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

interface RequestOptions {
  notifyAuthenticationFailure?: boolean;
  timeoutMs?: number;
}

const SESSION_ERROR_CODES = new Set([
  "authentication_required",
  "invalid_token",
  "unknown_principal",
]);

const DEFAULT_TIMEOUT_MS = 30_000;
const LONG_RUNNING_TIMEOUT_MS = 120_000;

async function request<T>(
  path: string,
  init?: RequestInit,
  options?: RequestOptions,
): Promise<T> {
  const signals = [AbortSignal.timeout(options?.timeoutMs ?? DEFAULT_TIMEOUT_MS)];
  if (init?.signal) signals.push(init.signal);
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      ...init,
      signal: signals.length === 1 ? signals[0] : AbortSignal.any(signals),
      cache: "no-store",
      credentials: "include",
      headers: {
        Accept: "application/json",
        ...(init?.body && !(init.body instanceof FormData) ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });
  } catch (caught) {
    if (caught instanceof DOMException && caught.name === "TimeoutError") {
      throw new ApiError("request_timeout", "请求超时，请稍后重试");
    }
    if (caught instanceof DOMException && caught.name === "AbortError") {
      throw new ApiError("request_cancelled", "请求已取消");
    }
    throw new ApiError("network_error", "无法连接控制面，请检查网络后重试");
  }
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as Record<string, string>;
    const code = body.code ?? "request_failed";
    if (
      options?.notifyAuthenticationFailure !== false &&
      SESSION_ERROR_CODES.has(code)
    ) {
      notifyAuthenticationRequired();
    }
    throw new ApiError(
      code,
      body.message ?? "请求未能完成",
      body.correlation_id,
    );
  }
  if (response.status === 204) return undefined as T;
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError("invalid_response", "控制面返回了无法解析的响应");
  }
}

export const api = {
  createSession: (accessToken: string) =>
    request<SessionPrincipal>(
      "/auth/session",
      {
        method: "POST",
        body: JSON.stringify({ access_token: accessToken }),
      },
      { notifyAuthenticationFailure: false },
    ),
  getSession: () =>
    request<SessionPrincipal>(
      "/auth/session",
      undefined,
      { notifyAuthenticationFailure: false },
    ),
  deleteSession: () =>
    request<void>(
      "/auth/session",
      { method: "DELETE" },
      { notifyAuthenticationFailure: false },
    ),
  listWorkspaces: () => request<Workspace[]>("/workspaces"),
  createWorkspace: (name: string, description: string) =>
    request<Workspace>("/workspaces", {
      method: "POST",
      body: JSON.stringify({ name, description }),
    }),
  listThreads: (workspaceId: string, includeArchived = false) =>
    request<Thread[]>(
      `/workspaces/${workspaceId}/threads?include_archived=${includeArchived}`,
    ),
  listWorkspaceMembers: (workspaceId: string) =>
    request<WorkspaceMember[]>(`/workspaces/${workspaceId}/members`),
  createThread: (workspaceId: string, title: string) =>
    request<Thread>("/threads", {
      method: "POST",
      body: JSON.stringify({ workspace_id: workspaceId, title }),
    }),
  archiveThread: (threadId: string) =>
    request<Thread>(`/threads/${threadId}/archive`, { method: "POST" }),
  resumeThread: (threadId: string) =>
    request<Thread>(`/threads/${threadId}/resume`, { method: "POST" }),
  forkThread: (
    threadId: string,
    input: { title: string; from_turn_id?: string },
  ) => request<Thread>(`/threads/${threadId}/fork`, {
    method: "POST",
    body: JSON.stringify(input),
  }),
  listThreadEvents: (threadId: string, afterSequence = 0) =>
    request<ThreadEvent[]>(
      `/threads/${threadId}/events?after_sequence=${afterSequence}&limit=200`,
    ),
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
  getRunFeedback: (runId: string) => request<RunFeedback | null>(`/runs/${runId}/feedback`),
  recordRunFeedback: (
    runId: string,
    input: { rating: RunFeedbackRating; reason?: string; expected_version?: number },
  ) => request<RunFeedback>(`/runs/${runId}/feedback`, {
    method: "PUT",
    body: JSON.stringify({ reason: "", ...input }),
  }),
  listEvents: (runId: string, after = 0) =>
    request<RunEvent[]>(`/runs/${runId}/events?after=${after}`),
  listWorkspaceTimeline: (workspaceId: string, limit = 500) =>
    request<RunEvent[]>(`/workspaces/${workspaceId}/timeline?limit=${limit}`),
  listSteps: (runId: string) => request<RunStep[]>(`/runs/${runId}/steps`),
  listEvidence: (runId: string) => request<Evidence[]>(`/runs/${runId}/evidence`),
  listWorkspaceEvidence: (workspaceId: string) =>
    request<Evidence[]>(`/workspaces/${workspaceId}/evidence`),
  listRunMemories: (runId: string) =>
    request<MemorySnapshot[]>(`/runs/${runId}/memories`),
  listRunConversation: (runId: string) =>
    request<ConversationSnapshot[]>(`/runs/${runId}/conversation`),
  listClaims: (runId: string) => request<Claim[]>(`/runs/${runId}/claims`),
  listArtifacts: (runId: string) => request<Artifact[]>(`/runs/${runId}/artifacts`),
  listWorkspaceArtifacts: (workspaceId: string) =>
    request<Artifact[]>(`/workspaces/${workspaceId}/artifacts`),
  listWorkspaceFiles: (workspaceId: string, includeSuperseded = false) =>
    request<Artifact[]>(
      `/workspaces/${workspaceId}/files?include_superseded=${includeSuperseded}`,
    ),
  listWorkspaceReports: (workspaceId: string) =>
    request<Artifact[]>(`/workspaces/${workspaceId}/reports`),
  listWorkspaceDashboards: (workspaceId: string) =>
    request<Artifact[]>(`/workspaces/${workspaceId}/dashboards`),
  listWorkspaceSql: (workspaceId: string) =>
    request<Artifact[]>(`/workspaces/${workspaceId}/sql`),
  getArtifact: (artifactId: string) =>
    request<Artifact>(`/artifacts/${artifactId}`),
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
      if (SESSION_ERROR_CODES.has(body.code ?? "")) notifyAuthenticationRequired();
      throw new ApiError(
        body.code ?? "artifact_download_failed",
        body.message ?? "产物下载失败",
        body.correlation_id,
      );
    }
    return response.blob();
  },
  listMetrics: () => request<Metric[]>("/data/metrics"),
  getMetricLineage: (metricId: string) =>
    request<MetricLineage>(`/data/lineage/${metricId}`),
  uploadDocument: (form: FormData) =>
    request<{ document: { id: string; title: string }; chunk_count: number }>(
      "/knowledge/documents",
      { method: "POST", body: form },
      { timeoutMs: LONG_RUNNING_TIMEOUT_MS },
    ),
  knowledgeSearch: (query: string) =>
    request<
      Array<{
        chunk_id: string;
        document_id: string;
        version: number;
        title: string;
        source: string;
        heading_path: string[];
        content: string;
        score: number;
        classification: string;
        external_id?: string | null;
        revision_id?: string | null;
        connector_name?: string | null;
        operation?: string | null;
      }>
    >("/knowledge/search", {
      method: "POST",
      body: JSON.stringify({ query, limit: 12 }),
    }),
  ingestFeishuDocument: (input: {
    document_id: string;
    obj_type?: "auto" | "docx" | "wiki";
    title?: string;
    classification?: string;
    acl?: Record<string, unknown>;
    inherit_acl?: boolean;
  }) =>
    request<{
      document: { id: string; title: string };
      chunk_count: number;
      source: string;
      external_id: string;
    }>("/knowledge/sources/feishu/documents", {
      method: "POST",
      body: JSON.stringify({
        obj_type: "auto",
        classification: "INTERNAL",
        acl: { organization: true },
        inherit_acl: false,
        ...input,
      }),
    }, { timeoutMs: LONG_RUNNING_TIMEOUT_MS }),
  ingestDingTalkDocument: (input: {
    document_id: string;
    title?: string;
    classification?: string;
    acl?: Record<string, unknown>;
    inherit_acl?: boolean;
  }) =>
    request<{
      document: { id: string; title: string };
      chunk_count: number;
      source: string;
      external_id: string;
    }>("/knowledge/sources/dingtalk/documents", {
      method: "POST",
      body: JSON.stringify({
        classification: "INTERNAL",
        acl: { organization: true },
        inherit_acl: false,
        ...input,
      }),
    }, { timeoutMs: LONG_RUNNING_TIMEOUT_MS }),
  ingestWeComDocument: (input: {
    document_id: string;
    title?: string;
    classification?: string;
    acl?: Record<string, unknown>;
    inherit_acl?: boolean;
  }) =>
    request<{
      document: { id: string; title: string };
      chunk_count: number;
      source: string;
      external_id: string;
    }>("/knowledge/sources/wecom/documents", {
      method: "POST",
      body: JSON.stringify({
        classification: "INTERNAL",
        acl: { organization: true },
        inherit_acl: false,
        ...input,
      }),
    }, { timeoutMs: LONG_RUNNING_TIMEOUT_MS }),
  listFeishuSpaces: () =>
    request<Array<{ space_id: string; name: string; description: string }>>(
      "/knowledge/sources/feishu/spaces",
    ),
  listFeishuWikiNodes: (spaceId: string) =>
    request<
      Array<{
        space_id: string;
        node_token: string;
        obj_token: string;
        obj_type: string;
        title: string;
      }>
    >(`/knowledge/sources/feishu/spaces/${encodeURIComponent(spaceId)}/nodes`),
  syncFeishuSpace: (
    spaceId: string,
    input: {
      classification?: string;
      acl?: Record<string, unknown>;
      inherit_acl?: boolean;
    } = {},
  ) =>
    request<{
      space_id: string;
      ingested_count: number;
      skipped_count: number;
      failed_count: number;
    }>(`/knowledge/sources/feishu/spaces/${encodeURIComponent(spaceId)}/sync`, {
      method: "POST",
      body: JSON.stringify({
        classification: "INTERNAL",
        acl: { organization: true },
        inherit_acl: false,
        ...input,
      }),
    }, { timeoutMs: LONG_RUNNING_TIMEOUT_MS }),
  ingestConfluencePage: (input: {
    page_id: string;
    title?: string;
    classification?: string;
    acl?: Record<string, unknown>;
    inherit_acl?: boolean;
  }) =>
    request<{
      document: { id: string; title: string };
      chunk_count: number;
      source: string;
      external_id: string;
    }>("/knowledge/sources/confluence/pages", {
      method: "POST",
      body: JSON.stringify({
        classification: "INTERNAL",
        acl: { organization: true },
        inherit_acl: false,
        ...input,
      }),
    }, { timeoutMs: LONG_RUNNING_TIMEOUT_MS }),
  listCodeRepositories: () => request<CodeRepository[]>("/code/repositories"),
  searchCodeSymbols: (query: string) =>
    request<CodeSymbolHit[]>("/code/symbols/search", {
      method: "POST",
      body: JSON.stringify({ query, limit: 20 }),
    }),
  collaboration: {
    listTasks: (workspaceId: string, status?: WorkspaceTaskStatus) =>
      request<WorkspaceTask[]>(
        `/workspaces/${workspaceId}/tasks?limit=500${status ? `&status=${status}` : ""}`,
      ),
    createTask: (workspaceId: string, definition: Record<string, unknown>) =>
      request<WorkspaceTask>(`/workspaces/${workspaceId}/tasks`, {
        method: "POST",
        body: JSON.stringify(definition),
      }),
    updateTask: (taskId: string, definition: Record<string, unknown>) =>
      request<WorkspaceTask>(`/workspace-tasks/${taskId}`, {
        method: "PATCH",
        body: JSON.stringify(definition),
      }),
    listDecisions: (workspaceId: string) =>
      request<WorkspaceDecision[]>(`/workspaces/${workspaceId}/decisions?limit=500`),
    createDecision: (workspaceId: string, definition: Record<string, unknown>) =>
      request<WorkspaceDecision>(`/workspaces/${workspaceId}/decisions`, {
        method: "POST",
        body: JSON.stringify(definition),
      }),
    reviseDecision: (decisionId: string, definition: Record<string, unknown>) =>
      request<WorkspaceDecision>(`/workspace-decisions/${decisionId}`, {
        method: "PATCH",
        body: JSON.stringify(definition),
      }),
    decide: (decisionId: string, approve: boolean, expectedVersion: number) =>
      request<WorkspaceDecision>(
        `/workspace-decisions/${decisionId}/${approve ? "accept" : "reject"}`,
        { method: "POST", body: JSON.stringify({ expected_version: expectedVersion }) },
      ),
    versions: (decisionId: string) =>
      request<WorkspaceDecisionVersion[]>(`/workspace-decisions/${decisionId}/versions`),
  },
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
    createVersion: (workflowId: string, spec: Record<string, unknown>) =>
      request<WorkflowVersion>(`/workflows/${workflowId}/versions`, {
        method: "POST",
        body: JSON.stringify({ spec }),
      }),
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
    trigger: (
      workflowId: string,
      inputPayload: Record<string, unknown> = {},
      idempotencyKey?: string,
    ) =>
      request<AutomationExecution>(`/workflows/${workflowId}/trigger`, {
        method: "POST",
        body: JSON.stringify({
          input_payload: inputPayload,
          idempotency_key: idempotencyKey ?? `web-${crypto.randomUUID()}`,
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
    probeConnectorHealth: (connectorId: string) =>
      request<Record<string, unknown>>(`/admin/connectors/${connectorId}/health`, {
        method: "POST",
      }),
    discoverConnector: (connectorId: string) =>
      request<Record<string, unknown>>(`/admin/connectors/${connectorId}/discover`, {
        method: "POST",
      }),
    scanConnectorPlugin: (connectorId: string) =>
      request<Record<string, unknown>>(`/admin/connectors/${connectorId}/scan`, {
        method: "POST",
      }),
    promoteConnectorPlugin: (connectorId: string) =>
      request<Record<string, unknown>>(`/admin/connectors/${connectorId}/promote`, {
        method: "POST",
      }),
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
    feedbackSummary: () => request<FeedbackSummary>("/admin/feedback/summary"),
    runtimeSlo: () => request<RuntimeSlo>("/admin/slo"),
    prompts: () => request<Array<Record<string, unknown>>>("/admin/prompts"),
    knowledge: () => request<Array<Record<string, unknown>>>("/admin/knowledge"),
    secrets: () => request<Array<Record<string, unknown>>>("/admin/secrets"),
    audit: () => request<Array<Record<string, unknown>>>("/admin/audit?limit=30"),
    operatorInvocations: () =>
      request<Array<Record<string, unknown>>>("/admin/operator-invocations?limit=30"),
    imBindings: () => request<ImBinding[]>("/admin/im-bindings"),
    createImBinding: (input: { channel: string; sender_id: string; user_id: string }) =>
      request<ImBinding>("/admin/im-bindings", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    revokeImBinding: (bindingId: string) =>
      request<ImBinding>(`/admin/im-bindings/${bindingId}/revoke`, { method: "POST" }),
  },
  studio: {
    catalog: () => request<StudioCatalog>("/studio/catalog"),
    validate: (document: string) =>
      request<StudioValidateResult>("/studio/validate", {
        method: "POST",
        body: JSON.stringify({ document }),
      }),
    publishAgent: (document: string) =>
      request<StudioVersion>("/studio/agents", {
        method: "POST",
        body: JSON.stringify({ document }),
      }),
    publishSkill: (document: string) =>
      request<StudioVersion>("/studio/skills", {
        method: "POST",
        body: JSON.stringify({ document }),
      }),
    promote: (input: { kind: string; name: string; version: number }) =>
      request<StudioVersion>("/studio/promote", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    rollback: (input: { kind: string; name: string; version: number }) =>
      request<StudioVersion>("/studio/rollback", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    compare: (input: {
      kind: string;
      name: string;
      baseline_version: number;
      candidate_version: number;
    }) =>
      request<StudioCompare>("/studio/compare", {
        method: "POST",
        body: JSON.stringify(input),
      }),
  },
  eval: {
    catalog: () => request<EvalCatalog>("/eval/catalog"),
    createDataset: (input: { name: string; domain: string; description?: string }) =>
      request<EvalDataset>("/eval/datasets", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    cases: (datasetId: string) => request<EvalCase[]>(`/eval/datasets/${datasetId}/cases`),
    addCase: (datasetId: string, input: Record<string, unknown>) =>
      request<EvalCase>(`/eval/datasets/${datasetId}/cases`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    startRun: (
      datasetId: string,
      input: {
        agent_version_id: string;
        model_profile_id: string;
        application_revision: string;
        baseline_run_id?: string;
        run_bindings?: Record<string, string>;
        prompt_pins?: Record<string, number>;
      },
    ) =>
      request<EvalRun>(`/eval/datasets/${datasetId}/runs`, {
        method: "POST",
        body: JSON.stringify(input),
      }, { timeoutMs: LONG_RUNNING_TIMEOUT_MS }),
    results: (runId: string) => request<EvalResult[]>(`/eval/runs/${runId}/results`),
    compare: (input: { baseline_run_id: string; candidate_run_id: string }) =>
      request<EvalCompare>("/eval/compare", {
        method: "POST",
        body: JSON.stringify(input),
      }),
  },
};
