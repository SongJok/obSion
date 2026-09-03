import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Workbench } from "@/components/workbench";
import { api } from "@/lib/api";
import { streamRunEvents } from "@/lib/app-server";
import type {
  Artifact,
  Claim,
  ConversationSnapshot,
  Evidence,
  MemorySnapshot,
  Run,
  RunEvent,
  RunFeedback,
  RunStep,
  SessionPrincipal,
  Thread,
  Turn,
  Workspace,
  WorkspaceMember,
  WorkspaceTask,
} from "@/lib/types";

vi.mock("@/lib/app-server", () => ({
  streamRunEvents: vi.fn(() => new Promise<() => void>(() => undefined)),
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listWorkspaces: vi.fn(),
      listThreads: vi.fn(),
      listTurns: vi.fn(),
      listThreadRuns: vi.fn(),
      createThread: vi.fn(),
      createTurn: vi.fn(),
      getRun: vi.fn(),
      cancelRun: vi.fn(),
      replayRun: vi.fn(),
      getRunFeedback: vi.fn(),
      recordRunFeedback: vi.fn(),
      listEvents: vi.fn(),
      listSteps: vi.fn(),
      listEvidence: vi.fn(),
      listRunMemories: vi.fn(),
      listRunConversation: vi.fn(),
      listClaims: vi.fn(),
      listArtifacts: vi.fn(),
      listWorkspaceArtifacts: vi.fn(),
      listWorkspaceMembers: vi.fn(),
      uploadArtifact: vi.fn(),
      collaboration: {
        ...actual.api.collaboration,
        listTasks: vi.fn(),
        listDecisions: vi.fn(),
        versions: vi.fn(),
      },
    },
  };
});

const mockedApi = vi.mocked(api);
const mockedStreamRunEvents = vi.mocked(streamRunEvents);
const collaboration = vi.mocked(api.collaboration);

const NOW = "2026-09-03T08:00:00Z";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function workspace(id: string): Workspace {
  return {
    id,
    name: `空间 ${id}`,
    description: "",
    classification: "INTERNAL",
    visibility: "PRIVATE",
    updated_at: NOW,
  };
}

function thread(workspaceId: string, suffix: string): Thread {
  return {
    id: `thread-${suffix}`,
    workspace_id: workspaceId,
    title: `任务 ${suffix}`,
    status: "ACTIVE",
    created_by: "user-1",
    parent_thread_id: null,
    forked_from_turn_id: null,
    created_at: NOW,
    updated_at: NOW,
    archived_at: null,
  };
}

function turn(threadId: string, suffix: string): Turn {
  return {
    id: `turn-${suffix}`,
    thread_id: threadId,
    ordinal: 1,
    created_by: "user-1",
    input_text: `问题 ${suffix}`,
    context_refs: [],
    attachment_refs: [],
    created_at: NOW,
  };
}

function run(
  workspaceId: string,
  turnId: string,
  suffix: string,
  status: Run["status"] = "COMPLETED",
): Run {
  return {
    id: `run-${suffix}`,
    turn_id: turnId,
    status,
    agent_version_id: null,
    model_profile_id: null,
    prompt_pins: [],
    context_budget: {},
    conversation_compact: {},
    workspace_context: {
      workspace_id: workspaceId,
      name: `空间 ${workspaceId}`,
      classification: "INTERNAL",
      visibility: "PRIVATE",
      description: "",
    },
    intent: {},
    plan: { route: "ANALYTICS" },
    max_steps: 20,
    timeout_seconds: 1_800,
    step_count: 1,
    max_input_tokens: 10_000,
    max_output_tokens: 4_000,
    max_cost_amount: "10.0",
    input_tokens: 10,
    output_tokens: 20,
    cost_amount: "0.01",
    started_at: NOW,
    completed_at: NOW,
    cancellation_requested_at: null,
    error_code: null,
    error_message: null,
    replay_of_run_id: null,
    created_at: NOW,
    updated_at: NOW,
  };
}

function event(runId: string, suffix: string): RunEvent {
  return {
    id: `event-${suffix}`,
    event_id: `event-${suffix}`,
    organization_id: "org-1",
    aggregate_type: "run",
    aggregate_id: runId,
    sequence: 1,
    name: `run.completed.${suffix}`,
    run_id: runId,
    run_sequence: 1,
    causation_id: null,
    correlation_id: runId,
    actor_type: "SYSTEM",
    actor_id: null,
    schema_version: 1,
    classification: "INTERNAL",
    payload: {},
    created_at: NOW,
  };
}

function step(runId: string, suffix: string): RunStep {
  return {
    id: `step-${suffix}`,
    run_id: runId,
    ordinal: 1,
    name: `步骤 ${suffix}`,
    kind: "CAPABILITY",
    status: "COMPLETED",
    depends_on: [],
    capability_version_id: null,
    output_ref: null,
    retry_count: 0,
    error_code: null,
    started_at: NOW,
    completed_at: NOW,
  };
}

function evidence(runId: string, suffix: string): Evidence {
  return {
    id: `evidence-${suffix}`,
    run_id: runId,
    step_id: `step-${suffix}`,
    evidence_type: "METRIC",
    source: `证据来源 ${suffix}`,
    resource: `metric://${suffix}`,
    observed_at: NOW,
    ingested_at: NOW,
    content: { summary: `证据内容 ${suffix}` },
    content_fingerprint: suffix.padEnd(64, "0"),
    confidence: "0.95",
    classification: "INTERNAL",
    permissions: ["metrics.read"],
    lineage: {},
  };
}

function memory(runId: string, suffix: string): MemorySnapshot {
  return {
    id: `memory-snapshot-${suffix}`,
    run_id: runId,
    memory_id: `memory-${suffix}`,
    principal_id: "user-1",
    ordinal: 1,
    scope: "WORKSPACE",
    owner_ref: "workspace",
    content: { summary: `记忆 ${suffix}` },
    content_fingerprint: suffix.padEnd(64, "1"),
    sensitivity: "INTERNAL",
    policy_decision_id: `policy-${suffix}`,
    memory_updated_at: NOW,
    captured_at: NOW,
  };
}

function conversationSnapshot(runId: string, threadId: string, suffix: string): ConversationSnapshot {
  return {
    id: `conversation-${suffix}`,
    run_id: runId,
    source_thread_id: threadId,
    source_turn_id: `source-turn-${suffix}`,
    source_run_id: null,
    source_artifact_id: null,
    source_principal_id: "user-1",
    ordinal: 1,
    user_content: `历史问题 ${suffix}`,
    assistant_content: `历史回答 ${suffix}`,
    content_fingerprint: suffix.padEnd(64, "2"),
    classification: "INTERNAL",
    captured_at: NOW,
  };
}

function claim(runId: string, suffix: string): Claim {
  return {
    id: `claim-${suffix}`,
    run_id: runId,
    ordinal: 1,
    statement: `结论内容 ${suffix}`,
    confidence: "0.95",
    verification_status: "VERIFIED",
    critic_notes: {},
    evidence_ids: [`evidence-${suffix}`],
  };
}

function artifact(workspaceId: string, runId: string | null, suffix: string): Artifact {
  return {
    id: `artifact-${suffix}`,
    workspace_id: workspaceId,
    run_id: runId,
    kind: "REPORT",
    title: `产物标题 ${suffix}`,
    media_type: "text/markdown",
    inline_content: { markdown: `# 回答内容 ${suffix}` },
    storage_key: null,
    classification: "INTERNAL",
    lineage: {},
    created_at: NOW,
  };
}

function feedback(runId: string): RunFeedback {
  return {
    id: `feedback-${runId}`,
    run_id: runId,
    user_id: "user-1",
    rating: "HELPFUL",
    reason: "",
    version: 1,
    created_at: NOW,
    updated_at: NOW,
  };
}

function member(workspaceId: string): WorkspaceMember {
  return {
    workspace_id: workspaceId,
    user_id: "user-1",
    display_name: "测试用户",
    email: "tester@example.com",
    permissions: ["read", "write"],
    created_by: "user-1",
    created_at: NOW,
  };
}

function task(workspaceId: string, sourceRunId: string, suffix: string): WorkspaceTask {
  return {
    id: `task-${suffix}`,
    workspace_id: workspaceId,
    title: `来源任务 ${suffix}`,
    description: "",
    status: "OPEN",
    priority: "NORMAL",
    assignee_id: null,
    created_by: "user-1",
    source_run_id: sourceRunId,
    due_at: null,
    completed_at: null,
    version: 1,
    created_at: NOW,
    updated_at: NOW,
  };
}

function inspection(runId: string, workspaceId: string, threadId: string, suffix: string) {
  return {
    events: [event(runId, suffix)],
    steps: [step(runId, suffix)],
    evidence: [evidence(runId, suffix)],
    memories: [memory(runId, suffix)],
    conversation: [conversationSnapshot(runId, threadId, suffix)],
    claims: [claim(runId, suffix)],
    artifacts: [artifact(workspaceId, runId, suffix)],
  };
}

function principal(): SessionPrincipal {
  return {
    principal_id: "user-1",
    organization_id: "org-1",
    display_name: "测试用户",
    department: "质量工程",
    roles: ["member"],
  };
}

function inspectionMethod<T>(
  values: Record<string, T>,
  method: (runId: string, ...rest: never[]) => Promise<T>,
) {
  vi.mocked(method).mockImplementation(async (runId: string) => values[runId] ?? [] as T);
}

function configureDefaults(workspaces: Workspace[]) {
  mockedApi.listWorkspaces.mockResolvedValue(workspaces);
  mockedApi.listThreads.mockResolvedValue([]);
  mockedApi.listTurns.mockResolvedValue([]);
  mockedApi.listThreadRuns.mockResolvedValue([]);
  mockedApi.getRunFeedback.mockResolvedValue(null);
  mockedApi.listEvents.mockResolvedValue([]);
  mockedApi.listSteps.mockResolvedValue([]);
  mockedApi.listEvidence.mockResolvedValue([]);
  mockedApi.listRunMemories.mockResolvedValue([]);
  mockedApi.listRunConversation.mockResolvedValue([]);
  mockedApi.listClaims.mockResolvedValue([]);
  mockedApi.listArtifacts.mockResolvedValue([]);
  mockedApi.listWorkspaceArtifacts.mockResolvedValue([]);
  collaboration.listTasks.mockResolvedValue([]);
  collaboration.listDecisions.mockResolvedValue([]);
  collaboration.versions.mockResolvedValue([]);
  mockedApi.listWorkspaceMembers.mockResolvedValue([]);
}

function renderWorkbench() {
  return render(<Workbench principal={principal()} onSignOut={vi.fn()} />);
}

async function selectWorkspace(workspaceId: string) {
  fireEvent.change(await screen.findByLabelText("选择工作空间"), {
    target: { value: workspaceId },
  });
}

async function openCollaboration() {
  fireEvent.click(await screen.findByRole("button", { name: "任务与决策" }));
}

async function assertSelectedWorkspace(workspaceId: string) {
  await waitFor(() => {
    expect((screen.getByLabelText("选择工作空间") as HTMLSelectElement).value).toBe(workspaceId);
  });
}

async function assertInspection(suffix: string) {
  const evidenceTab = await screen.findByRole("tab", { name: /证据/ });
  fireEvent.click(evidenceTab);
  await waitFor(() => {
    expect(evidenceTab.textContent).toContain("1");
    expect(screen.getByRole("tabpanel").textContent).toContain(`证据来源 ${suffix}`);
  });
  const claimsTab = screen.getByRole("tab", { name: /结论/ });
  fireEvent.click(claimsTab);
  await waitFor(() => {
    expect(claimsTab.textContent).toContain("1");
    expect(screen.getByRole("tabpanel").textContent).toContain(`结论内容 ${suffix}`);
  });
  const artifactsTab = screen.getByRole("tab", { name: /产物/ });
  fireEvent.click(artifactsTab);
  await waitFor(() => {
    expect(artifactsTab.textContent).toContain("1");
    expect(screen.getByRole("tabpanel").textContent).toContain(`产物标题 ${suffix}`);
  });
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
});

describe("Workbench root orchestration ownership", () => {
  it("keeps only the newest Workspace thread response", async () => {
    const workspaceA = workspace("ws-a");
    const workspaceB = workspace("ws-b");
    const aThreads = deferred<Thread[]>();
    const bThreads = deferred<Thread[]>();
    configureDefaults([workspaceA, workspaceB]);
    mockedApi.listThreads.mockImplementation((workspaceId) => {
      if (workspaceId === workspaceA.id) return aThreads.promise;
      if (workspaceId === workspaceB.id) return bThreads.promise;
      return Promise.resolve([]);
    });
    renderWorkbench();
    await screen.findByLabelText("选择工作空间");

    await selectWorkspace(workspaceB.id);
    await act(async () => {
      bThreads.resolve([thread(workspaceB.id, "B")]);
      await bThreads.promise;
    });
    expect(await screen.findByText("任务 B")).toBeDefined();

    await act(async () => {
      aThreads.resolve([thread(workspaceA.id, "A")]);
      await aThreads.promise;
    });
    expect(screen.getByText("任务 B")).toBeDefined();
    expect(screen.queryByText("任务 A")).toBeNull();
    await assertSelectedWorkspace(workspaceB.id);
  });

  it("commits one complete Thread and inspection snapshot after reverse completion", async () => {
    const currentWorkspace = workspace("ws-1");
    const threadA = thread(currentWorkspace.id, "A");
    const threadB = thread(currentWorkspace.id, "B");
    const turnA = turn(threadA.id, "A");
    const turnB = turn(threadB.id, "B");
    const runA = run(currentWorkspace.id, turnA.id, "A");
    const runB = run(currentWorkspace.id, turnB.id, "B");
    const aTurns = deferred<Turn[]>();
    const bTurns = deferred<Turn[]>();
    const aRuns = deferred<Run[]>();
    const bRuns = deferred<Run[]>();
    configureDefaults([currentWorkspace]);
    mockedApi.listThreads.mockResolvedValue([threadA, threadB]);
    mockedApi.listTurns.mockImplementation((threadId) =>
      threadId === threadA.id ? aTurns.promise : bTurns.promise,
    );
    mockedApi.listThreadRuns.mockImplementation((threadId) =>
      threadId === threadA.id ? aRuns.promise : bRuns.promise,
    );
    const snapshotA = inspection(runA.id, currentWorkspace.id, threadA.id, "A");
    const snapshotB = inspection(runB.id, currentWorkspace.id, threadB.id, "B");
    inspectionMethod({ [runA.id]: snapshotA.events, [runB.id]: snapshotB.events }, api.listEvents);
    inspectionMethod({ [runA.id]: snapshotA.steps, [runB.id]: snapshotB.steps }, api.listSteps);
    inspectionMethod({ [runA.id]: snapshotA.evidence, [runB.id]: snapshotB.evidence }, api.listEvidence);
    inspectionMethod({ [runA.id]: snapshotA.memories, [runB.id]: snapshotB.memories }, api.listRunMemories);
    inspectionMethod({ [runA.id]: snapshotA.conversation, [runB.id]: snapshotB.conversation }, api.listRunConversation);
    inspectionMethod({ [runA.id]: snapshotA.claims, [runB.id]: snapshotB.claims }, api.listClaims);
    inspectionMethod({ [runA.id]: snapshotA.artifacts, [runB.id]: snapshotB.artifacts }, api.listArtifacts);
    mockedApi.getRunFeedback.mockImplementation(async (runId) => feedback(runId));
    renderWorkbench();
    await screen.findByText("任务 A");

    fireEvent.click(screen.getByText("任务 A"));
    expect(screen.queryByText("问题 A")).toBeNull();
    fireEvent.click(screen.getByText("任务 B"));
    await act(async () => {
      bTurns.resolve([turnB]);
      bRuns.resolve([runB]);
      await Promise.all([bTurns.promise, bRuns.promise]);
    });
    expect(await screen.findByText("问题 B")).toBeDefined();
    await assertInspection("B");

    await act(async () => {
      aTurns.resolve([turnA]);
      aRuns.resolve([runA]);
      await Promise.all([aTurns.promise, aRuns.promise]);
    });
    expect(screen.getByText("问题 B")).toBeDefined();
    expect(screen.queryByText("问题 A")).toBeNull();
    expect(screen.queryByText("证据来源 A")).toBeNull();
    expect(screen.queryByText("结论内容 A")).toBeNull();
    expect(screen.queryByText("产物标题 A")).toBeNull();
  });

  it("opens a Collaboration source Run through its owning Thread", async () => {
    const currentWorkspace = workspace("ws-1");
    const threadA = thread(currentWorkspace.id, "owner-A");
    const threadB = thread(currentWorkspace.id, "owner-B");
    const turnA = turn(threadA.id, "owner-A");
    const turnB = turn(threadB.id, "owner-B");
    const runA = run(currentWorkspace.id, turnA.id, "owner-A");
    const runB = run(currentWorkspace.id, turnB.id, "owner-B");
    configureDefaults([currentWorkspace]);
    mockedApi.listThreads.mockResolvedValue([threadA, threadB]);
    mockedApi.listTurns.mockImplementation(async (threadId) =>
      threadId === threadA.id ? [turnA] : [turnB],
    );
    mockedApi.listThreadRuns.mockImplementation(async (threadId) =>
      threadId === threadA.id ? [runA] : [runB],
    );
    const snapshotA = inspection(runA.id, currentWorkspace.id, threadA.id, "owner-A");
    const snapshotB = inspection(runB.id, currentWorkspace.id, threadB.id, "owner-B");
    inspectionMethod({ [runA.id]: snapshotA.events, [runB.id]: snapshotB.events }, api.listEvents);
    inspectionMethod({ [runA.id]: snapshotA.steps, [runB.id]: snapshotB.steps }, api.listSteps);
    inspectionMethod({ [runA.id]: snapshotA.evidence, [runB.id]: snapshotB.evidence }, api.listEvidence);
    inspectionMethod({ [runA.id]: snapshotA.memories, [runB.id]: snapshotB.memories }, api.listRunMemories);
    inspectionMethod({ [runA.id]: snapshotA.conversation, [runB.id]: snapshotB.conversation }, api.listRunConversation);
    inspectionMethod({ [runA.id]: snapshotA.claims, [runB.id]: snapshotB.claims }, api.listClaims);
    inspectionMethod({ [runA.id]: snapshotA.artifacts, [runB.id]: snapshotB.artifacts }, api.listArtifacts);
    collaboration.listTasks.mockResolvedValue([task(currentWorkspace.id, runB.id, "owner-B")]);
    collaboration.listDecisions.mockResolvedValue([]);
    mockedApi.listWorkspaceMembers.mockResolvedValue([member(currentWorkspace.id)]);
    renderWorkbench();
    await screen.findByText("任务 owner-A");

    fireEvent.click(screen.getByText("任务 owner-A"));
    expect(await screen.findByText("问题 owner-A")).toBeDefined();
    await openCollaboration();
    fireEvent.click(await screen.findByTitle("在 Runtime 面板中查看来源 Run"));

    expect(await screen.findByText("问题 owner-B")).toBeDefined();
    expect(screen.queryByText("问题 owner-A")).toBeNull();
    expect(screen.getAllByText("任务 owner-B").length).toBeGreaterThan(0);
    await assertInspection("owner-B");
    expect(mockedApi.getRun).not.toHaveBeenCalled();
  });

  it("ignores a slower source-Run inspection after a newer source Run commits", async () => {
    const currentWorkspace = workspace("ws-1");
    const sourceThread = thread(currentWorkspace.id, "source");
    const runA = run(currentWorkspace.id, "turn-A", "source-A");
    const runB = run(currentWorkspace.id, "turn-B", "source-B");
    const claimsA = deferred<Claim[]>();
    configureDefaults([currentWorkspace]);
    mockedApi.listThreads.mockResolvedValue([sourceThread]);
    mockedApi.listTurns.mockResolvedValue([
      { ...turn(sourceThread.id, "source-A"), id: runA.turn_id },
      { ...turn(sourceThread.id, "source-B"), id: runB.turn_id, ordinal: 2 },
    ]);
    collaboration.listTasks.mockResolvedValue([
      task(currentWorkspace.id, runA.id, "A"),
      task(currentWorkspace.id, runB.id, "B"),
    ]);
    collaboration.listDecisions.mockResolvedValue([]);
    mockedApi.listWorkspaceMembers.mockResolvedValue([member(currentWorkspace.id)]);
    mockedApi.listThreadRuns.mockResolvedValue([runA, runB]);
    const snapshotA = inspection(runA.id, currentWorkspace.id, sourceThread.id, "source-A");
    const snapshotB = inspection(runB.id, currentWorkspace.id, sourceThread.id, "source-B");
    inspectionMethod({ [runA.id]: snapshotA.events, [runB.id]: snapshotB.events }, api.listEvents);
    inspectionMethod({ [runA.id]: snapshotA.steps, [runB.id]: snapshotB.steps }, api.listSteps);
    inspectionMethod({ [runA.id]: snapshotA.evidence, [runB.id]: snapshotB.evidence }, api.listEvidence);
    inspectionMethod({ [runA.id]: snapshotA.memories, [runB.id]: snapshotB.memories }, api.listRunMemories);
    inspectionMethod({ [runA.id]: snapshotA.conversation, [runB.id]: snapshotB.conversation }, api.listRunConversation);
    mockedApi.listClaims.mockImplementation((runId) =>
      runId === runA.id ? claimsA.promise : Promise.resolve(snapshotB.claims),
    );
    inspectionMethod({ [runA.id]: snapshotA.artifacts, [runB.id]: snapshotB.artifacts }, api.listArtifacts);
    renderWorkbench();
    await openCollaboration();
    const firstTask = await screen.findByText("来源任务 A");
    const taskPanel = firstTask.closest("section") ?? document.body;
    const sourceButtons = within(taskPanel).getAllByTitle("在 Runtime 面板中查看来源 Run");

    fireEvent.click(sourceButtons[0]);
    await waitFor(() => expect(mockedApi.listClaims).toHaveBeenCalledWith(runA.id));
    await openCollaboration();
    const refreshedButtons = await screen.findAllByTitle("在 Runtime 面板中查看来源 Run");
    fireEvent.click(refreshedButtons[1]);
    await assertInspection("source-B");

    await act(async () => {
      claimsA.resolve(snapshotA.claims);
      await claimsA.promise;
    });
    expect(screen.queryByText("证据来源 source-A")).toBeNull();
    expect(screen.queryByText("结论内容 source-A")).toBeNull();
    expect(screen.queryByText("产物标题 source-A")).toBeNull();
  });

  it("rejects a mismatched inspection atomically without replacing the prior Run", async () => {
    const currentWorkspace = workspace("ws-1");
    const sourceThread = thread(currentWorkspace.id, "source");
    const goodRun = run(currentWorkspace.id, "turn-good", "good");
    const badRun = run(currentWorkspace.id, "turn-bad", "bad");
    goodRun.created_at = "2026-09-03T08:00:00Z";
    badRun.created_at = "2026-09-03T07:59:00Z";
    configureDefaults([currentWorkspace]);
    mockedApi.listThreads.mockResolvedValue([sourceThread]);
    mockedApi.listTurns.mockResolvedValue([
      { ...turn(sourceThread.id, "good"), id: goodRun.turn_id },
      { ...turn(sourceThread.id, "bad"), id: badRun.turn_id, ordinal: 2 },
    ]);
    collaboration.listTasks.mockResolvedValue([
      task(currentWorkspace.id, goodRun.id, "good"),
      task(currentWorkspace.id, badRun.id, "bad"),
    ]);
    collaboration.listDecisions.mockResolvedValue([]);
    mockedApi.listWorkspaceMembers.mockResolvedValue([member(currentWorkspace.id)]);
    mockedApi.listThreadRuns.mockResolvedValue([goodRun]);
    const good = inspection(goodRun.id, currentWorkspace.id, sourceThread.id, "good");
    const bad = inspection(badRun.id, currentWorkspace.id, sourceThread.id, "bad");
    bad.claims = [claim(goodRun.id, "wrong-owner")];
    inspectionMethod({ [goodRun.id]: good.events, [badRun.id]: bad.events }, api.listEvents);
    inspectionMethod({ [goodRun.id]: good.steps, [badRun.id]: bad.steps }, api.listSteps);
    inspectionMethod({ [goodRun.id]: good.evidence, [badRun.id]: bad.evidence }, api.listEvidence);
    inspectionMethod({ [goodRun.id]: good.memories, [badRun.id]: bad.memories }, api.listRunMemories);
    inspectionMethod({ [goodRun.id]: good.conversation, [badRun.id]: bad.conversation }, api.listRunConversation);
    inspectionMethod({ [goodRun.id]: good.claims, [badRun.id]: bad.claims }, api.listClaims);
    inspectionMethod({ [goodRun.id]: good.artifacts, [badRun.id]: bad.artifacts }, api.listArtifacts);
    collaboration.versions.mockResolvedValue([]);
    renderWorkbench();
    await openCollaboration();
    fireEvent.click(await screen.findByText(
      `${sourceThread.title} · Run ${goodRun.id.slice(0, 8)}`,
    ));
    await waitFor(() => expect(mockedApi.listEvidence).toHaveBeenCalledWith(goodRun.id));
    expect(await screen.findByText("问题 good")).toBeDefined();
    expect(mockedApi.listEvents.mock.calls.map(([runId]) => runId)).toContain(goodRun.id);
    expect(mockedApi.listClaims.mock.calls.map(([runId]) => runId)).toContain(goodRun.id);
    expect(mockedApi.listArtifacts.mock.calls.map(([runId]) => runId)).toContain(goodRun.id);
    await assertInspection("good");

    mockedApi.listThreadRuns.mockResolvedValue([goodRun, badRun]);
    await openCollaboration();
    fireEvent.click(await screen.findByText(
      `${sourceThread.title} · Run ${badRun.id.slice(0, 8)}`,
    ));
    await waitFor(() => expect(mockedApi.listClaims).toHaveBeenCalledWith(badRun.id));
    fireEvent.click(await screen.findByRole("button", { name: "智能工作台" }));
    await screen.findByText("结论归属与所选 Run 不一致");
    await assertInspection("good");
    expect(screen.queryByText("结论内容 wrong-owner")).toBeNull();
    expect(screen.queryByText("产物标题 bad")).toBeNull();
  });

  it("keeps Context Picker results scoped to the newest Workspace", async () => {
    const workspaceA = workspace("ws-a");
    const workspaceB = workspace("ws-b");
    const contextA = deferred<Artifact[]>();
    configureDefaults([workspaceA, workspaceB]);
    mockedApi.listThreads.mockResolvedValue([]);
    mockedApi.listWorkspaceArtifacts.mockImplementation((workspaceId) =>
      workspaceId === workspaceA.id
        ? contextA.promise
        : Promise.resolve([artifact(workspaceB.id, null, "context-B")]),
    );
    renderWorkbench();
    await assertSelectedWorkspace(workspaceA.id);

    fireEvent.click(screen.getByLabelText("添加上下文"));
    await selectWorkspace(workspaceB.id);
    fireEvent.click(await screen.findByLabelText("添加上下文"));
    expect(await screen.findByText("产物标题 context-B")).toBeDefined();

    await act(async () => {
      contextA.resolve([artifact(workspaceA.id, null, "context-A")]);
      await contextA.promise;
    });
    expect(screen.getByText("产物标题 context-B")).toBeDefined();
    expect(screen.queryByText("产物标题 context-A")).toBeNull();
  });

  it("does not continue loading a superseded source Run", async () => {
    const currentWorkspace = workspace("ws-1");
    const sourceThread = thread(currentWorkspace.id, "source");
    const runA = run(currentWorkspace.id, "turn-A", "source-A");
    const runB = run(currentWorkspace.id, "turn-B", "source-B");
    const ownerResolutionA = deferred<Thread[]>();
    configureDefaults([currentWorkspace]);
    mockedApi.listThreads.mockResolvedValue([sourceThread]);
    mockedApi.listTurns.mockResolvedValue([
      { ...turn(sourceThread.id, "source-A"), id: runA.turn_id },
      { ...turn(sourceThread.id, "source-B"), id: runB.turn_id, ordinal: 2 },
    ]);
    collaboration.listTasks.mockResolvedValue([
      task(currentWorkspace.id, runA.id, "A"),
      task(currentWorkspace.id, runB.id, "B"),
    ]);
    mockedApi.listWorkspaceMembers.mockResolvedValue([member(currentWorkspace.id)]);
    mockedApi.listThreadRuns.mockResolvedValue([runA, runB]);
    const snapshotB = inspection(runB.id, currentWorkspace.id, sourceThread.id, "source-B");
    inspectionMethod({ [runB.id]: snapshotB.events }, api.listEvents);
    inspectionMethod({ [runB.id]: snapshotB.steps }, api.listSteps);
    inspectionMethod({ [runB.id]: snapshotB.evidence }, api.listEvidence);
    inspectionMethod({ [runB.id]: snapshotB.memories }, api.listRunMemories);
    inspectionMethod({ [runB.id]: snapshotB.conversation }, api.listRunConversation);
    inspectionMethod({ [runB.id]: snapshotB.claims }, api.listClaims);
    inspectionMethod({ [runB.id]: snapshotB.artifacts }, api.listArtifacts);
    renderWorkbench();
    await openCollaboration();
    const sourceButtons = await screen.findAllByTitle("在 Runtime 面板中查看来源 Run");
    mockedApi.listThreads.mockReset();
    mockedApi.listThreads
      .mockReturnValueOnce(ownerResolutionA.promise)
      .mockResolvedValue([sourceThread]);

    fireEvent.click(sourceButtons[0]);
    await waitFor(() => expect(mockedApi.listThreads).toHaveBeenCalledTimes(1));
    fireEvent.click(sourceButtons[1]);
    await assertInspection("source-B");
    await act(async () => {
      ownerResolutionA.resolve([sourceThread]);
      await ownerResolutionA.promise;
    });

    expect(mockedApi.listEvents).not.toHaveBeenCalledWith(runA.id);
    expect(mockedApi.listSteps).not.toHaveBeenCalledWith(runA.id);
    expect(mockedApi.listClaims).not.toHaveBeenCalledWith(runA.id);
  });

  it("ignores stream events that belong to another Run", async () => {
    const currentWorkspace = workspace("ws-1");
    const selectedThread = thread(currentWorkspace.id, "stream");
    const selectedTurn = turn(selectedThread.id, "stream");
    const running = run(currentWorkspace.id, selectedTurn.id, "stream", "RUNNING");
    let streamObserver: ((item: RunEvent) => void) | undefined;
    const poll = deferred<Run>();
    configureDefaults([currentWorkspace]);
    mockedApi.listThreads.mockResolvedValue([selectedThread]);
    mockedApi.createTurn.mockResolvedValue({ turn: selectedTurn, run: running });
    mockedApi.getRun.mockReturnValue(poll.promise);
    mockedStreamRunEvents.mockImplementation(async (_runId, _after, onEvent) => {
      streamObserver = onEvent;
      return () => undefined;
    });
    renderWorkbench();
    await screen.findByText("任务 stream");
    fireEvent.click(screen.getByText("任务 stream"));
    await waitFor(() => expect(mockedApi.listTurns).toHaveBeenCalledWith(selectedThread.id));
    fireEvent.change(screen.getByLabelText("向 Obsion 提问"), {
      target: { value: "检查流式归属" },
    });
    fireEvent.click(screen.getByLabelText("发送"));
    await waitFor(() => expect(mockedStreamRunEvents).toHaveBeenCalledWith(
      running.id,
      0,
      expect.any(Function),
    ));

    act(() => {
      streamObserver?.(event("run-elsewhere", "foreign"));
    });
    fireEvent.click(screen.getByRole("tab", { name: "轨迹" }));
    expect(screen.queryByText("run.completed.foreign")).toBeNull();
  });

  it("resumes stream and REST reconciliation when opening a Thread with an active Run", async () => {
    const currentWorkspace = workspace("ws-1");
    const selectedThread = thread(currentWorkspace.id, "resume-active");
    const selectedTurn = turn(selectedThread.id, "resume-active");
    const activeRun = run(currentWorkspace.id, selectedTurn.id, "resume-active", "RUNNING");
    const completedRun = { ...activeRun, status: "COMPLETED" as const, completed_at: NOW };
    const polling = deferred<Run>();
    configureDefaults([currentWorkspace]);
    mockedApi.listThreads.mockResolvedValue([selectedThread]);
    mockedApi.listTurns.mockResolvedValue([selectedTurn]);
    mockedApi.listThreadRuns.mockResolvedValue([activeRun]);
    mockedApi.getRun.mockReturnValue(polling.promise);
    renderWorkbench();
    await screen.findByText("任务 resume-active");

    fireEvent.click(screen.getByText("任务 resume-active"));
    await waitFor(() => expect(mockedStreamRunEvents).toHaveBeenCalledWith(
      activeRun.id,
      0,
      expect.any(Function),
    ));
    expect(mockedApi.getRun).toHaveBeenCalledWith(activeRun.id);
    expect(screen.getByLabelText("停止运行")).toBeDefined();

    await act(async () => {
      polling.resolve(completedRun);
      await polling.promise;
    });
    await waitFor(() => expect(screen.getByLabelText("发送")).toBeDefined());
  });

  it("rejects Thread history feedback owned by another Run atomically", async () => {
    const currentWorkspace = workspace("ws-1");
    const selectedThread = thread(currentWorkspace.id, "feedback-history");
    const selectedTurn = turn(selectedThread.id, "feedback-history");
    const completedRun = run(currentWorkspace.id, selectedTurn.id, "feedback-history");
    configureDefaults([currentWorkspace]);
    mockedApi.listThreads.mockResolvedValue([selectedThread]);
    mockedApi.listTurns.mockResolvedValue([selectedTurn]);
    mockedApi.listThreadRuns.mockResolvedValue([completedRun]);
    mockedApi.getRunFeedback.mockResolvedValue(feedback("run-elsewhere"));
    renderWorkbench();
    await screen.findByText("任务 feedback-history");

    fireEvent.click(screen.getByText("任务 feedback-history"));
    await screen.findByText("运行反馈归属与所选 Run 不一致");
    expect(screen.queryByText("问题 feedback-history")).toBeNull();
    expect(screen.getAllByText("任务 feedback-history").length).toBeGreaterThan(0);
  });

  it("allows only one submit while Thread and Run creation are in flight", async () => {
    const currentWorkspace = workspace("ws-1");
    const newThread = thread(currentWorkspace.id, "single-flight");
    const newTurn = turn(newThread.id, "single-flight");
    const newRun = run(currentWorkspace.id, newTurn.id, "single-flight", "RUNNING");
    const createdThread = deferred<Thread>();
    const createdRun = deferred<{ turn: Turn; run: Run }>();
    const poll = deferred<Run>();
    configureDefaults([currentWorkspace]);
    mockedApi.createThread.mockReturnValue(createdThread.promise);
    mockedApi.createTurn.mockReturnValue(createdRun.promise);
    mockedApi.getRun.mockReturnValue(poll.promise);
    renderWorkbench();
    await assertSelectedWorkspace(currentWorkspace.id);
    const composer = screen.getByLabelText("向 Obsion 提问");

    fireEvent.change(composer, { target: { value: "只应创建一个运行" } });
    fireEvent.click(screen.getByLabelText("发送"));
    await waitFor(() => expect(mockedApi.createThread).toHaveBeenCalledTimes(1));
    expect((screen.getByLabelText("发送") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("发送"));
    fireEvent.keyDown(composer, { key: "Enter", code: "Enter" });
    expect(mockedApi.createThread).toHaveBeenCalledTimes(1);
    expect(mockedApi.createTurn).not.toHaveBeenCalled();

    await act(async () => {
      createdThread.resolve(newThread);
      await createdThread.promise;
    });
    await waitFor(() => expect(mockedApi.createTurn).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByLabelText("发送"));
    fireEvent.keyDown(composer, { key: "Enter", code: "Enter" });
    expect(mockedApi.createThread).toHaveBeenCalledTimes(1);
    expect(mockedApi.createTurn).toHaveBeenCalledTimes(1);

    await act(async () => {
      createdRun.resolve({ turn: newTurn, run: newRun });
      await createdRun.promise;
    });
    expect(await screen.findByText("问题 single-flight")).toBeDefined();
    expect(mockedStreamRunEvents).toHaveBeenCalledWith(newRun.id, 0, expect.any(Function));
  });

  it("does not expose a server-created Thread until its first Run exists", async () => {
    const currentWorkspace = workspace("ws-1");
    const failedThread = thread(currentWorkspace.id, "failed-first-turn");
    configureDefaults([currentWorkspace]);
    mockedApi.createThread.mockResolvedValue(failedThread);
    mockedApi.createTurn.mockRejectedValue(new Error("首轮运行创建失败"));
    renderWorkbench();
    await assertSelectedWorkspace(currentWorkspace.id);

    fireEvent.change(screen.getByLabelText("向 Obsion 提问"), {
      target: { value: "保留输入但不选择空任务" },
    });
    fireEvent.click(screen.getByLabelText("发送"));

    expect(await screen.findByText("首轮运行创建失败")).toBeDefined();
    expect(screen.queryByText("任务 failed-first-turn")).toBeNull();
    expect(screen.getByDisplayValue("保留输入但不选择空任务")).toBeDefined();
    expect((screen.getByLabelText("发送") as HTMLButtonElement).disabled).toBe(false);
  });

  it("ignores a stale submit after switching Workspace", async () => {
    const workspaceA = workspace("ws-a");
    const workspaceB = workspace("ws-b");
    const createdThread = deferred<Thread>();
    configureDefaults([workspaceA, workspaceB]);
    mockedApi.listThreads.mockResolvedValue([]);
    mockedApi.createThread.mockReturnValue(createdThread.promise);
    renderWorkbench();
    await assertSelectedWorkspace(workspaceA.id);

    fireEvent.change(screen.getByLabelText("向 Obsion 提问"), {
      target: { value: "调查 A" },
    });
    fireEvent.click(screen.getByLabelText("发送"));
    await waitFor(() => expect(mockedApi.createThread).toHaveBeenCalledWith(workspaceA.id, "调查 A"));
    await selectWorkspace(workspaceB.id);
    await act(async () => {
      createdThread.resolve(thread(workspaceA.id, "created-A"));
      await createdThread.promise;
    });

    expect(mockedApi.createTurn).not.toHaveBeenCalled();
    expect(screen.queryByText("问题 created-A")).toBeNull();
    expect(screen.queryByDisplayValue("调查 A")).toBeNull();
    expect(screen.queryByText("任务创建失败")).toBeNull();
    await assertSelectedWorkspace(workspaceB.id);
  });

  it("ignores a stale cancel response after switching Workspace", async () => {
    const workspaceA = workspace("ws-a");
    const workspaceB = workspace("ws-b");
    const selectedThread = thread(workspaceA.id, "cancel");
    const selectedTurn = turn(selectedThread.id, "cancel");
    const activeRun = run(workspaceA.id, selectedTurn.id, "cancel", "RUNNING");
    const cancelled = deferred<Run>();
    const poll = deferred<Run>();
    configureDefaults([workspaceA, workspaceB]);
    mockedApi.listThreads.mockImplementation(async (workspaceId) =>
      workspaceId === workspaceA.id ? [selectedThread] : [],
    );
    mockedApi.listTurns.mockResolvedValue([]);
    mockedApi.listThreadRuns.mockResolvedValue([]);
    mockedApi.createTurn.mockResolvedValue({ turn: selectedTurn, run: activeRun });
    mockedApi.getRun.mockReturnValue(poll.promise);
    mockedApi.cancelRun.mockReturnValue(cancelled.promise);
    renderWorkbench();
    await screen.findByText("任务 cancel");
    fireEvent.click(screen.getByText("任务 cancel"));
    await waitFor(() => expect(mockedApi.listTurns).toHaveBeenCalledWith(selectedThread.id));
    fireEvent.change(screen.getByLabelText("向 Obsion 提问"), {
      target: { value: "启动待停止运行" },
    });
    fireEvent.click(screen.getByLabelText("发送"));
    await waitFor(() => expect(mockedStreamRunEvents).toHaveBeenCalledWith(
      activeRun.id,
      0,
      expect.any(Function),
    ));

    fireEvent.click(screen.getByLabelText("停止运行"));
    await waitFor(() => expect(mockedApi.cancelRun).toHaveBeenCalledWith(activeRun.id));
    await selectWorkspace(workspaceB.id);
    await act(async () => {
      cancelled.resolve({ ...activeRun, status: "CANCELLED" });
      await cancelled.promise;
    });

    expect(screen.queryByText("无法停止运行")).toBeNull();
    expect(screen.queryByText("问题 cancel")).toBeNull();
    await assertSelectedWorkspace(workspaceB.id);
  });

  it("ignores stale replay pending and errors after switching Workspace", async () => {
    const workspaceA = workspace("ws-a");
    const workspaceB = workspace("ws-b");
    const selectedThread = thread(workspaceA.id, "replay");
    const selectedTurn = turn(selectedThread.id, "replay");
    const completedRun = run(workspaceA.id, selectedTurn.id, "replay");
    const replayed = deferred<Run>();
    configureDefaults([workspaceA, workspaceB]);
    mockedApi.listThreads.mockImplementation(async (workspaceId) =>
      workspaceId === workspaceA.id ? [selectedThread] : [],
    );
    mockedApi.listTurns.mockResolvedValue([selectedTurn]);
    mockedApi.listThreadRuns.mockResolvedValue([completedRun]);
    mockedApi.getRunFeedback.mockResolvedValue(feedback(completedRun.id));
    mockedApi.replayRun.mockReturnValue(replayed.promise);
    renderWorkbench();
    await screen.findByText("任务 replay");
    fireEvent.click(screen.getByText("任务 replay"));
    await screen.findByText("问题 replay");

    fireEvent.click(screen.getAllByLabelText("回放此运行快照")[0]);
    await waitFor(() => expect(mockedApi.replayRun).toHaveBeenCalledWith(completedRun.id));
    await selectWorkspace(workspaceB.id);
    await act(async () => {
      replayed.reject(new Error("旧回放失败"));
      await replayed.promise.catch(() => undefined);
    });

    expect(screen.queryByText("旧回放失败")).toBeNull();
    expect(screen.queryByText("问题 replay")).toBeNull();
    await assertSelectedWorkspace(workspaceB.id);
  });

  it("ignores stale feedback pending and errors after switching Workspace", async () => {
    const workspaceA = workspace("ws-a");
    const workspaceB = workspace("ws-b");
    const selectedThread = thread(workspaceA.id, "feedback");
    const selectedTurn = turn(selectedThread.id, "feedback");
    const completedRun = run(workspaceA.id, selectedTurn.id, "feedback");
    const recorded = deferred<RunFeedback>();
    configureDefaults([workspaceA, workspaceB]);
    mockedApi.listThreads.mockImplementation(async (workspaceId) =>
      workspaceId === workspaceA.id ? [selectedThread] : [],
    );
    mockedApi.listTurns.mockResolvedValue([selectedTurn]);
    mockedApi.listThreadRuns.mockResolvedValue([completedRun]);
    mockedApi.recordRunFeedback.mockReturnValue(recorded.promise);
    renderWorkbench();
    await screen.findByText("任务 feedback");
    fireEvent.click(screen.getByText("任务 feedback"));
    await screen.findByText("问题 feedback");

    fireEvent.click(screen.getByLabelText("回答有帮助"));
    await waitFor(() => expect(mockedApi.recordRunFeedback).toHaveBeenCalledWith(
      completedRun.id,
      { rating: "HELPFUL", reason: "" },
    ));
    expect((screen.getByLabelText("回答有帮助") as HTMLButtonElement).disabled).toBe(true);
    await selectWorkspace(workspaceB.id);
    await act(async () => {
      recorded.reject(new Error("旧反馈失败"));
      await recorded.promise.catch(() => undefined);
    });

    expect(screen.queryByText("旧反馈失败")).toBeNull();
    expect(screen.queryByText("问题 feedback")).toBeNull();
    await assertSelectedWorkspace(workspaceB.id);
  });

  it("rejects feedback returned for another Run without committing it", async () => {
    const currentWorkspace = workspace("ws-1");
    const selectedThread = thread(currentWorkspace.id, "feedback-owner");
    const selectedTurn = turn(selectedThread.id, "feedback-owner");
    const completedRun = run(currentWorkspace.id, selectedTurn.id, "feedback-owner");
    configureDefaults([currentWorkspace]);
    mockedApi.listThreads.mockResolvedValue([selectedThread]);
    mockedApi.listTurns.mockResolvedValue([selectedTurn]);
    mockedApi.listThreadRuns.mockResolvedValue([completedRun]);
    mockedApi.recordRunFeedback.mockResolvedValue(feedback("run-elsewhere"));
    renderWorkbench();
    await screen.findByText("任务 feedback-owner");
    fireEvent.click(screen.getByText("任务 feedback-owner"));
    await screen.findByText("问题 feedback-owner");

    fireEvent.click(screen.getByLabelText("回答有帮助"));
    await screen.findByText("反馈响应与所选 Run 不一致");
    expect(screen.queryByText("已标记有帮助")).toBeNull();
    expect((screen.getByLabelText("回答有帮助") as HTMLButtonElement).disabled).toBe(false);
  });

  it("stops a stale multi-file upload and never attaches it to another Workspace", async () => {
    const workspaceA = workspace("ws-a");
    const workspaceB = workspace("ws-b");
    const firstUpload = deferred<Artifact>();
    configureDefaults([workspaceA, workspaceB]);
    mockedApi.listThreads.mockResolvedValue([]);
    mockedApi.uploadArtifact.mockReturnValue(firstUpload.promise);
    renderWorkbench();
    await assertSelectedWorkspace(workspaceA.id);
    const input = document.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();

    fireEvent.change(input!, {
      target: {
        files: [
          new File(["first"], "first.txt", { type: "text/plain" }),
          new File(["second"], "second.txt", { type: "text/plain" }),
        ],
      },
    });
    await waitFor(() => expect(mockedApi.uploadArtifact).toHaveBeenCalledTimes(1));
    await selectWorkspace(workspaceB.id);
    await act(async () => {
      firstUpload.resolve(artifact(workspaceA.id, null, "upload-A"));
      await firstUpload.promise;
    });

    expect(mockedApi.uploadArtifact).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("产物标题 upload-A")).toBeNull();
    expect(screen.queryByText("正在安全上传…")).toBeNull();
    await assertSelectedWorkspace(workspaceB.id);
  });
});
