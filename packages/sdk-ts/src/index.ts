export type RunStatus =
  | "PENDING"
  | "RUNNING"
  | "WAITING_APPROVAL"
  | "WAITING_USER"
  | "REPLANNING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export interface Workspace {
  id: string;
  name: string;
  description: string;
  owner_id: string;
  classification: string;
  visibility: string;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export interface Run {
  id: string;
  turn_id: string;
  status: RunStatus;
  intent: Record<string, unknown>;
  plan: Record<string, unknown>;
  max_steps: number;
  timeout_seconds: number;
  max_input_tokens: number;
  max_output_tokens: number;
  max_cost_amount: string;
  step_count: number;
  input_tokens: number;
  output_tokens: number;
  cost_amount: string;
  started_at: string | null;
  completed_at: string | null;
  cancellation_requested_at: string | null;
  error_code: string | null;
  error_message: string | null;
  replay_of_run_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface Thread {
  id: string;
  workspace_id: string;
  title: string;
  status: "ACTIVE" | "ARCHIVED";
  created_at: string;
  updated_at: string;
}

export interface Turn {
  id: string;
  thread_id: string;
  ordinal: number;
  created_by: string;
  input_text: string;
  context_refs: Array<Record<string, unknown>>;
  attachment_refs: Array<Record<string, unknown>>;
  created_at: string;
}

export interface Artifact {
  id: string;
  workspace_id: string;
  run_id: string | null;
  kind:
    | "TEXT"
    | "TABLE"
    | "CHART"
    | "SQL"
    | "CODE"
    | "DIFF"
    | "REPORT"
    | "DASHBOARD"
    | "FILE"
    | "DIAGRAM";
  title: string;
  media_type: string;
  inline_content: Record<string, unknown> | null;
  storage_key: string | null;
  classification: string;
  lineage: Record<string, unknown>;
  created_at: string;
}

export interface Capability {
  id: string;
  version_id: string;
  name: string;
  display_name: string;
  description: string;
  version: number;
  transport: string;
  risk: string;
  side_effect: string;
  permission: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
}

export interface KnowledgeHit {
  chunk_id: string;
  document_id: string;
  version: number;
  title: string;
  source: string;
  heading_path: string[];
  content: string;
  score: number;
  classification: string;
}

export interface RunEvent {
  id: string;
  sequence: number;
  name: string;
  run_id: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export type WorkflowStatus = "DRAFT" | "ACTIVE" | "PAUSED" | "RETIRED";
export type AutomationStatus =
  | "PENDING"
  | "RUNNING"
  | "WAITING_REVIEW"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "SKIPPED";

export interface Workflow {
  id: string;
  workspace_id: string;
  name: string;
  display_name: string;
  description: string;
  status: WorkflowStatus;
  owner_id: string;
  active_version: number | null;
  concurrency_policy: "FORBID" | "ALLOW" | "REPLACE";
  max_concurrency: number;
  timeout_seconds: number;
  notify_on_success: boolean;
  notify_on_failure: boolean;
  classification: string;
  created_at: string;
  updated_at: string;
}

export interface WorkflowVersion {
  id: string;
  workflow_id: string;
  version: number;
  spec: Record<string, unknown>;
  checksum_sha256: string;
  created_by: string;
  created_at: string;
  published_at: string | null;
}

export interface WorkflowSchedule {
  id: string;
  workspace_id: string;
  workflow_id: string;
  workflow_version_id: string;
  name: string;
  cron_expression: string;
  timezone: string;
  misfire_policy: "SKIP" | "FIRE_ONCE";
  misfire_grace_seconds: number;
  input_payload: Record<string, unknown>;
  owner_id: string;
  enabled: boolean;
  next_fire_at: string;
  last_fire_at: string | null;
  last_error_code: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface AutomationStep {
  id: string;
  execution_id: string;
  step_key: string;
  ordinal: number;
  name: string;
  step_type: "ANALYSIS" | "HUMAN_REVIEW" | "NOTIFICATION";
  depends_on: string[];
  status: AutomationStatus;
  run_id: string | null;
  output_refs: Array<Record<string, unknown>>;
  review_decision: "APPROVE" | "REJECT" | null;
  reviewed_by: string | null;
  review_reason: string | null;
  reviewed_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface AutomationExecution {
  id: string;
  workspace_id: string;
  workflow_id: string;
  workflow_version_id: string;
  schedule_id: string | null;
  trigger: "MANUAL" | "SCHEDULE";
  scheduled_for: string | null;
  idempotency_key: string;
  status: AutomationStatus;
  owner_id: string;
  input_payload: Record<string, unknown>;
  max_duration_seconds: number;
  deadline_at: string;
  started_at: string | null;
  completed_at: string | null;
  cancellation_requested_at: string | null;
  error_code: string | null;
  error_message: string | null;
  summary: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  steps?: AutomationStep[];
}

export interface Notification {
  id: string;
  workspace_id: string;
  execution_id: string | null;
  action_request_id: string | null;
  step_execution_id: string | null;
  recipient_id: string;
  title: string;
  body: string;
  payload: Record<string, unknown>;
  status: "DELIVERED" | "READ";
  delivered_at: string;
  read_at: string | null;
  created_at: string;
}

export type ActionType =
  | "GENERATE_PR"
  | "CREATE_TICKET"
  | "MODIFY_CONFIG"
  | "RESTART_SERVICE"
  | "DEPLOY";

export type ActionStatus =
  | "DRAFT"
  | "PREFLIGHT_FAILED"
  | "WAITING_APPROVAL"
  | "APPROVED"
  | "EXECUTING"
  | "COMPLETED"
  | "FAILED"
  | "WAITING_ROLLBACK_APPROVAL"
  | "ROLLBACK_APPROVED"
  | "ROLLING_BACK"
  | "ROLLED_BACK"
  | "ROLLBACK_FAILED"
  | "ROLLBACK_REJECTED"
  | "REJECTED"
  | "EXPIRED"
  | "CANCELLED";

export interface ActionRequest {
  id: string;
  workspace_id: string;
  action_type: ActionType;
  title: string;
  description: string;
  environment: string;
  target: Record<string, unknown>;
  parameters: Record<string, unknown>;
  rollback_parameters: Record<string, unknown>;
  status: ActionStatus;
  owner_id: string;
  requested_by: string;
  idempotency_key: string;
  timeout_seconds: number;
  deadline_at: string | null;
  plan_checksum_sha256: string | null;
  preflight: Record<string, unknown>;
  result: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface ActionApproval {
  id: string;
  action_request_id: string;
  purpose: "EXECUTE" | "ROLLBACK";
  revision: number;
  plan_checksum_sha256: string;
  status: "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED" | "CANCELLED";
  reason: string;
  requested_by: string;
  decided_by: string | null;
  decision_reason: string | null;
  expires_at: string;
  decided_at: string | null;
}

export interface ActionAttempt {
  id: string;
  purpose: "EXECUTE" | "ROLLBACK";
  ordinal: number;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
  capability_version_id: string;
  connector_id: string;
  approval_id: string;
  policy_decision_id: string | null;
  idempotency_key: string;
  output: Record<string, unknown>;
  error_code: string | null;
  error_message: string | null;
}

export interface ActionDetail {
  action: ActionRequest;
  plan: {
    id: string;
    spec: Record<string, unknown>;
    checksum_sha256: string;
    created_at: string;
  } | null;
  approvals: ActionApproval[];
  attempts: ActionAttempt[];
}

export class ObsionApiError extends Error {
  constructor(
    readonly statusCode: number,
    readonly code: string,
    message: string,
    readonly correlationId: string,
  ) {
    super(message);
    this.name = "ObsionApiError";
  }
}

export class ObsionClient {
  constructor(
    private readonly baseUrl: string,
    private readonly getToken?: () => string | Promise<string>,
  ) {}

  listWorkspaces(includeArchived = false): Promise<Workspace[]> {
    return this.request(`/api/v1/workspaces?include_archived=${includeArchived}`);
  }

  createWorkspace(input: { name: string; description?: string }): Promise<Workspace> {
    return this.request("/api/v1/workspaces", { method: "POST", body: JSON.stringify(input) });
  }

  createThread(workspaceId: string, title: string): Promise<Thread> {
    return this.request("/api/v1/threads", {
      method: "POST",
      body: JSON.stringify({ workspace_id: workspaceId, title }),
    });
  }

  listThreads(workspaceId: string): Promise<Thread[]> {
    return this.request(`/api/v1/workspaces/${workspaceId}/threads`);
  }

  listTurns(threadId: string): Promise<Turn[]> {
    return this.request(`/api/v1/threads/${threadId}/turns`);
  }

  listThreadRuns(threadId: string): Promise<Run[]> {
    return this.request(`/api/v1/threads/${threadId}/runs`);
  }

  createTurn(
    threadId: string,
    input: string,
    options: {
      contextRefs?: Array<Record<string, unknown>>;
      attachmentRefs?: Array<Record<string, unknown>>;
      modelProfile?: string;
    } = {},
  ): Promise<{ turn: Turn; run: Run }> {
    return this.request(`/api/v1/threads/${threadId}/turns`, {
      method: "POST",
      body: JSON.stringify({
        input,
        context_refs: options.contextRefs ?? [],
        attachment_refs: options.attachmentRefs ?? [],
        ...(options.modelProfile ? { model_profile: options.modelProfile } : {}),
      }),
    });
  }

  getRun(runId: string): Promise<Run> {
    return this.request(`/api/v1/runs/${runId}`);
  }

  cancelRun(runId: string): Promise<Run> {
    return this.request(`/api/v1/runs/${runId}/cancel`, { method: "POST" });
  }

  replayRun(runId: string): Promise<Run> {
    return this.request(`/api/v1/runs/${runId}/replay`, { method: "POST" });
  }

  listEvents(runId: string, after = 0): Promise<RunEvent[]> {
    return this.request(`/api/v1/runs/${runId}/events?after=${after}`);
  }

  listRunSteps(runId: string): Promise<Array<Record<string, unknown>>> {
    return this.request(`/api/v1/runs/${runId}/steps`);
  }

  listRunEvidence(runId: string): Promise<Array<Record<string, unknown>>> {
    return this.request(`/api/v1/runs/${runId}/evidence`);
  }

  listRunClaims(runId: string): Promise<Array<Record<string, unknown>>> {
    return this.request(`/api/v1/runs/${runId}/claims`);
  }

  listRunArtifacts(runId: string): Promise<Artifact[]> {
    return this.request(`/api/v1/runs/${runId}/artifacts`);
  }

  getArtifact(artifactId: string): Promise<Artifact> {
    return this.request(`/api/v1/artifacts/${artifactId}`);
  }

  listWorkspaceArtifacts(workspaceId: string): Promise<Artifact[]> {
    return this.request(`/api/v1/workspaces/${workspaceId}/artifacts`);
  }

  async uploadArtifact(
    workspaceId: string,
    input: {
      title: string;
      filename: string;
      content: Blob;
      kind?: Artifact["kind"];
      classification?: string;
      runId?: string;
      lineage?: Record<string, unknown>;
    },
  ): Promise<Artifact> {
    const form = new FormData();
    form.set("file", input.content, input.filename);
    form.set("title", input.title);
    form.set("kind", input.kind ?? "FILE");
    form.set("classification", input.classification ?? "INTERNAL");
    form.set("lineage", JSON.stringify(input.lineage ?? {}));
    if (input.runId) form.set("run_id", input.runId);
    return this.request(`/api/v1/workspaces/${workspaceId}/artifacts`, {
      method: "POST",
      body: form,
    });
  }

  async downloadArtifact(artifactId: string): Promise<ArrayBuffer> {
    const response = await this.fetchResponse(`/api/v1/artifacts/${artifactId}/content`);
    return response.arrayBuffer();
  }

  listCapabilities(): Promise<Capability[]> {
    return this.request("/api/v1/capabilities");
  }

  getCapability(capabilityId: string): Promise<Capability> {
    return this.request(`/api/v1/capabilities/${capabilityId}`);
  }

  invokeCapability(
    name: string,
    input: {
      run_id: string;
      payload: Record<string, unknown>;
      resource: Record<string, unknown>;
      environment: string;
      agent_name?: string;
      step_id?: string;
      capability_version?: number;
    },
  ): Promise<Record<string, unknown>> {
    return this.request(`/api/v1/capabilities/${encodeURIComponent(name)}/invoke`, {
      method: "POST",
      body: JSON.stringify({ agent_name: "general-agent", ...input }),
    });
  }

  queryData(
    threadId: string,
    question: string,
    modelProfile?: string,
  ): Promise<{ turn: Turn; run: Run }> {
    return this.request("/api/v1/data/query", {
      method: "POST",
      body: JSON.stringify({
        thread_id: threadId,
        question,
        ...(modelProfile ? { model_profile: modelProfile } : {}),
      }),
    });
  }

  searchKnowledge(query: string, limit = 8): Promise<KnowledgeHit[]> {
    return this.request("/api/v1/knowledge/search", {
      method: "POST",
      body: JSON.stringify({ query, limit }),
    });
  }

  createWorkflow(
    workspaceId: string,
    definition: Record<string, unknown>,
  ): Promise<{ workflow: Workflow; version: WorkflowVersion }> {
    return this.request(`/api/v1/workspaces/${workspaceId}/workflows`, {
      method: "POST",
      body: JSON.stringify(definition),
    });
  }

  listWorkflows(workspaceId: string): Promise<Workflow[]> {
    return this.request(`/api/v1/workspaces/${workspaceId}/workflows`);
  }

  getWorkflow(workflowId: string): Promise<Workflow> {
    return this.request(`/api/v1/workflows/${workflowId}`);
  }

  createWorkflowVersion(
    workflowId: string,
    spec: Record<string, unknown>,
  ): Promise<WorkflowVersion> {
    return this.request(`/api/v1/workflows/${workflowId}/versions`, {
      method: "POST",
      body: JSON.stringify({ spec }),
    });
  }

  publishWorkflowVersion(
    workflowId: string,
    version: number,
  ): Promise<{ workflow: Workflow; version: WorkflowVersion }> {
    return this.request(`/api/v1/workflows/${workflowId}/versions/${version}/publish`, {
      method: "POST",
    });
  }

  setWorkflowStatus(
    workflowId: string,
    action: "pause" | "activate" | "retire",
  ): Promise<Workflow> {
    return this.request(`/api/v1/workflows/${workflowId}/${action}`, { method: "POST" });
  }

  createSchedule(
    workflowId: string,
    schedule: Record<string, unknown>,
  ): Promise<WorkflowSchedule> {
    return this.request(`/api/v1/workflows/${workflowId}/schedules`, {
      method: "POST",
      body: JSON.stringify(schedule),
    });
  }

  listSchedules(workflowId: string): Promise<WorkflowSchedule[]> {
    return this.request(`/api/v1/workflows/${workflowId}/schedules`);
  }

  setScheduleEnabled(scheduleId: string, enabled: boolean): Promise<WorkflowSchedule> {
    return this.request(`/api/v1/automation/schedules/${scheduleId}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled }),
    });
  }

  triggerWorkflow(
    workflowId: string,
    input: { inputPayload?: Record<string, unknown>; idempotencyKey?: string } = {},
  ): Promise<AutomationExecution> {
    return this.request(`/api/v1/workflows/${workflowId}/trigger`, {
      method: "POST",
      body: JSON.stringify({
        input_payload: input.inputPayload ?? {},
        idempotency_key: input.idempotencyKey ?? null,
      }),
    });
  }

  listWorkflowExecutions(workflowId: string, limit = 100): Promise<AutomationExecution[]> {
    return this.request(`/api/v1/workflows/${workflowId}/executions?limit=${limit}`);
  }

  getAutomationExecution(executionId: string): Promise<AutomationExecution> {
    return this.request(`/api/v1/automation/executions/${executionId}`);
  }

  cancelAutomationExecution(executionId: string): Promise<AutomationExecution> {
    return this.request(`/api/v1/automation/executions/${executionId}/cancel`, {
      method: "POST",
    });
  }

  reviewAutomationStep(
    stepId: string,
    decision: "APPROVE" | "REJECT",
    reason: string,
  ): Promise<AutomationStep> {
    return this.request(`/api/v1/automation/steps/${stepId}/review`, {
      method: "POST",
      body: JSON.stringify({ decision, reason }),
    });
  }

  listNotifications(unreadOnly = false, limit = 100): Promise<Notification[]> {
    return this.request(`/api/v1/notifications?unread_only=${unreadOnly}&limit=${limit}`);
  }

  markNotificationRead(notificationId: string): Promise<Notification> {
    return this.request(`/api/v1/notifications/${notificationId}/read`, { method: "POST" });
  }

  createAction(
    workspaceId: string,
    input: {
      action_type: ActionType;
      title: string;
      description?: string;
      environment: string;
      target: Record<string, unknown>;
      parameters: Record<string, unknown>;
      rollback_parameters?: Record<string, unknown>;
      owner_id?: string;
      idempotency_key: string;
      timeout_seconds?: number;
    },
  ): Promise<ActionRequest> {
    return this.request(`/api/v1/workspaces/${workspaceId}/actions`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  listActions(workspaceId: string, status?: ActionStatus, limit = 100): Promise<ActionRequest[]> {
    const query = new URLSearchParams({ limit: String(limit) });
    if (status) query.set("status", status);
    return this.request(`/api/v1/workspaces/${workspaceId}/actions?${query}`);
  }

  getAction(actionId: string): Promise<ActionDetail> {
    return this.request(`/api/v1/actions/${actionId}`);
  }

  preflightAction(
    actionId: string,
    reason: string,
    approvalTtlMinutes = 60,
  ): Promise<ActionDetail> {
    return this.request(`/api/v1/actions/${actionId}/preflight`, {
      method: "POST",
      body: JSON.stringify({ reason, approval_ttl_minutes: approvalTtlMinutes }),
    });
  }

  listActionApprovals(status?: ActionApproval["status"], limit = 200): Promise<ActionApproval[]> {
    const query = new URLSearchParams({ limit: String(limit) });
    if (status) query.set("status", status);
    return this.request(`/api/v1/action-approvals?${query}`);
  }

  decideActionApproval(
    approvalId: string,
    approve: boolean,
    reason: string,
  ): Promise<ActionApproval> {
    return this.request(
      `/api/v1/action-approvals/${approvalId}/${approve ? "approve" : "reject"}`,
      { method: "POST", body: JSON.stringify({ reason }) },
    );
  }

  requestActionRollback(
    actionId: string,
    reason: string,
    approvalTtlMinutes = 60,
  ): Promise<ActionRequest> {
    return this.request(`/api/v1/actions/${actionId}/rollback`, {
      method: "POST",
      body: JSON.stringify({ reason, approval_ttl_minutes: approvalTtlMinutes }),
    });
  }

  cancelAction(actionId: string): Promise<ActionRequest> {
    return this.request(`/api/v1/actions/${actionId}/cancel`, { method: "POST" });
  }

  listActionEvents(actionId: string, after = 0, limit = 500): Promise<RunEvent[]> {
    return this.request(`/api/v1/actions/${actionId}/events?after=${after}&limit=${limit}`);
  }

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await this.fetchResponse(path, init);
    return (await response.json()) as T;
  }

  private async fetchResponse(path: string, init: RequestInit = {}): Promise<Response> {
    const token = this.getToken ? await this.getToken() : undefined;
    const response = await fetch(`${this.baseUrl.replace(/\/$/, "")}${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(typeof init.body === "string" ? { "Content-Type": "application/json" } : {}),
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...init.headers,
      },
    });
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as Record<string, string>;
      throw new ObsionApiError(
        response.status,
        body.code ?? "http_error",
        body.message ?? "Obsion API request failed",
        body.correlation_id ?? response.headers.get("X-Request-ID") ?? "",
      );
    }
    return response;
  }
}
