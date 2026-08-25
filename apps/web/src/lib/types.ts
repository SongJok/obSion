export type ViewName =
  | "assistant"
  | "automation"
  | "actions"
  | "artifacts"
  | "knowledge"
  | "data"
  | "admin";

export interface Workspace {
  id: string;
  name: string;
  description: string;
  classification: string;
  visibility: string;
  updated_at: string;
}

export interface Thread {
  id: string;
  workspace_id: string;
  title: string;
  status: "ACTIVE" | "ARCHIVED";
  updated_at: string;
}

export interface Turn {
  id: string;
  thread_id: string;
  ordinal: number;
  input_text: string;
  created_at: string;
}

export type RunStatus =
  | "PENDING"
  | "RUNNING"
  | "WAITING_APPROVAL"
  | "WAITING_USER"
  | "REPLANNING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export interface Run {
  id: string;
  turn_id: string;
  status: RunStatus;
  agent_version_id: string | null;
  model_profile_id: string | null;
  intent: Record<string, unknown>;
  plan: { route?: string; required_evidence?: string[]; steps?: PlanStep[] };
  step_count: number;
  max_input_tokens: number;
  max_output_tokens: number;
  max_cost_amount: string;
  input_tokens: number;
  output_tokens: number;
  cost_amount: string;
  error_code: string | null;
  error_message: string | null;
  replay_of_run_id: string | null;
  created_at: string;
}

export interface PlanStep {
  ordinal: number;
  name: string;
  capability: string;
}

export interface RunStep {
  id: string;
  ordinal: number;
  name: string;
  kind: string;
  status: string;
  capability_version_id: string | null;
  error_code: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface RunEvent {
  id: string;
  sequence: number;
  name: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Artifact {
  id: string;
  workspace_id: string;
  run_id: string | null;
  kind: string;
  title: string;
  media_type: string;
  inline_content: ArtifactContent | null;
  storage_key: string | null;
  classification: string;
  lineage: Record<string, unknown>;
  created_at: string;
}

export interface ArtifactContent {
  markdown?: string;
  verification?: Verification;
  sql?: string;
  columns?: string[];
  rows?: Record<string, unknown>[];
  row_count?: number;
  data?: { values?: Record<string, unknown>[] };
  encoding?: Record<string, { field?: string; type?: string }>;
  [key: string]: unknown;
}

export interface Verification {
  verified: boolean;
  confidence: number;
  coverage: number;
  missing_evidence: string[];
  checks: Record<string, boolean>;
}

export interface Evidence {
  id: string;
  evidence_type: string;
  source: string;
  resource: string;
  observed_at: string;
  content: Record<string, unknown>;
  content_fingerprint: string;
  confidence: string;
  classification: string;
  permissions: string[];
  lineage: Record<string, unknown>;
}

export interface Claim {
  id: string;
  statement: string;
  confidence: string;
  verification_status: string;
  evidence_ids: string[];
}

export interface MessageBundle {
  turn: Turn;
  run?: Run;
  artifact?: Artifact;
  artifacts?: Artifact[];
}

export interface Metric {
  id: string;
  name: string;
  display_name: string;
  version: number;
  owner: string;
  synonyms: string[];
  validated: boolean;
  updated_at: string;
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
  checksum_sha256: string;
  published_at: string | null;
  created_at: string;
}

export interface WorkflowSchedule {
  id: string;
  workflow_id: string;
  workflow_version_id: string;
  name: string;
  cron_expression: string;
  timezone: string;
  misfire_policy: "SKIP" | "FIRE_ONCE";
  enabled: boolean;
  next_fire_at: string;
  last_fire_at: string | null;
  last_error_code: string | null;
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
  review_reason: string | null;
  reviewed_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface AutomationExecution {
  id: string;
  workflow_id: string;
  workflow_version_id: string;
  schedule_id: string | null;
  trigger: "MANUAL" | "SCHEDULE";
  scheduled_for: string | null;
  status: AutomationStatus;
  owner_id: string;
  input_payload: Record<string, unknown>;
  deadline_at: string;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
  summary: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  steps?: AutomationStep[];
}

export interface NotificationDelivery {
  id: string;
  execution_id: string | null;
  action_request_id: string | null;
  title: string;
  body: string;
  payload: Record<string, unknown>;
  status: "DELIVERED" | "READ";
  delivered_at: string;
  read_at: string | null;
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
  started_at: string | null;
  completed_at: string | null;
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
  created_at: string;
}

export interface ActionAttempt {
  id: string;
  purpose: "EXECUTE" | "ROLLBACK";
  ordinal: number;
  status: "PENDING" | "RUNNING" | "COMPLETED" | "FAILED";
  capability_version_id: string;
  connector_id: string;
  policy_decision_id: string | null;
  idempotency_key: string;
  output: Record<string, unknown>;
  started_at: string | null;
  completed_at: string | null;
  error_code: string | null;
  error_message: string | null;
}

export interface ActionPlan {
  id: string;
  spec: Record<string, unknown>;
  checksum_sha256: string;
  created_at: string;
}

export interface ActionDetail {
  action: ActionRequest;
  plan: ActionPlan | null;
  approvals: ActionApproval[];
  attempts: ActionAttempt[];
}
