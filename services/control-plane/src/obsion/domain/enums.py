from enum import StrEnum


class Classification(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class ThreadStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class RunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    WAITING_USER = "WAITING_USER"
    REPLANNING = "REPLANNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class StepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class StepKind(StrEnum):
    UNDERSTAND = "UNDERSTAND"
    PLAN = "PLAN"
    MODEL = "MODEL"
    CAPABILITY = "CAPABILITY"
    VERIFY = "VERIFY"
    RESPOND = "RESPOND"


class RiskLevel(StrEnum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"

    @property
    def ordinal(self) -> int:
        return int(self.value[1:])


class DecisionEffect(StrEnum):
    ALLOW = "ALLOW"
    MASK = "MASK"
    ASK = "ASK"
    DENY = "DENY"


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class SideEffect(StrEnum):
    NONE = "NONE"
    IDEMPOTENT_WRITE = "IDEMPOTENT_WRITE"
    WRITE = "WRITE"
    DESTRUCTIVE = "DESTRUCTIVE"


class CapabilityTransport(StrEnum):
    INTERNAL = "INTERNAL"
    HTTP = "HTTP"
    GRPC = "GRPC"
    MCP = "MCP"
    SDK = "SDK"
    SQL_PROXY = "SQL_PROXY"
    AGENT = "AGENT"
    WORKFLOW = "WORKFLOW"


class ArtifactKind(StrEnum):
    TEXT = "TEXT"
    TABLE = "TABLE"
    CHART = "CHART"
    SQL = "SQL"
    CODE = "CODE"
    DIFF = "DIFF"
    REPORT = "REPORT"
    DASHBOARD = "DASHBOARD"
    FILE = "FILE"
    DIAGRAM = "DIAGRAM"


class EvidenceType(StrEnum):
    DOCUMENT = "DOCUMENT"
    DATA = "DATA"
    METRIC = "METRIC"
    LOG = "LOG"
    TRACE = "TRACE"
    DEPLOYMENT = "DEPLOYMENT"
    CONFIG = "CONFIG"
    CODE = "CODE"
    TOOL = "TOOL"


class VerificationStatus(StrEnum):
    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"


class EvaluationTarget(StrEnum):
    ROUTING = "ROUTING"
    SQL_POLICY = "SQL_POLICY"
    RUN_OUTPUT = "RUN_OUTPUT"


class EvaluationResultStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    ERROR = "ERROR"


class Visibility(StrEnum):
    PRIVATE = "PRIVATE"
    WORKSPACE = "WORKSPACE"
    ORGANIZATION = "ORGANIZATION"


class MemoryScope(StrEnum):
    TURN = "TURN"
    SESSION = "SESSION"
    WORKSPACE = "WORKSPACE"
    USER_PREFERENCE = "USER_PREFERENCE"


class MemoryStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ConnectorStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    ERROR = "ERROR"


class RegistryStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"


class ActorType(StrEnum):
    USER = "USER"
    SERVICE = "SERVICE"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class WorkflowStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    RETIRED = "RETIRED"


class WorkflowConcurrencyPolicy(StrEnum):
    FORBID = "FORBID"
    ALLOW = "ALLOW"
    REPLACE = "REPLACE"


class WorkflowStepType(StrEnum):
    ANALYSIS = "ANALYSIS"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    NOTIFICATION = "NOTIFICATION"


class AutomationTrigger(StrEnum):
    MANUAL = "MANUAL"
    SCHEDULE = "SCHEDULE"


class AutomationStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_REVIEW = "WAITING_REVIEW"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class AutomationStepStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_REVIEW = "WAITING_REVIEW"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    SKIPPED = "SKIPPED"


class ScheduleMisfirePolicy(StrEnum):
    SKIP = "SKIP"
    FIRE_ONCE = "FIRE_ONCE"


class ReviewDecision(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class NotificationStatus(StrEnum):
    DELIVERED = "DELIVERED"
    READ = "READ"


class ActionType(StrEnum):
    GENERATE_PR = "GENERATE_PR"
    CREATE_TICKET = "CREATE_TICKET"
    MODIFY_CONFIG = "MODIFY_CONFIG"
    RESTART_SERVICE = "RESTART_SERVICE"
    DEPLOY = "DEPLOY"


class ActionStatus(StrEnum):
    DRAFT = "DRAFT"
    PREFLIGHT_FAILED = "PREFLIGHT_FAILED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    WAITING_ROLLBACK_APPROVAL = "WAITING_ROLLBACK_APPROVAL"
    ROLLBACK_APPROVED = "ROLLBACK_APPROVED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"
    ROLLBACK_REJECTED = "ROLLBACK_REJECTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class ActionApprovalPurpose(StrEnum):
    EXECUTE = "EXECUTE"
    ROLLBACK = "ROLLBACK"


class ActionAttemptStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
