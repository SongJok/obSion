export type ViewName =
  | "assistant"
  | "collaboration"
  | "automation"
  | "actions"
  | "artifacts"
  | "files"
  | "reports"
  | "dashboards"
  | "sql"
  | "evidence"
  | "timeline"
  | "knowledge"
  | "code"
  | "data"
  | "studio"
  | "eval"
  | "admin";

export interface StudioVersion {
  kind: "Agent" | "Skill";
  name: string;
  display_name: string;
  description: string;
  definition_id: string;
  version_id: string;
  version: number;
  status: string;
  checksum_sha256: string;
  promoted: boolean;
  promoted_at: string | null;
  spec: Record<string, unknown>;
}

export interface StudioCompare {
  kind: string;
  name: string;
  baseline: { version: number; checksum_sha256: string; promoted: boolean };
  candidate: { version: number; checksum_sha256: string; promoted: boolean };
  identical: boolean;
  changes: Array<{ path: string; baseline: unknown; candidate: unknown }>;
  traffic_split: boolean;
  evaluation: string;
}

export interface StudioCatalog {
  agents: StudioVersion[];
  skills: StudioVersion[];
}

export interface StudioValidateResult {
  kind: string;
  name: string;
  checksum_sha256: string;
  preview: Record<string, unknown>;
}

export interface EvalDataset {
  id: string;
  name: string;
  description: string;
  domain: string;
  created_at: string;
  updated_at: string;
}

export interface EvalAgentPin {
  name: string;
  version: number;
  version_id: string;
  checksum_sha256: string;
}

export interface EvalProfilePin {
  id: string;
  name: string;
}

export interface EvalRun {
  id: string;
  dataset_id: string;
  application_revision: string;
  status: string;
  gate_passed: boolean | null;
  metrics: Record<string, unknown>;
  snapshot_sha256: string;
}

export interface EvalCase {
  id: string;
  dataset_id: string;
  external_id: string;
  version: number;
  evaluator: string;
}

export interface EvalResult {
  id: string;
  external_id: string;
  evaluator: string;
  status: string;
}

export interface EvalCatalog {
  datasets: EvalDataset[];
  runs: EvalRun[];
  agents: EvalAgentPin[];
  prompts: EvalAgentPin[];
  model_profiles: EvalProfilePin[];
}

export interface EvalCompare {
  baseline: EvalRun;
  candidate: EvalRun;
  gate_passed: boolean;
  metrics: Record<string, unknown>;
  agent_changed: boolean;
  prompt_changed: boolean;
}

export interface SessionPrincipal {
  principal_id: string;
  organization_id: string;
  display_name: string;
  department: string | null;
  roles: string[];
}

export interface ImBinding {
  id: string;
  channel: string;
  sender_id: string;
  user_id: string;
  active: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
  revoked_at: string | null;
}

export interface Workspace {
  id: string;
  name: string;
  description: string;
  classification: string;
  visibility: string;
  updated_at: string;
}

export interface WorkspaceMember {
  workspace_id: string;
  user_id: string;
  display_name: string;
  email: string;
  permissions: string[];
  created_by: string;
  created_at: string;
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

export interface ThreadEvent {
  id: string;
  sequence: number;
  name: string;
  correlation_id: string;
  payload: Record<string, unknown>;
  created_at: string;
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
  prompt_pins?: Array<{
    name: string;
    version: number;
    version_id: string;
    checksum_sha256: string;
  }>;
  context_budget?: {
    budget?: number;
    used?: number;
    method?: string;
    decisions?: Array<{
      source: string;
      trust: string;
      action: "KEEP" | "COMPRESS" | "SUMMARIZE" | "DROP";
      original_chars: number;
      kept_chars: number;
      reason: string;
    }>;
  };
  conversation_compact?: {
    method?: string;
    keep_recent?: number;
    kept_turns?: number;
    summarized_turns?: number;
    source_turn_ids?: string[];
    summary?: Record<string, unknown> | null;
  };
  workspace_context?: {
    workspace_id?: string;
    name?: string;
    classification?: string;
    visibility?: string;
    description?: string;
    description_fingerprint?: string;
  };
  intent: Record<string, unknown>;
  plan: {
    route?: string;
    required_evidence?: string[];
    steps?: PlanStep[];
    sandbox?: { network?: string; enabled?: boolean; mounts?: string[] };
  };
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
  path?: string | null;
  file_version?: number | null;
  superseded_at?: string | null;
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
  mark?: string | { type?: string; point?: boolean; tooltip?: boolean };
  validation?: { valid?: boolean };
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
  run_id: string;
  step_id: string | null;
  evidence_type: string;
  source: string;
  resource: string;
  observed_at: string;
  ingested_at: string;
  content: Record<string, unknown>;
  content_fingerprint: string;
  confidence: string;
  classification: string;
  permissions: string[];
  lineage: Record<string, unknown>;
}

export interface MemorySnapshot {
  id: string;
  run_id: string;
  memory_id: string;
  principal_id: string;
  ordinal: number;
  scope: "TURN" | "SESSION" | "WORKSPACE" | "USER_PREFERENCE";
  owner_ref: string;
  content: Record<string, unknown>;
  content_fingerprint: string;
  sensitivity: string;
  policy_decision_id: string;
  memory_updated_at: string;
  captured_at: string;
}

export interface ConversationSnapshot {
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

export interface RuntimeSlo {
  source: "postgresql";
  runs: {
    terminal: number;
    completed: number;
    failed: number;
    cancelled: number;
    success_rate: number | null;
  };
  latency: {
    average_ms: number | null;
    count: number;
    ttft: { available: boolean; metric: string; reason: string };
    model: { average_ms: number | null; count: number };
    tool: { average_ms: number | null; count: number; source: "capability-steps" };
  };
  steps: { average: number | null; count: number };
  tokens: { input: number; output: number };
  cost: { amount: string };
  replans: { events: number; rate: number | null };
  approvals: {
    requested: number;
    approved: number;
    rejected: number;
    pending: number;
    approval_rate: number | null;
  };
  satisfaction: FeedbackSummary;
  evidence_coverage: { average: number | null; count: number };
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

export interface CodeRepository {
  id: string;
  name: string;
  default_branch: string;
  classification: string;
  current_snapshot_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface CodeSymbolHit {
  repository_id: string;
  repository: string;
  commit_id: string;
  snapshot_id: string;
  symbol_id: string;
  path: string;
  language: string;
  kind: string;
  name: string;
  qualified_name: string;
  start_line: number;
  end_line: number;
  relations: Array<Record<string, unknown>>;
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
  updated_at: string;
}

export interface MetricLineage {
  metric: { id: string; name: string; version: number };
  table: { id: string; name: string; owner: string };
  data_source: { id: string; name: string; environment: string; read_only: boolean };
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

export type WorkflowStepType = "ANALYSIS" | "HUMAN_REVIEW" | "NOTIFICATION";

export interface WorkflowStepSpec {
  id: string;
  name: string;
  type: WorkflowStepType;
  depends_on: string[];
  prompt?: string;
  model_profile?: string;
  title?: string;
  body?: string;
  review_instructions?: string;
  disallow_self_review?: boolean;
}

export interface WorkflowSpec {
  steps: WorkflowStepSpec[];
}

export interface WorkflowVersion {
  id: string;
  workflow_id: string;
  version: number;
  spec: WorkflowSpec;
  checksum_sha256: string;
  created_by: string;
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
  trigger: "MANUAL" | "SCHEDULE" | "CAPABILITY";
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
