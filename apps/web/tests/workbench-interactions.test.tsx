import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi, beforeEach } from "vitest";

afterEach(() => {
  cleanup();
});

import { ApiError, api } from "@/lib/api";
import { Composer } from "@/components/composer";
import { RuntimeInspector } from "@/components/runtime-inspector";
import { CollaborationView } from "@/components/collaboration-view";
import type {
  Artifact,
  Claim,
  ConversationSnapshot,
  Evidence,
  MemorySnapshot,
  Run,
  Thread,
  Workspace,
  WorkspaceMember,
  WorkspaceTask,
} from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listThreads: vi.fn(),
      listThreadRuns: vi.fn(),
      listWorkspaceMembers: vi.fn(),
      collaboration: {
        ...actual.api.collaboration,
        listTasks: vi.fn(),
        listDecisions: vi.fn(),
        createTask: vi.fn(),
        createDecision: vi.fn(),
      },
    },
  };
});

const listThreads = vi.mocked(api.listThreads);
const listThreadRuns = vi.mocked(api.listThreadRuns);
const listWorkspaceMembers = vi.mocked(api.listWorkspaceMembers);
const listTasks = vi.mocked(api.collaboration.listTasks);
const listDecisions = vi.mocked(api.collaboration.listDecisions);
const createTask = vi.mocked(api.collaboration.createTask);

function artifact(partial: Partial<Artifact> = {}): Artifact {
  return {
    id: "art-1",
    workspace_id: "ws-1",
    run_id: null,
    kind: "REPORT",
    title: "支付周报",
    media_type: "text/markdown",
    inline_content: { markdown: "# 周报" },
    storage_key: null,
    classification: "INTERNAL",
    lineage: {},
    created_at: "2026-08-20T08:00:00Z",
    ...partial,
  };
}

function workspace(partial: Partial<Workspace> = {}): Workspace {
  return {
    id: "ws-1",
    name: "支付平台",
    description: "",
    classification: "INTERNAL",
    visibility: "PRIVATE",
    updated_at: "2026-08-20T08:00:00Z",
    ...partial,
  };
}

function run(partial: Partial<Run> = {}): Run {
  return {
    id: "123e4567-e89b-42d3-a456-426614174000",
    turn_id: "turn-1",
    status: "COMPLETED",
    agent_version_id: null,
    model_profile_id: null,
    workspace_context: { workspace_id: "ws-1", name: "支付平台" },
    intent: {},
    plan: { route: "ANALYTICS" },
    step_count: 2,
    max_input_tokens: 1000,
    max_output_tokens: 1000,
    max_cost_amount: "1.0",
    input_tokens: 10,
    output_tokens: 20,
    cost_amount: "0.01",
    error_code: null,
    error_message: null,
    replay_of_run_id: null,
    created_at: "2026-08-20T08:00:00Z",
    ...partial,
  } as Run;
}

function claim(partial: Partial<Claim> = {}): Claim {
  return {
    id: "claim-1",
    statement: "支付成功率下降主要由渠道 B 的 5xx 激增导致",
    confidence: "HIGH",
    verification_status: "VERIFIED",
    evidence_ids: [],
    ...partial,
  };
}

function evidence(partial: Partial<Evidence> = {}): Evidence {
  return {
    id: "evidence-1",
    run_id: "123e4567-e89b-42d3-a456-426614174000",
    step_id: null,
    evidence_type: "METRIC",
    source: "prometheus",
    resource: "payment_success_rate",
    observed_at: "2026-08-20T08:00:00Z",
    ingested_at: "2026-08-20T08:00:01Z",
    content: { summary: "支付成功率下降 12%" },
    content_fingerprint: "a".repeat(64),
    confidence: "HIGH",
    classification: "INTERNAL",
    permissions: ["metrics.read"],
    lineage: {},
    ...partial,
  };
}

function memory(partial: Partial<MemorySnapshot> = {}): MemorySnapshot {
  return {
    id: "memory-snapshot-1",
    run_id: "123e4567-e89b-42d3-a456-426614174000",
    memory_id: "memory-1",
    principal_id: "user-1",
    ordinal: 1,
    scope: "WORKSPACE",
    owner_ref: "ws-1",
    content: { preference: "使用支付平台告警阈值" },
    content_fingerprint: "b".repeat(64),
    sensitivity: "INTERNAL",
    policy_decision_id: "policy-1",
    memory_updated_at: "2026-08-20T07:59:00Z",
    captured_at: "2026-08-20T08:00:00Z",
    ...partial,
  };
}

function conversation(partial: Partial<ConversationSnapshot> = {}): ConversationSnapshot {
  return {
    id: "conversation-1",
    run_id: "123e4567-e89b-42d3-a456-426614174000",
    source_thread_id: "thread-1",
    source_turn_id: "turn-previous",
    source_run_id: "run-previous",
    source_artifact_id: null,
    source_principal_id: "user-1",
    ordinal: 1,
    user_content: "最近的支付成功率如何？",
    assistant_content: "最近一周保持稳定。",
    content_fingerprint: "c".repeat(64),
    classification: "INTERNAL",
    captured_at: "2026-08-20T08:00:00Z",
    ...partial,
  };
}

function thread(partial: Partial<Thread> = {}): Thread {
  return {
    id: "thread-1",
    workspace_id: "ws-1",
    title: "支付超时调查",
    status: "ACTIVE",
    created_by: "owner-1",
    parent_thread_id: null,
    forked_from_turn_id: null,
    created_at: "2026-08-20T08:00:00Z",
    updated_at: "2026-08-20T08:00:00Z",
    archived_at: null,
    ...partial,
  } as Thread;
}

function member(partial: Partial<WorkspaceMember> = {}): WorkspaceMember {
  return {
    workspace_id: "ws-1",
    user_id: "user-1",
    display_name: "王晓",
    email: "wangxiao@example.com",
    permissions: ["read", "write"],
    created_by: "owner-1",
    created_at: "2026-08-20T08:00:00Z",
    ...partial,
  };
}

function task(partial: Partial<WorkspaceTask> = {}): WorkspaceTask {
  return {
    id: "task-1",
    workspace_id: "ws-1",
    title: "复核渠道 B 告警阈值",
    description: "",
    status: "OPEN",
    priority: "NORMAL",
    assignee_id: null,
    created_by: "owner-1",
    source_run_id: null,
    due_at: null,
    completed_at: null,
    version: 1,
    created_at: "2026-08-20T08:00:00Z",
    updated_at: "2026-08-20T08:00:00Z",
    ...partial,
  };
}

function composerProps(overrides: Record<string, unknown> = {}) {
  return {
    value: "",
    onChange: vi.fn(),
    onSubmit: vi.fn(),
    onCancel: vi.fn(),
    running: false,
    attachments: [] as Artifact[],
    uploading: false,
    onAttach: vi.fn(),
    onRemoveAttachment: vi.fn(),
    contextArtifacts: [] as Artifact[],
    contextOpen: false,
    contextLoading: false,
    onOpenContext: vi.fn(),
    onCloseContext: vi.fn(),
    onAddContext: vi.fn(),
    ...overrides,
  };
}

describe("Composer interactions", () => {
  it("submits on Enter but not on Shift+Enter or empty input", () => {
    const props = composerProps({ value: "分析支付成功率" });
    render(<Composer {...props} />);
    const textarea = screen.getByLabelText("向 Obsion 提问");
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });
    expect(props.onSubmit).not.toHaveBeenCalled();
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(props.onSubmit).toHaveBeenCalledTimes(1);

    const empty = composerProps({ value: "   " });
    render(<Composer {...empty} />);
    fireEvent.keyDown(screen.getAllByLabelText("向 Obsion 提问")[1], { key: "Enter" });
    expect(empty.onSubmit).not.toHaveBeenCalled();
  });

  it("turns the send button into a stop action while running", () => {
    const props = composerProps({ value: "分析", running: true });
    render(<Composer {...props} />);
    fireEvent.click(screen.getByLabelText("停止运行"));
    expect(props.onCancel).toHaveBeenCalledTimes(1);
    expect(props.onSubmit).not.toHaveBeenCalled();
  });

  it("filters the context picker and adds a readable artifact", () => {
    const contextArtifacts = [
      artifact({ id: "art-1", title: "支付周报" }),
      artifact({ id: "art-2", title: "网关架构图", kind: "DIAGRAM" }),
    ];
    const props = composerProps({ contextOpen: true, contextArtifacts });
    render(<Composer {...props} />);
    fireEvent.change(screen.getByLabelText("搜索工作区上下文"), { target: { value: "周报" } });
    expect(screen.queryByLabelText("添加上下文 支付周报")).not.toBeNull();
    expect(screen.queryByLabelText("添加上下文 网关架构图")).toBeNull();
    fireEvent.click(screen.getByLabelText("添加上下文 支付周报"));
    expect(props.onAddContext).toHaveBeenCalledWith(contextArtifacts[0]);
  });

  it("removes attachments through their chip buttons", () => {
    const props = composerProps({ attachments: [artifact()] });
    render(<Composer {...props} />);
    fireEvent.click(screen.getByLabelText("移除附件 支付周报"));
    expect(props.onRemoveAttachment).toHaveBeenCalledWith("art-1");
  });
});

describe("RuntimeInspector claim actions", () => {
  beforeEach(() => {
    createTask.mockReset();
  });

  function renderInspector(overrides: Partial<Parameters<typeof RuntimeInspector>[0]> = {}) {
    const props: Parameters<typeof RuntimeInspector>[0] = {
      open: true,
      onClose: vi.fn(),
      onReplay: vi.fn(),
      run: run(),
      events: [],
      steps: [],
      evidence: [],
      memories: [],
      conversation: [],
      claims: [claim()],
      artifacts: [],
      onOpenCollaboration: vi.fn(),
      ...overrides,
    };
    render(<RuntimeInspector {...props} />);
    return props;
  }

  it("turns a verified claim into a task with source-Run provenance", async () => {
    createTask.mockResolvedValue(task());
    const props = renderInspector();
    fireEvent.click(screen.getByRole("tab", { name: /结论/ }));
    fireEvent.click(screen.getByRole("button", { name: /转为任务/ }));

    const titleInput = screen.getByDisplayValue(/结论 C1：支付成功率下降/);
    expect(titleInput).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "创建任务" }));

    await waitFor(() => expect(createTask).toHaveBeenCalledTimes(1));
    const [workspaceId, payload] = createTask.mock.calls[0];
    expect(workspaceId).toBe("ws-1");
    expect(payload.source_run_id).toBe("123e4567-e89b-42d3-a456-426614174000");
    expect(String(payload.title)).toContain("结论 C1：");

    await screen.findByText("任务已创建，并带来源 Run 溯源");
    fireEvent.click(screen.getByRole("button", { name: "在协作中查看" }));
    expect(props.onOpenCollaboration).toHaveBeenCalledTimes(1);
  });

  it("hides claim actions while the run is not completed", () => {
    renderInspector({ run: run({ status: "RUNNING" }) });
    fireEvent.click(screen.getByRole("tab", { name: /结论/ }));
    expect(screen.queryByRole("button", { name: /转为任务/ })).toBeNull();
  });

  it("maps source-Run mismatches to an actionable message", async () => {
    createTask.mockRejectedValue(
      new ApiError("workspace_source_run_mismatch", "The source run belongs elsewhere"),
    );
    renderInspector();
    fireEvent.click(screen.getByRole("tab", { name: /结论/ }));
    fireEvent.click(screen.getByRole("button", { name: /转为任务/ }));
    fireEvent.click(screen.getByRole("button", { name: "创建任务" }));
    await screen.findByText("来源 Run 必须属于当前工作空间，请刷新后重试。");
  });
});

describe("RuntimeInspector tabs", () => {
  function renderInspector() {
    render(
      <RuntimeInspector
        open
        onClose={vi.fn()}
        onReplay={vi.fn()}
        run={run()}
        events={[]}
        steps={[]}
        evidence={[evidence()]}
        memories={[memory()]}
        conversation={[conversation()]}
        claims={[claim({ evidence_ids: ["evidence-1"] })]}
        artifacts={[artifact()]}
      />,
    );
  }

  it("exposes tabs with roving keyboard focus", () => {
    renderInspector();
    const runtimeTab = screen.getByRole("tab", { name: "轨迹" });
    expect(runtimeTab.getAttribute("aria-selected")).toBe("true");

    runtimeTab.focus();
    fireEvent.keyDown(runtimeTab, { key: "ArrowRight" });
    const contextTab = screen.getByRole("tab", { name: /上下文/ });
    expect(contextTab.getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(contextTab);

    fireEvent.keyDown(contextTab, { key: "End" });
    const artifactsTab = screen.getByRole("tab", { name: /产物/ });
    expect(artifactsTab.getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(artifactsTab);

    fireEvent.keyDown(artifactsTab, { key: "Home" });
    expect(runtimeTab.getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(runtimeTab);
  });

  it("opens the context, evidence, memory, claim evidence, and artifact details", () => {
    renderInspector();

    fireEvent.click(screen.getByRole("tab", { name: /上下文/ }));
    expect(screen.getByText("最近的支付成功率如何？")).toBeDefined();

    fireEvent.click(screen.getByRole("tab", { name: /证据/ }));
    fireEvent.click(screen.getByRole("button", { name: /prometheus/ }));
    expect(screen.getByLabelText("关闭证据详情")).toBeDefined();
    fireEvent.click(screen.getByLabelText("关闭证据详情"));

    fireEvent.click(screen.getByRole("tab", { name: /记忆/ }));
    expect(screen.getByText("工作空间记忆")).toBeDefined();

    fireEvent.click(screen.getByRole("tab", { name: /结论/ }));
    fireEvent.click(screen.getByLabelText("查看证据：prometheus"));
    expect(screen.getByRole("tab", { name: /证据/ }).getAttribute("aria-selected")).toBe("true");
    fireEvent.click(screen.getByLabelText("关闭证据详情"));

    fireEvent.click(screen.getByRole("tab", { name: /产物/ }));
    fireEvent.click(screen.getByRole("button", { name: /支付周报/ }));
    expect(screen.getByLabelText("关闭产物详情")).toBeDefined();
  });
});

describe("CollaborationView task creation", () => {
  beforeEach(() => {
    listTasks.mockReset();
    listDecisions.mockReset();
    listThreads.mockReset();
    listThreadRuns.mockReset();
    listWorkspaceMembers.mockReset();
    createTask.mockReset();
    listTasks.mockResolvedValue([task()]);
    listDecisions.mockResolvedValue([]);
    listThreads.mockResolvedValue([thread()]);
    listThreadRuns.mockResolvedValue([run()]);
    listWorkspaceMembers.mockResolvedValue([member()]);
  });

  it("creates a task from the modal with an optional source Run", async () => {
    createTask.mockResolvedValue(task({ id: "task-2", title: "跟进渠道 B" }));
    render(<CollaborationView workspace={workspace()} />);
    await screen.findByText("复核渠道 B 告警阈值");

    fireEvent.click(screen.getAllByRole("button", { name: /新建任务/ })[0]);
    await screen.findByText("新建工作任务");
    fireEvent.change(screen.getByLabelText(/任务名称/), { target: { value: "跟进渠道 B" } });
    fireEvent.change(screen.getByLabelText(/来源 Run/), {
      target: { value: "123e4567-e89b-42d3-a456-426614174000" },
    });
    fireEvent.click(screen.getByRole("button", { name: "创建任务" }));

    await waitFor(() => expect(createTask).toHaveBeenCalledTimes(1));
    const [workspaceId, payload] = createTask.mock.calls[0];
    expect(workspaceId).toBe("ws-1");
    expect(payload.title).toBe("跟进渠道 B");
    expect(payload.source_run_id).toBe("123e4567-e89b-42d3-a456-426614174000");
  });

  it("maps invalid assignees to an actionable message", async () => {
    createTask.mockRejectedValue(
      new ApiError("workspace_task_assignee_invalid", "The assignee is not an active member"),
    );
    render(<CollaborationView workspace={workspace()} />);
    await screen.findByText("复核渠道 B 告警阈值");

    fireEvent.click(screen.getAllByRole("button", { name: /新建任务/ })[0]);
    await screen.findByText("新建工作任务");
    fireEvent.change(screen.getByLabelText(/任务名称/), { target: { value: "指派外部成员" } });
    fireEvent.click(screen.getByRole("button", { name: "创建任务" }));

    await screen.findByText("指派的成员必须是该工作空间的在职成员，请刷新成员列表后重试。");
  });

  it("keeps the version-conflict guidance visible after the refresh", async () => {
    createTask.mockRejectedValue(
      new ApiError("workspace_task_version_conflict", "The record changed underneath you"),
    );
    render(<CollaborationView workspace={workspace()} />);
    await screen.findByText("复核渠道 B 告警阈值");

    fireEvent.click(screen.getAllByRole("button", { name: /新建任务/ })[0]);
    await screen.findByText("新建工作任务");
    fireEvent.change(screen.getByLabelText(/任务名称/), { target: { value: "并发编辑" } });
    fireEvent.click(screen.getByRole("button", { name: "创建任务" }));

    // The follow-up refresh used to clear this notice before anyone could
    // read it; the guidance must survive the reload.
    await screen.findByText("记录已被其他成员更新，已为你刷新到最新版本。请确认后重试。");
    expect(listTasks.mock.calls.length).toBeGreaterThanOrEqual(2);
  });
});
