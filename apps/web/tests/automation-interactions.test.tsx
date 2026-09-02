import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { AutomationView } from "@/components/automation-view";
import type {
  AutomationExecution,
  Workflow,
  WorkflowSchedule,
  WorkflowVersion,
  Workspace,
} from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      automation: {
        ...actual.api.automation,
        listWorkflows: vi.fn(),
        listNotifications: vi.fn(),
        listSchedules: vi.fn(),
        listExecutions: vi.fn(),
        listVersions: vi.fn(),
        getExecution: vi.fn(),
        publishVersion: vi.fn(),
        createVersion: vi.fn(),
        setStatus: vi.fn(),
        trigger: vi.fn(),
        createSchedule: vi.fn(),
        setScheduleEnabled: vi.fn(),
      },
    },
  };
});

const automation = vi.mocked(api.automation);

afterEach(() => {
  cleanup();
});

function workspace(): Workspace {
  return {
    id: "ws-1",
    name: "支付平台",
    description: "",
    classification: "INTERNAL",
    visibility: "PRIVATE",
    updated_at: "2026-08-20T08:00:00Z",
  };
}

function workflow(partial: Partial<Workflow> = {}): Workflow {
  return {
    id: "wf-1",
    workspace_id: "ws-1",
    name: "payments-weekly",
    display_name: "支付周报",
    description: "每周一汇总支付成功率",
    status: "ACTIVE",
    owner_id: "owner-1",
    active_version: 2,
    concurrency_policy: "FORBID",
    max_concurrency: 1,
    timeout_seconds: 1800,
    notify_on_success: true,
    notify_on_failure: true,
    classification: "INTERNAL",
    created_at: "2026-08-20T08:00:00Z",
    updated_at: "2026-08-20T08:00:00Z",
    ...partial,
  };
}

function version(partial: Partial<WorkflowVersion> = {}): WorkflowVersion {
  return {
    id: "wfv-2",
    workflow_id: "wf-1",
    version: 2,
    spec: {
      steps: [
        { id: "analyze", name: "分析", type: "ANALYSIS", depends_on: [], prompt: "汇总支付成功率" },
        {
          id: "notify",
          name: "通知",
          type: "NOTIFICATION",
          depends_on: ["analyze"],
          title: "支付周报 已完成",
          body: "周期分析已完成",
        },
      ],
    },
    checksum_sha256: "a".repeat(64),
    created_by: "owner-1",
    published_at: "2026-08-21T08:00:00Z",
    created_at: "2026-08-21T08:00:00Z",
    ...partial,
  };
}

function schedule(partial: Partial<WorkflowSchedule> = {}): WorkflowSchedule {
  return {
    id: "sch-1",
    workflow_id: "wf-1",
    workflow_version_id: "wfv-2",
    name: "每周晨报",
    cron_expression: "0 9 * * 1",
    timezone: "Asia/Shanghai",
    misfire_policy: "FIRE_ONCE",
    enabled: true,
    next_fire_at: "2026-09-07T01:00:00Z",
    last_fire_at: null,
    last_error_code: null,
    ...partial,
  };
}

function execution(partial: Partial<AutomationExecution> = {}): AutomationExecution {
  return {
    id: "exec-1",
    workflow_id: "wf-1",
    workflow_version_id: "wfv-2",
    schedule_id: null,
    trigger: "MANUAL",
    scheduled_for: null,
    status: "RUNNING",
    owner_id: "owner-1",
    input_payload: {},
    deadline_at: "2026-09-01T09:00:00Z",
    started_at: "2026-09-01T08:30:00Z",
    completed_at: null,
    error_code: null,
    error_message: null,
    summary: {},
    created_at: "2026-09-01T08:30:00Z",
    updated_at: "2026-09-01T08:30:00Z",
    steps: [],
    ...partial,
  };
}

function mountView(item: Workflow = workflow()) {
  automation.listWorkflows.mockResolvedValue([item]);
  automation.listNotifications.mockResolvedValue([]);
  automation.listSchedules.mockResolvedValue([schedule()]);
  automation.listExecutions.mockResolvedValue([execution()]);
  automation.listVersions.mockResolvedValue([
    version(),
    version({ id: "wfv-1", version: 1, published_at: "2026-08-20T08:00:00Z" }),
  ]);
  automation.getExecution.mockResolvedValue(execution());
  render(<AutomationView workspace={workspace()} />);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AutomationView interactions", () => {
  it("loads the overview and the selected workflow detail", async () => {
    mountView();
    await screen.findByText("每周晨报");
    expect(automation.listWorkflows).toHaveBeenCalledWith("ws-1");
    expect(automation.listSchedules).toHaveBeenCalledWith("wf-1");
    expect(automation.listExecutions).toHaveBeenCalledWith("wf-1");
    expect(automation.listVersions).toHaveBeenCalledWith("wf-1");
    expect(screen.getByText("v2")).toBeDefined();
  });

  it("publishes an older version from the versions card", async () => {
    automation.publishVersion.mockResolvedValue({
      workflow: workflow({ active_version: 1 }),
      version: version({ version: 1 }),
    });
    mountView();
    await screen.findByText("每周晨报");
    fireEvent.click(screen.getByLabelText("发布版本 v1"));
    await waitFor(() => expect(automation.publishVersion).toHaveBeenCalledWith("wf-1", 1));
    await screen.findByText("版本 v1 已发布为当前版本");
  });

  it("triggers a manual run with a validated JSON payload", async () => {
    automation.trigger.mockResolvedValue(execution());
    mountView();
    await screen.findByText("每周晨报");
    fireEvent.click(screen.getByRole("button", { name: /立即运行/ }));
    fireEvent.change(screen.getByLabelText(/运行参数/), {
      target: { value: '{"day": "2026-09-01"}' },
    });
    fireEvent.click(screen.getByRole("button", { name: "启动运行" }));
    await waitFor(() =>
      expect(automation.trigger).toHaveBeenCalledWith("wf-1", { day: "2026-09-01" }),
    );
    // The drawer opens from the created execution.
    await screen.findByLabelText("自动化运行详情");
  });

  it("rejects malformed trigger payloads without calling the API", async () => {
    mountView();
    await screen.findByText("每周晨报");
    fireEvent.click(screen.getByRole("button", { name: /立即运行/ }));
    fireEvent.change(screen.getByLabelText(/运行参数/), { target: { value: "{invalid" } });
    fireEvent.click(screen.getByRole("button", { name: "启动运行" }));
    await screen.findByText("运行参数不是合法的 JSON，请检查逗号、引号与括号。");
    expect(automation.trigger).not.toHaveBeenCalled();
  });

  it("creates a schedule from a preset without a version pin", async () => {
    automation.createSchedule.mockResolvedValue(schedule({ id: "sch-2", name: "每日晨报" }));
    mountView();
    await screen.findByText("每周晨报");
    fireEvent.click(screen.getByLabelText("添加运行计划"));
    await screen.findByText("添加运行计划");
    fireEvent.change(screen.getByLabelText(/计划名称/), { target: { value: "每日晨报" } });
    fireEvent.click(screen.getByRole("button", { name: "创建计划" }));
    await waitFor(() => expect(automation.createSchedule).toHaveBeenCalledTimes(1));
    const [workflowId, payload] = automation.createSchedule.mock.calls[0];
    expect(workflowId).toBe("wf-1");
    expect(payload).toMatchObject({
      name: "每日晨报",
      cron_expression: "0 9 * * *",
      misfire_policy: "FIRE_ONCE",
      misfire_grace_seconds: 300,
      input_payload: {},
      enabled: true,
    });
    expect(payload).not.toHaveProperty("workflow_version");
    await screen.findByText("计划「每日晨报」已创建");
  });

  it("requires the two-step confirm before retiring a paused workflow", async () => {
    automation.setStatus.mockResolvedValue(workflow({ status: "RETIRED" }));
    mountView(workflow({ status: "PAUSED" }));
    await screen.findByText("每周晨报");
    fireEvent.click(screen.getByRole("button", { name: /退役/ }));
    expect(automation.setStatus).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /确认退役/ }));
    await waitFor(() => expect(automation.setStatus).toHaveBeenCalledWith("wf-1", "retire"));
  });

  it("derives a new immutable version from an existing one", async () => {
    automation.createVersion.mockResolvedValue(version({ id: "wfv-3", version: 3 }));
    mountView();
    await screen.findByText("每周晨报");
    fireEvent.click(screen.getByLabelText("基于版本 v2 新建版本"));
    await screen.findByText("基于 v2 新建版本");
    // The draft is prefilled from the base version's spec.
    expect(screen.getByDisplayValue("汇总支付成功率")).toBeDefined();
    fireEvent.click(screen.getByRole("button", { name: "保存为新版本" }));
    await waitFor(() => expect(automation.createVersion).toHaveBeenCalledTimes(1));
    const [workflowId, spec] = automation.createVersion.mock.calls[0];
    expect(workflowId).toBe("wf-1");
    const steps = (spec as { steps: Array<{ type: string }> }).steps;
    expect(steps.map((step) => step.type)).toEqual(["ANALYSIS", "NOTIFICATION"]);
    await screen.findByText("版本 v3 已创建，可在版本列表中发布");
  });
});
