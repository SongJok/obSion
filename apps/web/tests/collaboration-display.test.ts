import { describe, expect, it } from "vitest";

import {
  MAX_SOURCE_RUN_OPTIONS,
  buildSourceRunOptions,
  memberDisplayName,
  sourceRunLabel,
  sourceRunThreadId,
  taskCreatePayload,
  taskUpdateHasChanges,
  taskUpdatePayload,
  toDateTimeLocalValue,
  type TaskDraft,
} from "@/lib/collaboration-display";
import type { Run, Thread, WorkspaceMember, WorkspaceTask } from "@/lib/types";

function member(partial: Partial<WorkspaceMember> = {}): WorkspaceMember {
  return {
    workspace_id: "ws-1",
    user_id: "user-1",
    display_name: "王晓",
    email: "wangxiao@example.com",
    permissions: ["read", "write"],
    created_by: "owner-1",
    created_at: "2026-08-01T08:00:00Z",
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
    created_at: "2026-08-01T08:00:00Z",
    updated_at: "2026-08-01T08:00:00Z",
    archived_at: null,
    ...partial,
  };
}

function run(partial: Partial<Run> = {}): Run {
  return {
    id: "run-1",
    turn_id: "turn-1",
    status: "COMPLETED",
    agent_version_id: null,
    model_profile_id: null,
    intent: {},
    plan: {},
    step_count: 3,
    max_input_tokens: 0,
    max_output_tokens: 0,
    max_cost_amount: "0",
    input_tokens: 0,
    output_tokens: 0,
    cost_amount: "0",
    error_code: null,
    error_message: null,
    replay_of_run_id: null,
    created_at: "2026-08-20T10:00:00Z",
    ...partial,
  } as Run;
}

function task(partial: Partial<WorkspaceTask> = {}): WorkspaceTask {
  return {
    id: "task-1",
    workspace_id: "ws-1",
    title: "验证支付超时的客户影响",
    description: "确认影响面",
    status: "OPEN",
    priority: "NORMAL",
    assignee_id: null,
    created_by: "owner-1",
    source_run_id: null,
    due_at: null,
    completed_at: null,
    version: 3,
    created_at: "2026-08-10T08:00:00Z",
    updated_at: "2026-08-10T08:00:00Z",
    ...partial,
  };
}

function draft(partial: Partial<TaskDraft> = {}): TaskDraft {
  return {
    title: "验证支付超时的客户影响",
    description: "确认影响面",
    priority: "NORMAL",
    assigneeId: "",
    dueAt: "",
    ...partial,
  };
}

describe("memberDisplayName", () => {
  it("resolves the display name of a workspace member", () => {
    expect(memberDisplayName([member()], "user-1")).toBe("王晓");
  });

  it("falls back to a truncated id for members outside the loaded list", () => {
    expect(memberDisplayName([], "abcdef12-0000-0000")).toBe("成员 abcdef12");
    expect(memberDisplayName([member()], null)).toBe("");
  });
});

describe("buildSourceRunOptions", () => {
  it("labels runs with their thread title and sorts newest first", () => {
    const options = buildSourceRunOptions(
      [thread()],
      [
        {
          threadId: "thread-1",
          runs: [
            run({ id: "aaaa1111-old", created_at: "2026-08-19T10:00:00Z" }),
            run({ id: "bbbb2222-new", created_at: "2026-08-21T10:00:00Z" }),
          ],
        },
      ],
    );
    expect(options.map((option) => option.runId)).toEqual(["bbbb2222-new", "aaaa1111-old"]);
    expect(options[0]?.label).toBe("支付超时调查 · Run bbbb2222");
  });

  it("caps the options so the selector never grows with history", () => {
    const runs = Array.from({ length: MAX_SOURCE_RUN_OPTIONS + 10 }, (_, index) =>
      run({ id: `run-${String(index).padStart(3, "0")}`, created_at: `2026-08-${String((index % 28) + 1).padStart(2, "0")}T10:00:00Z` }),
    );
    const options = buildSourceRunOptions([thread()], [{ threadId: "thread-1", runs }]);
    expect(options).toHaveLength(MAX_SOURCE_RUN_OPTIONS);
  });

  it("labels runs from threads missing from the loaded list", () => {
    const options = buildSourceRunOptions([], [{ threadId: "deadbeef-thread", runs: [run()] }]);
    expect(options[0]?.label).toBe("任务 deadbeef · Run run-1");
  });
});

describe("sourceRunLabel", () => {
  it("falls back to a truncated id when the persisted run left the option window", () => {
    expect(sourceRunLabel([], "cafe0001-run")).toBe("Run cafe0001");
    expect(sourceRunLabel([], null)).toBe("");
  });
});

describe("sourceRunThreadId", () => {
  it("resolves the owning Thread for a loaded source Run", () => {
    const options = buildSourceRunOptions([thread()], [{ threadId: "thread-1", runs: [run()] }]);
    expect(sourceRunThreadId(options, "run-1")).toBe("thread-1");
    expect(sourceRunThreadId(options, "unknown-run")).toBeUndefined();
  });
});

describe("toDateTimeLocalValue", () => {
  it("converts persisted ISO timestamps to datetime-local values", () => {
    const value = toDateTimeLocalValue("2026-08-20T10:30:00Z");
    expect(value).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/);
    expect(new Date(value).toISOString()).toBe("2026-08-20T10:30:00.000Z");
  });

  it("returns empty for missing or invalid timestamps", () => {
    expect(toDateTimeLocalValue(null)).toBe("");
    expect(toDateTimeLocalValue("not-a-date")).toBe("");
  });
});

describe("taskCreatePayload", () => {
  it("omits optional fields that were left blank", () => {
    expect(taskCreatePayload(draft(), "")).toEqual({
      title: "验证支付超时的客户影响",
      description: "确认影响面",
      priority: "NORMAL",
    });
  });

  it("includes assignee, source run, and due date when chosen", () => {
    const payload = taskCreatePayload(
      draft({ assigneeId: "user-1", dueAt: "2026-09-10T09:30" }),
      "run-1",
    );
    expect(payload.assignee_id).toBe("user-1");
    expect(payload.source_run_id).toBe("run-1");
    expect(typeof payload.due_at).toBe("string");
    expect(new Date(payload.due_at as string).toISOString()).toBe(
      new Date("2026-09-10T09:30").toISOString(),
    );
  });
});

describe("taskUpdatePayload", () => {
  it("carries only the expected version when nothing changed", () => {
    const existing = task();
    const payload = taskUpdatePayload(existing, draft());
    expect(payload).toEqual({ expected_version: 3 });
    expect(taskUpdateHasChanges(payload)).toBe(false);
  });

  it("sends an explicit null to clear the assignee", () => {
    const existing = task({ assignee_id: "user-1" });
    const payload = taskUpdatePayload(existing, draft({ assigneeId: "" }));
    expect(payload).toEqual({ expected_version: 3, assignee_id: null });
    expect(taskUpdateHasChanges(payload)).toBe(true);
  });

  it("sends only changed fields alongside the expected version", () => {
    const existing = task();
    const payload = taskUpdatePayload(
      existing,
      draft({ priority: "HIGH", assigneeId: "user-2" }),
    );
    expect(payload).toEqual({ expected_version: 3, priority: "HIGH", assignee_id: "user-2" });
  });

  it("sends an explicit null to clear the due date", () => {
    const existing = task({ due_at: "2026-09-01T00:00:00.000Z" });
    const payload = taskUpdatePayload(existing, draft({ dueAt: "" }));
    expect(payload.due_at).toBeNull();
  });

  it("treats a re-rendered equal due date as unchanged", () => {
    const dueAt = toDateTimeLocalValue("2026-09-01T00:00:00.000Z");
    const existing = task({ due_at: "2026-09-01T00:00:00.000Z" });
    const payload = taskUpdatePayload(existing, draft({ dueAt }));
    expect(payload).toEqual({ expected_version: 3 });
  });
});
