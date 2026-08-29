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
  agent_version_id: string | null;
  model_profile_id: string | null;
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
  created_by: string;
  parent_thread_id: string | null;
  forked_from_turn_id: string | null;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
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

export interface Metric {
  id: string;
  name: string;
  display_name: string;
  version: number;
  expression: string;
  filters: Record<string, unknown>;
  time_column: string;
  source_table_id: string;
  owner: string;
  synonyms: string[];
  validated: boolean;
  created_at: string;
  updated_at: string;
}

export interface MetricLineage {
  metric: { id: string; name: string; version: number };
  table: { id: string; name: string; owner: string };
  data_source: { id: string; name: string; environment: string; read_only: boolean };
}

export interface RunEvent {
  id: string;
  event_id: string;
  organization_id: string;
  aggregate_type: string;
  aggregate_id: string;
  sequence: number;
  name: string;
  run_id: string | null;
  run_sequence: number | null;
  causation_id: string | null;
  correlation_id: string;
  actor_type: "USER" | "SERVICE" | "AGENT" | "SYSTEM";
  actor_id: string | null;
  schema_version: number;
  classification: "PUBLIC" | "INTERNAL" | "CONFIDENTIAL" | "RESTRICTED";
  payload: Record<string, unknown>;
  created_at: string;
}

export const APP_SERVER_PROTOCOL_VERSION = "2026-08-26";
export const APP_SERVER_SUBPROTOCOL = "obsion.jsonrpc.v1";

export interface AppServerNotification {
  jsonrpc: "2.0";
  method: string;
  params: Record<string, unknown>;
}

export interface AppServerWebSocket {
  readonly readyState: number;
  readonly protocol?: string;
  onopen: ((event: unknown) => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  onclose: ((event: { code?: number; reason?: string }) => void) | null;
  send(data: string): void;
  close(code?: number, reason?: string): void;
}

export type AppServerWebSocketFactory = (
  url: string,
  protocols: string[],
) => AppServerWebSocket;

export class ObsionAppServerError extends Error {
  constructor(
    public readonly rpcCode: number,
    message: string,
    public readonly code?: string,
    public readonly status?: number,
    public readonly correlationId?: string,
    public readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ObsionAppServerError";
  }
}

interface PendingAppServerRequest {
  resolve: (value: unknown) => void;
  reject: (reason: unknown) => void;
}

export class ObsionAppServerClient {
  private socket: AppServerWebSocket | null = null;
  private nextId = 1;
  private readonly pending = new Map<number, PendingAppServerRequest>();
  private readonly notificationListeners = new Set<
    (notification: AppServerNotification) => void
  >();
  private readyResolve: ((notification: AppServerNotification) => void) | null = null;
  private readyReject: ((reason: unknown) => void) | null = null;

  constructor(
    private readonly url: string,
    private readonly options: {
      token?: string;
      clientName?: string;
      clientVersion?: string;
      webSocketFactory?: AppServerWebSocketFactory;
    } = {},
  ) {}

  async connect(): Promise<Record<string, unknown>> {
    if (this.socket !== null) throw new Error("The App Server client is already connected");
    const factory = this.options.webSocketFactory ?? defaultAppServerWebSocketFactory;
    const ready = new Promise<AppServerNotification>((resolve, reject) => {
      this.readyResolve = resolve;
      this.readyReject = reject;
    });
    const opened = new Promise<void>((resolve, reject) => {
      const socket = factory(this.url, [APP_SERVER_SUBPROTOCOL]);
      this.socket = socket;
      socket.onmessage = (event) => this.handleMessage(event.data);
      socket.onopen = () => resolve();
      socket.onerror = () => reject(new Error("The App Server WebSocket failed to connect"));
      socket.onclose = (event) => {
        const error = new Error(
          `The App Server connection closed (${event.code ?? 1006}${
            event.reason ? `: ${event.reason}` : ""
          })`,
        );
        this.readyReject?.(error);
        this.readyReject = null;
        this.failPending(error);
      };
    });
    try {
      await opened;
      await ready;
      const params: Record<string, unknown> = {
        protocol_version: APP_SERVER_PROTOCOL_VERSION,
        client_name: this.options.clientName ?? "obsion-sdk-ts",
        client_version: this.options.clientVersion ?? "0.1.0",
      };
      if (this.options.token) params.bearer_token = this.options.token;
      return await this.request("server.initialize", params);
    } catch (error) {
      this.close();
      throw error;
    }
  }

  request<T = Record<string, unknown>>(
    method: string,
    params: Record<string, unknown> = {},
  ): Promise<T> {
    const socket = this.socket;
    if (socket === null || socket.readyState !== 1) {
      return Promise.reject(new Error("Connect the App Server client before sending requests"));
    }
    const id = this.nextId++;
    const response = new Promise<T>((resolve, reject) => {
      this.pending.set(id, {
        resolve: (value) => resolve(value as T),
        reject,
      });
    });
    try {
      socket.send(JSON.stringify({ jsonrpc: "2.0", id, method, params }));
    } catch (error) {
      this.pending.delete(id);
      return Promise.reject(error);
    }
    return response;
  }

  onNotification(listener: (notification: AppServerNotification) => void): () => void {
    this.notificationListeners.add(listener);
    return () => this.notificationListeners.delete(listener);
  }

  createThread(
    workspaceId: string,
    title: string,
    clientRequestId: string,
  ): Promise<Thread> {
    return this.request("thread.create", {
      client_request_id: clientRequestId,
      workspace_id: workspaceId,
      title,
    });
  }

  createTurn(
    threadId: string,
    input: string,
    clientRequestId: string,
    options: {
      contextRefs?: Array<Record<string, unknown>>;
      attachmentRefs?: Array<Record<string, unknown>>;
      modelProfile?: string;
    } = {},
  ): Promise<{ turn: Turn; run: Run }> {
    return this.request("turn.create", {
      client_request_id: clientRequestId,
      thread_id: threadId,
      input,
      context_refs: options.contextRefs ?? [],
      attachment_refs: options.attachmentRefs ?? [],
      ...(options.modelProfile ? { model_profile: options.modelProfile } : {}),
    });
  }

  subscribeRun(
    runId: string,
    afterSequence = 0,
  ): Promise<{
    subscription_id: string;
    run_id: string;
    after_sequence: number;
    run_status: RunStatus;
  }> {
    return this.request("run.subscribe", {
      run_id: runId,
      after_sequence: afterSequence,
    });
  }

  unsubscribeRun(subscriptionId: string): Promise<Record<string, unknown>> {
    return this.request("run.unsubscribe", { subscription_id: subscriptionId });
  }

  close(code = 1000, reason = "client closing"): void {
    const socket = this.socket;
    this.socket = null;
    this.readyResolve = null;
    this.readyReject = null;
    if (socket !== null) socket.close(code, reason);
    this.failPending(new Error("The App Server connection was closed"));
  }

  private handleMessage(raw: unknown): void {
    if (typeof raw !== "string") {
      this.close(1003, "text frames required");
      return;
    }
    let message: Record<string, unknown>;
    try {
      const parsed = JSON.parse(raw) as unknown;
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error("invalid message");
      }
      message = parsed as Record<string, unknown>;
    } catch {
      this.close(1002, "invalid JSON-RPC frame");
      return;
    }
    if ("id" in message) {
      const id = message.id;
      if (typeof id !== "number") return;
      const pending = this.pending.get(id);
      if (!pending) return;
      this.pending.delete(id);
      if (message.error) pending.reject(decodeAppServerError(message.error));
      else pending.resolve(message.result);
      return;
    }
    if (typeof message.method !== "string") return;
    const notification = message as unknown as AppServerNotification;
    if (notification.method === "server.ready") {
      this.readyResolve?.(notification);
      this.readyResolve = null;
      this.readyReject = null;
    }
    for (const listener of this.notificationListeners) listener(notification);
  }

  private failPending(error: Error): void {
    for (const pending of this.pending.values()) pending.reject(error);
    this.pending.clear();
  }
}

export function appServerUrlFromApiUrl(apiUrl: string): string {
  const url = new URL(apiUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = `${url.pathname.replace(/\/$/, "")}/app-server`;
  url.search = "";
  url.hash = "";
  return url.toString();
}

function decodeAppServerError(error: unknown): ObsionAppServerError {
  if (!error || typeof error !== "object") {
    return new ObsionAppServerError(-32603, "Malformed App Server error");
  }
  const body = error as Record<string, unknown>;
  const data =
    body.data && typeof body.data === "object"
      ? (body.data as Record<string, unknown>)
      : {};
  return new ObsionAppServerError(
    typeof body.code === "number" ? body.code : -32603,
    typeof body.message === "string" ? body.message : "App Server request failed",
    typeof data.code === "string" ? data.code : undefined,
    typeof data.status === "number" ? data.status : undefined,
    typeof data.correlation_id === "string" ? data.correlation_id : undefined,
    data.details && typeof data.details === "object"
      ? (data.details as Record<string, unknown>)
      : {},
  );
}

function defaultAppServerWebSocketFactory(
  url: string,
  protocols: string[],
): AppServerWebSocket {
  if (typeof WebSocket === "undefined") {
    throw new Error("A WebSocket implementation is required in this runtime");
  }
  return new WebSocket(url, protocols) as unknown as AppServerWebSocket;
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

export type EvaluationTarget = "ROUTING" | "SQL_POLICY" | "RUN_OUTPUT";

export interface EvaluationRun {
  id: string;
  dataset_id: string;
  agent_version_id: string;
  model_profile_id: string;
  application_revision: string;
  status: string;
  requested_by: string | null;
  baseline_run_id: string | null;
  dataset_snapshot_sha256: string;
  snapshot_sha256: string;
  configuration_snapshot: Record<string, unknown>;
  gate_passed: boolean | null;
  metrics: Record<string, unknown>;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface EvaluationCaseResult {
  id: string;
  evaluation_run_id: string;
  evaluation_case_id: string;
  ordinal: number;
  external_id: string;
  case_version: number;
  evaluator: EvaluationTarget;
  status: "PASSED" | "FAILED" | "ERROR";
  case_snapshot_sha256: string;
  checks: Record<string, boolean>;
  scores: Record<string, number>;
  observed: Record<string, unknown>;
  evidence_refs: Array<Record<string, unknown>>;
  error_code: string | null;
  error_message: string | null;
  duration_ms: number;
  created_at: string;
}

export type MemoryScope = "TURN" | "SESSION" | "WORKSPACE" | "USER_PREFERENCE";

export interface Memory {
  id: string;
  scope: MemoryScope;
  owner_ref: string;
  content: Record<string, unknown>;
  dedupe_key: string;
  sensitivity: string;
  status: "CANDIDATE" | "APPROVED" | "REJECTED" | "EXPIRED";
  policy_decision_id: string | null;
  expires_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface RunMemorySnapshot {
  id: string;
  run_id: string;
  memory_id: string;
  principal_id: string;
  ordinal: number;
  scope: MemoryScope;
  owner_ref: string;
  content: Record<string, unknown>;
  content_fingerprint: string;
  sensitivity: string;
  policy_decision_id: string;
  memory_updated_at: string;
  captured_at: string;
}

export interface RunConversationSnapshot {
  id: string;
  run_id: string;
  source_thread_id: string;
  source_turn_id: string;
  source_run_id: string | null;
  source_artifact_id: string | null;
  source_principal_id: string;
  ordinal: number;
  user_content: string;
  assistant_content: string | null;
  content_fingerprint: string;
  classification: string;
  captured_at: string;
}

export type RunFeedbackRating = "HELPFUL" | "NEEDS_IMPROVEMENT";

export interface RunFeedback {
  id: string;
  run_id: string;
  user_id: string;
  rating: RunFeedbackRating;
  reason: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface FeedbackSummary {
  total: number;
  helpful: number;
  needs_improvement: number;
  helpful_rate: number | null;
}

export type WorkspaceTaskStatus =
  | "OPEN"
  | "IN_PROGRESS"
  | "BLOCKED"
  | "COMPLETED"
  | "CANCELLED";
export type WorkspaceTaskPriority = "LOW" | "NORMAL" | "HIGH" | "CRITICAL";

export interface WorkspaceTask {
  id: string;
  workspace_id: string;
  title: string;
  description: string;
  status: WorkspaceTaskStatus;
  priority: WorkspaceTaskPriority;
  assignee_id: string | null;
  created_by: string;
  source_run_id: string | null;
  due_at: string | null;
  completed_at: string | null;
  version: number;
  created_at: string;
  updated_at: string;
}

export type WorkspaceDecisionStatus = "PROPOSED" | "ACCEPTED" | "REJECTED" | "SUPERSEDED";

export interface WorkspaceDecision {
  id: string;
  workspace_id: string;
  status: WorkspaceDecisionStatus;
  current_version: number;
  created_by: string;
  decided_by: string | null;
  source_run_id: string | null;
  supersedes_decision_id: string | null;
  decided_at: string | null;
  created_at: string;
  updated_at: string;
  title: string;
  summary: string;
  rationale: string;
  alternatives: string[];
  checksum_sha256: string;
}

export interface WorkspaceDecisionVersion {
  id: string;
  decision_id: string;
  version: number;
  title: string;
  summary: string;
  rationale: string;
  alternatives: string[];
  created_by: string;
  checksum_sha256: string;
  created_at: string;
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

  listThreads(workspaceId: string, includeArchived = false): Promise<Thread[]> {
    return this.request(
      `/api/v1/workspaces/${workspaceId}/threads?include_archived=${includeArchived}`,
    );
  }

  archiveThread(threadId: string): Promise<Thread> {
    return this.request(`/api/v1/threads/${threadId}/archive`, { method: "POST" });
  }

  resumeThread(threadId: string): Promise<Thread> {
    return this.request(`/api/v1/threads/${threadId}/resume`, { method: "POST" });
  }

  forkThread(
    threadId: string,
    input: { title?: string; from_turn_id?: string } = {},
  ): Promise<Thread> {
    return this.request(`/api/v1/threads/${threadId}/fork`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  listThreadEvents(threadId: string, afterSequence = 0, limit = 200): Promise<RunEvent[]> {
    return this.request(
      `/api/v1/threads/${threadId}/events?after_sequence=${afterSequence}&limit=${limit}`,
    );
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

  getRunFeedback(runId: string): Promise<RunFeedback | null> {
    return this.request(`/api/v1/runs/${runId}/feedback`);
  }

  recordRunFeedback(
    runId: string,
    input: {
      rating: RunFeedbackRating;
      reason?: string;
      expected_version?: number;
    },
  ): Promise<RunFeedback> {
    return this.request(`/api/v1/runs/${runId}/feedback`, {
      method: "PUT",
      body: JSON.stringify({ reason: "", ...input }),
    });
  }

  getFeedbackSummary(): Promise<FeedbackSummary> {
    return this.request("/api/v1/admin/feedback/summary");
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

  listRunMemories(runId: string): Promise<RunMemorySnapshot[]> {
    return this.request(`/api/v1/runs/${runId}/memories`);
  }

  listRunConversation(runId: string): Promise<RunConversationSnapshot[]> {
    return this.request(`/api/v1/runs/${runId}/conversation`);
  }

  createMemory(input: {
    scope: MemoryScope;
    owner_ref: string;
    content: Record<string, unknown>;
    sensitivity?: string;
    expires_at?: string;
  }): Promise<Memory> {
    return this.request("/api/v1/memories", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  listMemories(options: {
    scope?: MemoryScope;
    ownerRef?: string;
    status?: Memory["status"];
  } = {}): Promise<Memory[]> {
    const query = new URLSearchParams();
    if (options.scope) query.set("scope", options.scope);
    if (options.ownerRef) query.set("owner_ref", options.ownerRef);
    if (options.status) query.set("status", options.status);
    const suffix = query.size ? `?${query.toString()}` : "";
    return this.request(`/api/v1/memories${suffix}`);
  }

  decideMemory(memoryId: string, approve: boolean, reason: string): Promise<Memory> {
    return this.request(`/api/v1/memories/${memoryId}/${approve ? "approve" : "reject"}`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
  }

  createWorkspaceTask(
    workspaceId: string,
    input: {
      title: string;
      description?: string;
      priority?: WorkspaceTaskPriority;
      assignee_id?: string | null;
      source_run_id?: string | null;
      due_at?: string | null;
    },
  ): Promise<WorkspaceTask> {
    return this.request(`/api/v1/workspaces/${workspaceId}/tasks`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  listWorkspaceTasks(
    workspaceId: string,
    options: {
      status?: WorkspaceTaskStatus;
      assigneeId?: string;
      limit?: number;
    } = {},
  ): Promise<WorkspaceTask[]> {
    const query = new URLSearchParams();
    if (options.status) query.set("status", options.status);
    if (options.assigneeId) query.set("assignee_id", options.assigneeId);
    query.set("limit", String(options.limit ?? 200));
    return this.request(`/api/v1/workspaces/${workspaceId}/tasks?${query.toString()}`);
  }

  updateWorkspaceTask(
    taskId: string,
    input: {
      expected_version: number;
      title?: string;
      description?: string;
      status?: WorkspaceTaskStatus;
      priority?: WorkspaceTaskPriority;
      assignee_id?: string | null;
      due_at?: string | null;
    },
  ): Promise<WorkspaceTask> {
    return this.request(`/api/v1/workspace-tasks/${taskId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    });
  }

  listWorkspaceTaskEvents(
    taskId: string,
    afterSequence = 0,
    limit = 200,
  ): Promise<RunEvent[]> {
    return this.request(
      `/api/v1/workspace-tasks/${taskId}/events?after_sequence=${afterSequence}&limit=${limit}`,
    );
  }

  createWorkspaceDecision(
    workspaceId: string,
    input: {
      title: string;
      summary: string;
      rationale: string;
      alternatives?: string[];
      source_run_id?: string | null;
      supersedes_decision_id?: string | null;
    },
  ): Promise<WorkspaceDecision> {
    return this.request(`/api/v1/workspaces/${workspaceId}/decisions`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  listWorkspaceDecisions(
    workspaceId: string,
    options: { status?: WorkspaceDecisionStatus; limit?: number } = {},
  ): Promise<WorkspaceDecision[]> {
    const query = new URLSearchParams();
    if (options.status) query.set("status", options.status);
    query.set("limit", String(options.limit ?? 200));
    return this.request(`/api/v1/workspaces/${workspaceId}/decisions?${query.toString()}`);
  }

  reviseWorkspaceDecision(
    decisionId: string,
    input: {
      expected_version: number;
      title: string;
      summary: string;
      rationale: string;
      alternatives?: string[];
    },
  ): Promise<WorkspaceDecision> {
    return this.request(`/api/v1/workspace-decisions/${decisionId}`, {
      method: "PATCH",
      body: JSON.stringify(input),
    });
  }

  decideWorkspaceDecision(
    decisionId: string,
    approve: boolean,
    expectedVersion: number,
  ): Promise<WorkspaceDecision> {
    return this.request(
      `/api/v1/workspace-decisions/${decisionId}/${approve ? "accept" : "reject"}`,
      {
        method: "POST",
        body: JSON.stringify({ expected_version: expectedVersion }),
      },
    );
  }

  listWorkspaceDecisionVersions(decisionId: string): Promise<WorkspaceDecisionVersion[]> {
    return this.request(`/api/v1/workspace-decisions/${decisionId}/versions`);
  }

  listWorkspaceDecisionEvents(
    decisionId: string,
    afterSequence = 0,
    limit = 200,
  ): Promise<RunEvent[]> {
    return this.request(
      `/api/v1/workspace-decisions/${decisionId}/events?after_sequence=${afterSequence}&limit=${limit}`,
    );
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

  listMetrics(): Promise<Metric[]> {
    return this.request("/api/v1/data/metrics");
  }

  getMetricLineage(metricId: string): Promise<MetricLineage> {
    return this.request(`/api/v1/data/lineage/${metricId}`);
  }

  validateSql(sql: string, dataSourceId: string): Promise<Record<string, unknown>> {
    return this.request("/api/v1/data/sql/validate", {
      method: "POST",
      body: JSON.stringify({ sql, data_source_id: dataSourceId }),
    });
  }

  explainSql(sql: string, dataSourceId: string): Promise<Record<string, unknown>> {
    return this.request("/api/v1/data/sql/explain", {
      method: "POST",
      body: JSON.stringify({ sql, data_source_id: dataSourceId }),
    });
  }

  getDataCatalog(): Promise<Record<string, number>> {
    return this.request("/api/v1/admin/data/catalog");
  }

  createMetric(definition: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request("/api/v1/admin/data/metrics", {
      method: "POST",
      body: JSON.stringify(definition),
    });
  }

  createDimension(definition: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request("/api/v1/admin/data/dimensions", {
      method: "POST",
      body: JSON.stringify(definition),
    });
  }

  createEntity(definition: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request("/api/v1/admin/data/entities", {
      method: "POST",
      body: JSON.stringify(definition),
    });
  }

  createRelation(definition: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request("/api/v1/admin/data/relations", {
      method: "POST",
      body: JSON.stringify(definition),
    });
  }

  createBusinessRule(definition: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request("/api/v1/admin/data/rules", {
      method: "POST",
      body: JSON.stringify(definition),
    });
  }

  createTimeDefinition(definition: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request("/api/v1/admin/data/time-definitions", {
      method: "POST",
      body: JSON.stringify(definition),
    });
  }

  createSemanticSynonym(definition: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.request("/api/v1/admin/data/synonyms", {
      method: "POST",
      body: JSON.stringify(definition),
    });
  }

  searchKnowledge(query: string, limit = 8): Promise<KnowledgeHit[]> {
    return this.request("/api/v1/knowledge/search", {
      method: "POST",
      body: JSON.stringify({ query, limit }),
    });
  }

  createEvaluationDataset(input: {
    name: string;
    domain: string;
    description?: string;
  }): Promise<Record<string, unknown>> {
    return this.request("/api/v1/admin/evaluations/datasets", {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  addEvaluationCase(
    datasetId: string,
    input: {
      external_id: string;
      version?: number;
      evaluator: EvaluationTarget;
      input_payload: Record<string, unknown>;
      expected: Record<string, unknown>;
      fixtures?: Record<string, unknown>;
    },
  ): Promise<Record<string, unknown>> {
    return this.request(`/api/v1/admin/evaluations/datasets/${datasetId}/cases`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  runEvaluation(
    datasetId: string,
    input: {
      agent_version_id: string;
      model_profile_id: string;
      application_revision: string;
      baseline_run_id?: string;
      minimum_pass_rate?: number;
      maximum_regression_rate?: number;
      score_thresholds?: Record<string, number>;
      run_bindings?: Record<string, string>;
    },
  ): Promise<EvaluationRun> {
    return this.request(`/api/v1/admin/evaluations/datasets/${datasetId}/runs`, {
      method: "POST",
      body: JSON.stringify(input),
    });
  }

  listEvaluationRuns(datasetId?: string): Promise<EvaluationRun[]> {
    const query = datasetId ? `?dataset_id=${encodeURIComponent(datasetId)}` : "";
    return this.request(`/api/v1/admin/evaluations/runs${query}`);
  }

  getEvaluationRun(runId: string): Promise<EvaluationRun> {
    return this.request(`/api/v1/admin/evaluations/runs/${runId}`);
  }

  listEvaluationResults(runId: string): Promise<EvaluationCaseResult[]> {
    return this.request(`/api/v1/admin/evaluations/runs/${runId}/results`);
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
