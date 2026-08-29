from __future__ import annotations

from static_contract_analysis import EnumFingerprint, EventContractPair

REVIEWED_EVENT_SINKS: dict[str, frozenset[EventContractPair]] = {
    "actions/gateway.py::ActionGateway._invoke#EventDraft[1]": frozenset(
        {("action.policy_decided", 1)}
    ),
    "actions/gateway.py::ActionGateway._invoke#EventDraft[2]": frozenset(
        {("action.provider_started", 1)}
    ),
    "actions/gateway.py::ActionGateway._invoke#EventDraft[3]": frozenset(
        {("action.provider_completed", 1)}
    ),
    "actions/gateway.py::ActionGateway._invoke#EventDraft[4]": frozenset(
        {("action.provider_failed", 1)}
    ),
    "actions/service.py::ActionService._event#EventDraft[1]": frozenset(
        {
            ("action.approval_decided", 1),
            ("action.approval_expired", 1),
            ("action.approval_requested", 1),
            ("action.cancelled", 1),
            ("action.created", 1),
            ("action.preflight_failed", 1),
            ("action.preflight_passed", 1),
            ("action.rollback_requested", 1),
            ("notification.delivered", 1),
        }
    ),
    "actions/worker.py::ActionWorker._event#EventDraft[1]": frozenset(
        {
            ("action.approval_expired", 1),
            ("action.claimed", 1),
            ("action.completed", 1),
            ("action.failed", 1),
            ("action.rollback_failed", 1),
            ("action.rolled_back", 1),
        }
    ),
    "application/approvals.py::ApprovalService.decide#EventDraft[1]": frozenset(
        {("approval.expired", 1)}
    ),
    "application/approvals.py::ApprovalService.decide#EventDraft[2]": frozenset(
        {("approval.approved", 1), ("approval.rejected", 1)}
    ),
    "application/memory.py::MemoryService._record#EventDraft[1]": frozenset(
        {
            ("memory.approved", 1),
            ("memory.candidate", 1),
            ("memory.expired", 1),
            ("memory.rejected", 1),
        }
    ),
    "application/workspaces.py::WorkspaceService.add_member#EventDraft[1]": frozenset(
        {("workspace.member_changed", 1)}
    ),
    "application/workspaces.py::WorkspaceService.archive_thread#EventDraft[1]": frozenset(
        {("thread.archived", 1)}
    ),
    "application/workspaces.py::WorkspaceService.cancel_run#EventDraft[1]": frozenset(
        {("run.cancellation_requested", 1)}
    ),
    "application/workspaces.py::WorkspaceService.cancel_run#EventDraft[2]": frozenset(
        {("run.cancelled", 1)}
    ),
    "application/workspaces.py::WorkspaceService.create_thread#EventDraft[1]": frozenset(
        {("thread.created", 1)}
    ),
    "application/workspaces.py::WorkspaceService.create_turn#EventDraft[1]": frozenset(
        {("turn.created", 1)}
    ),
    "application/workspaces.py::WorkspaceService.create_turn#EventDraft[2]": frozenset(
        {("run.created", 1)}
    ),
    "application/workspaces.py::WorkspaceService.create_workspace#EventDraft[1]": frozenset(
        {("workspace.created", 1)}
    ),
    "application/workspaces.py::WorkspaceService.fork_thread#EventDraft[1]": frozenset(
        {("thread.archived", 1)}
    ),
    "application/workspaces.py::WorkspaceService.fork_thread#EventDraft[2]": frozenset(
        {("thread.forked", 1)}
    ),
    "application/workspaces.py::WorkspaceService.remove_member#EventDraft[1]": frozenset(
        {("workspace.member_removed", 1)}
    ),
    "application/workspaces.py::WorkspaceService.replay_run#EventDraft[1]": frozenset(
        {("run.replay_requested", 1)}
    ),
    "application/workspaces.py::WorkspaceService.resume_thread#EventDraft[1]": frozenset(
        {("thread.resumed", 1)}
    ),
    "artifacts/service.py::ArtifactService.create_file#EventDraft[1]": frozenset(
        {("artifact.created", 1)}
    ),
    "automation/service.py::AutomationService._cancel_execution_rows#EventDraft[1]": frozenset(
        {("automation.cancelled", 1)}
    ),
    "automation/service.py::AutomationService._event#EventDraft[1]": frozenset(
        {
            ("automation.cancellation_requested", 1),
            ("automation.execution_created", 1),
            ("automation.execution_skipped", 1),
            ("automation.review_decided", 1),
            ("notification.delivered", 1),
            ("schedule.created", 1),
            ("schedule.disabled", 1),
            ("schedule.enabled", 1),
            ("workflow.active", 1),
            ("workflow.created", 1),
            ("workflow.paused", 1),
            ("workflow.retired", 1),
            ("workflow.version_created", 1),
            ("workflow.version_published", 1),
        }
    ),
    "automation/worker.py::AutomationWorker._execution_event#EventDraft[1]": frozenset(
        {
            ("automation.completed", 1),
            ("automation.failed", 1),
            ("automation.review_requested", 1),
            ("automation.started", 1),
            ("automation.step_completed", 1),
            ("automation.step_started", 1),
        }
    ),
    "automation/worker.py::AutomationWorker.tick_schedules#EventDraft[1]": frozenset(
        {("schedule.misfire_skipped", 1)}
    ),
    "automation/worker.py::AutomationWorker.tick_schedules#EventDraft[2]": frozenset(
        {("schedule.disabled", 1)}
    ),
    "capabilities/gateway.py::CapabilityGateway._create_approval#EventDraft[1]": frozenset(
        {("approval.requested", 1)}
    ),
    "capabilities/gateway.py::CapabilityGateway._evidence#EventDraft[1]": frozenset(
        {("evidence.created", 1)}
    ),
    "capabilities/gateway.py::CapabilityGateway._gateway_event#EventDraft[1]": frozenset(
        {
            ("capability.input_rejected", 1),
            ("capability.rate_limit_unavailable", 1),
            ("capability.rate_limited", 1),
        }
    ),
    "capabilities/gateway.py::CapabilityGateway._invoke#EventDraft[1]": frozenset(
        {("capability.requested", 1)}
    ),
    "capabilities/gateway.py::CapabilityGateway._invoke#EventDraft[2]": frozenset(
        {("tool.started", 1)}
    ),
    "capabilities/gateway.py::CapabilityGateway._invoke#EventDraft[3]": frozenset(
        {("tool.completed", 1)}
    ),
    "capabilities/gateway.py::CapabilityGateway._invoke#EventDraft[4]": frozenset(
        {("tool.failed", 1)}
    ),
    "capabilities/gateway.py::CapabilityGateway._policy_event#EventDraft[1]": frozenset(
        {("policy.decided", 1)}
    ),
    "collaboration/service.py::WorkspaceCollaborationService._record#EventDraft[1]": frozenset(
        {
            ("workspace_decision.accepted", 1),
            ("workspace_decision.proposed", 1),
            ("workspace_decision.rejected", 1),
            ("workspace_decision.revised", 1),
            ("workspace_decision.superseded", 1),
            ("workspace_task.created", 1),
            ("workspace_task.updated", 1),
        }
    ),
    "feedback/service.py::RunFeedbackService.record_feedback#EventDraft[1]": frozenset(
        {("run.feedback.recorded", 1), ("run.feedback.revised", 1)}
    ),
    "harness/replay.py::RunReplayService.materialize#EventDraft[1]": frozenset(
        {("run.replay.started", 1)}
    ),
    "harness/replay.py::RunReplayService.materialize#EventDraft[2]": frozenset(
        {("run.replay.event", 1)}
    ),
    "harness/replay.py::RunReplayService.materialize#EventDraft[3]": frozenset(
        {("run.replay.completed", 1)}
    ),
    "harness/runtime.py::HarnessRuntime._event#EventDraft[1]": frozenset(
        {
            ("answer.delta", 1),
            ("artifact.created", 1),
            ("context.resolved", 1),
            ("critic.completed", 1),
            ("evidence.created", 1),
            ("intent.detected", 1),
            ("plan.created", 1),
            ("plan.updated", 1),
            ("run.cancelled", 1),
            ("run.completed", 1),
            ("run.failed", 1),
            ("run.state_changed", 1),
        }
    ),
    "harness/worker.py::RunWorker._claim#EventDraft[1]": frozenset({("run.failed", 1)}),
    "harness/worker.py::RunWorker._claim#EventDraft[2]": frozenset(
        {("run.resumed", 1), ("run.started", 1)}
    ),
}

REVIEWED_EVENT_HELPER_CALLS: dict[str, frozenset[EventContractPair]] = {
    "actions/service.py::ActionService._create_approval#_event[1]": frozenset(
        {("action.approval_requested", 1)}
    ),
    "actions/service.py::ActionService.cancel#_event[1]": frozenset({("action.cancelled", 1)}),
    "actions/service.py::ActionService.create#_event[1]": frozenset({("action.created", 1)}),
    "actions/service.py::ActionService.decide#_event[1]": frozenset(
        {("action.approval_expired", 1)}
    ),
    "actions/service.py::ActionService.decide#_event[2]": frozenset(
        {("action.approval_decided", 1)}
    ),
    "actions/service.py::ActionService.deliver_notification#_event[1]": frozenset(
        {("notification.delivered", 1)}
    ),
    "actions/service.py::ActionService.preflight#_event[1]": frozenset(
        {("action.preflight_failed", 1)}
    ),
    "actions/service.py::ActionService.preflight#_event[2]": frozenset(
        {("action.preflight_passed", 1)}
    ),
    "actions/service.py::ActionService.request_rollback#_event[1]": frozenset(
        {("action.rollback_requested", 1)}
    ),
    "actions/worker.py::ActionWorker._claim#_event[1]": frozenset({("action.claimed", 1)}),
    "actions/worker.py::ActionWorker._complete#_event[1]": frozenset(
        {("action.completed", 1), ("action.rolled_back", 1)}
    ),
    "actions/worker.py::ActionWorker._expire_one_approval#_event[1]": frozenset(
        {("action.approval_expired", 1)}
    ),
    "actions/worker.py::ActionWorker._fail#_event[1]": frozenset(
        {("action.failed", 1), ("action.rollback_failed", 1)}
    ),
    "application/memory.py::MemoryService.create_candidate#_record[1]": frozenset(
        {("memory.candidate", 1)}
    ),
    "application/memory.py::MemoryService.decide#_record[1]": frozenset({("memory.expired", 1)}),
    "application/memory.py::MemoryService.decide#_record[2]": frozenset(
        {("memory.approved", 1), ("memory.rejected", 1)}
    ),
    "application/memory.py::MemoryService.list_memories#_record[1]": frozenset(
        {("memory.expired", 1)}
    ),
    "automation/service.py::AutomationService.cancel_execution#_event[1]": frozenset(
        {("automation.cancellation_requested", 1)}
    ),
    "automation/service.py::AutomationService.create_schedule#_event[1]": frozenset(
        {("schedule.created", 1)}
    ),
    "automation/service.py::AutomationService.create_version#_event[1]": frozenset(
        {("workflow.version_created", 1)}
    ),
    "automation/service.py::AutomationService.create_workflow#_event[1]": frozenset(
        {("workflow.created", 1)}
    ),
    "automation/service.py::AutomationService.deliver_notification#_event[1]": frozenset(
        {("notification.delivered", 1)}
    ),
    "automation/service.py::AutomationService.publish_version#_event[1]": frozenset(
        {("workflow.version_published", 1)}
    ),
    "automation/service.py::AutomationService.review_step#_event[1]": frozenset(
        {("automation.review_decided", 1)}
    ),
    "automation/service.py::AutomationService.set_schedule_enabled#_event[1]": frozenset(
        {("schedule.disabled", 1), ("schedule.enabled", 1)}
    ),
    "automation/service.py::AutomationService.set_workflow_status#_event[1]": frozenset(
        {("workflow.active", 1), ("workflow.paused", 1), ("workflow.retired", 1)}
    ),
    "automation/service.py::AutomationService.trigger_workflow#_event[1]": frozenset(
        {("automation.execution_created", 1), ("automation.execution_skipped", 1)}
    ),
    "automation/worker.py::AutomationWorker._advance#_execution_event[1]": frozenset(
        {("automation.review_requested", 1)}
    ),
    "automation/worker.py::AutomationWorker._advance#_execution_event[2]": frozenset(
        {("automation.completed", 1)}
    ),
    "automation/worker.py::AutomationWorker._claim_execution#_execution_event[1]": frozenset(
        {("automation.started", 1)}
    ),
    "automation/worker.py::AutomationWorker._fail_execution#_execution_event[1]": frozenset(
        {("automation.failed", 1)}
    ),
    "automation/worker.py::AutomationWorker._reconcile_analysis_steps#_execution_event[1]": (
        frozenset({("automation.step_completed", 1)})
    ),
    "automation/worker.py::AutomationWorker._start_analysis#_execution_event[1]": frozenset(
        {("automation.step_started", 1)}
    ),
    "capabilities/gateway.py::CapabilityGateway._invoke#_gateway_event[1]": frozenset(
        {("capability.input_rejected", 1)}
    ),
    "capabilities/gateway.py::CapabilityGateway._invoke#_gateway_event[2]": frozenset(
        {("capability.rate_limit_unavailable", 1)}
    ),
    "capabilities/gateway.py::CapabilityGateway._invoke#_gateway_event[3]": frozenset(
        {("capability.rate_limited", 1)}
    ),
    "collaboration/service.py::WorkspaceCollaborationService.create_decision#_record[1]": (
        frozenset({("workspace_decision.proposed", 1)})
    ),
    "collaboration/service.py::WorkspaceCollaborationService.create_task#_record[1]": frozenset(
        {("workspace_task.created", 1)}
    ),
    "collaboration/service.py::WorkspaceCollaborationService.decide#_record[1]": frozenset(
        {("workspace_decision.superseded", 1)}
    ),
    "collaboration/service.py::WorkspaceCollaborationService.decide#_record[2]": frozenset(
        {("workspace_decision.accepted", 1), ("workspace_decision.rejected", 1)}
    ),
    "collaboration/service.py::WorkspaceCollaborationService.revise_decision#_record[1]": (
        frozenset({("workspace_decision.revised", 1)})
    ),
    "collaboration/service.py::WorkspaceCollaborationService.update_task#_record[1]": frozenset(
        {("workspace_task.updated", 1)}
    ),
    "harness/runtime.py::HarnessRuntime._cancel#_event[1]": frozenset({("run.cancelled", 1)}),
    "harness/runtime.py::HarnessRuntime._fail#_event[1]": frozenset({("run.failed", 1)}),
    "harness/runtime.py::HarnessRuntime._ingest_attachments#_event[1]": frozenset(
        {("evidence.created", 1)}
    ),
    "harness/runtime.py::HarnessRuntime._prepare#_event[1]": frozenset({("context.resolved", 1)}),
    "harness/runtime.py::HarnessRuntime._prepare#_event[2]": frozenset({("intent.detected", 1)}),
    "harness/runtime.py::HarnessRuntime._prepare#_event[3]": frozenset({("plan.created", 1)}),
    "harness/runtime.py::HarnessRuntime._replan_transient_failures#_event[1]": frozenset(
        {("run.state_changed", 1)}
    ),
    "harness/runtime.py::HarnessRuntime._replan_transient_failures#_event[2]": frozenset(
        {("plan.updated", 1)}
    ),
    "harness/runtime.py::HarnessRuntime._replan_transient_failures#_event[3]": frozenset(
        {("run.state_changed", 1)}
    ),
    "harness/runtime.py::HarnessRuntime._respond#_event[1]": frozenset({("critic.completed", 1)}),
    "harness/runtime.py::HarnessRuntime._respond#_event[2]": frozenset({("answer.delta", 1)}),
    "harness/runtime.py::HarnessRuntime._respond#_event[3]": frozenset({("artifact.created", 1)}),
    "harness/runtime.py::HarnessRuntime._respond#_event[4]": frozenset({("artifact.created", 1)}),
    "harness/runtime.py::HarnessRuntime._respond#_event[5]": frozenset({("run.completed", 1)}),
}

REVIEWED_EVENT_ENUMS: dict[str, EnumFingerprint] = {
    "domain/enums.py::ActionApprovalPurpose": (
        ("EXECUTE", "EXECUTE"),
        ("ROLLBACK", "ROLLBACK"),
    ),
    "domain/enums.py::RunStatus": (
        ("PENDING", "PENDING"),
        ("RUNNING", "RUNNING"),
        ("WAITING_APPROVAL", "WAITING_APPROVAL"),
        ("WAITING_USER", "WAITING_USER"),
        ("REPLANNING", "REPLANNING"),
        ("COMPLETED", "COMPLETED"),
        ("FAILED", "FAILED"),
        ("CANCELLED", "CANCELLED"),
    ),
    "domain/enums.py::WorkflowStatus": (
        ("DRAFT", "DRAFT"),
        ("ACTIVE", "ACTIVE"),
        ("PAUSED", "PAUSED"),
        ("RETIRED", "RETIRED"),
    ),
    "domain/enums.py::WorkspaceDecisionStatus": (
        ("PROPOSED", "PROPOSED"),
        ("ACCEPTED", "ACCEPTED"),
        ("REJECTED", "REJECTED"),
        ("SUPERSEDED", "SUPERSEDED"),
    ),
}
