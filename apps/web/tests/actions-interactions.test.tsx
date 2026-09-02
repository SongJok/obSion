import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ActionsView } from "@/components/actions-view";
import { api } from "@/lib/api";
import type {
  ActionApproval,
  ActionDetail,
  ActionRequest,
  ActionStatus,
  Workspace,
} from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      actions: {
        list: vi.fn(),
        create: vi.fn(),
        get: vi.fn(),
        preflight: vi.fn(),
        approvals: vi.fn(),
        decide: vi.fn(),
        rollback: vi.fn(),
        cancel: vi.fn(),
      },
    },
  };
});

const actions = vi.mocked(api.actions);

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
    updated_at: "2026-09-01T08:00:00Z",
  };
}

function action(status: ActionStatus = "DRAFT"): ActionRequest {
  return {
    id: "action-1",
    workspace_id: "ws-1",
    action_type: "GENERATE_PR",
    title: "提交支付超时修复",
    description: "修复支付渠道超时",
    environment: "development",
    target: { repository: "obsion/payments" },
    parameters: { title: "fix payment timeout", head: "fix/payment-timeout", base: "main" },
    rollback_parameters: { reason: "close governed PR" },
    status,
    owner_id: "owner-1",
    requested_by: "owner-1",
    idempotency_key: "action-key-1",
    timeout_seconds: 1800,
    deadline_at: null,
    plan_checksum_sha256: status === "DRAFT" ? null : "a".repeat(64),
    preflight: {},
    result: {},
    started_at: null,
    completed_at: status === "COMPLETED" ? "2026-09-01T08:10:00Z" : null,
    error_code: null,
    error_message: null,
    created_at: "2026-09-01T08:00:00Z",
    updated_at: "2026-09-01T08:00:00Z",
  };
}

function approval(
  status: ActionApproval["status"] = "PENDING",
  purpose: ActionApproval["purpose"] = "EXECUTE",
): ActionApproval {
  return {
    id: purpose === "EXECUTE" ? "approval-execute-1" : "approval-rollback-1",
    action_request_id: "action-1",
    purpose,
    revision: 1,
    plan_checksum_sha256: "a".repeat(64),
    status,
    reason: purpose === "EXECUTE" ? "预检已通过，申请执行" : "验收完成后申请回滚",
    requested_by: "owner-1",
    decided_by: status === "PENDING" ? null : "approver-1",
    decision_reason: status === "APPROVED" ? "已独立复核" : null,
    expires_at: "2026-09-01T09:00:00Z",
    decided_at: status === "PENDING" ? null : "2026-09-01T08:05:00Z",
    created_at: "2026-09-01T08:00:00Z",
  };
}

function detail(
  status: ActionStatus = "DRAFT",
  approvals: ActionApproval[] = [],
): ActionDetail {
  return {
    action: action(status),
    plan: status === "DRAFT" ? null : {
      id: "plan-1",
      spec: {
        execute: { capability_name: "action.pr.create" },
        rollback: { capability_name: "action.pr.close" },
      },
      checksum_sha256: "a".repeat(64),
      created_at: "2026-09-01T08:00:00Z",
    },
    approvals,
    attempts: [],
  };
}

function configureActionLoads(item: ActionRequest = action(), itemDetail: ActionDetail = detail()) {
  actions.list.mockResolvedValue([item]);
  actions.approvals.mockResolvedValue(itemDetail.approvals);
  actions.get.mockResolvedValue(itemDetail);
  actions.create.mockResolvedValue(item);
  actions.preflight.mockResolvedValue(itemDetail);
  actions.rollback.mockResolvedValue(item);
  actions.cancel.mockResolvedValue(item);
}

beforeEach(() => {
  vi.clearAllMocks();
  configureActionLoads();
});

describe("ActionsView interactions", () => {
  it("creates a development PR draft without exposing a production option", async () => {
    actions.list.mockResolvedValue([]);
    actions.approvals.mockResolvedValue([]);
    actions.create.mockResolvedValue(action());
    actions.get.mockResolvedValue(detail());
    render(<ActionsView workspace={workspace()} />);
    await screen.findByText("发起第一个受控动作");

    fireEvent.click(screen.getAllByRole("button", { name: /发起受控动作/ })[0]);
    const createDialog = screen.getByRole("dialog", { name: "发起受控动作" });
    expect(within(createDialog).queryByRole("option", { name: "生产环境" })).toBeNull();
    fireEvent.change(screen.getByLabelText("动作名称"), {
      target: { value: "提交支付超时修复" },
    });
    fireEvent.change(screen.getByLabelText("代码仓库"), {
      target: { value: "obsion/payments" },
    });
    fireEvent.change(screen.getByLabelText("PR 标题"), {
      target: { value: "fix payment timeout" },
    });
    fireEvent.change(screen.getByLabelText("来源分支"), {
      target: { value: "fix/payment-timeout" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存动作草稿" }));

    await waitFor(() => expect(actions.create).toHaveBeenCalledTimes(1));
    const [workspaceId, payload] = actions.create.mock.calls[0];
    expect(workspaceId).toBe("ws-1");
    expect(payload).toMatchObject({
      action_type: "GENERATE_PR",
      environment: "development",
      target: { repository: "obsion/payments" },
      parameters: { title: "fix payment timeout", head: "fix/payment-timeout", base: "main" },
    });
    expect(String(payload.idempotency_key)).toMatch(/^web-action-/);
  });

  it("requires an operator declaration before preflight and preserves it", async () => {
    const checked = detail("WAITING_APPROVAL", [approval()]);
    actions.preflight.mockResolvedValue(checked);
    render(<ActionsView workspace={workspace()} />);
    await screen.findByText("提交支付超时修复");
    await screen.findByText("等待预检");
    fireEvent.click(screen.getByRole("button", { name: /预检并提交/ }));

    const preflightDialog = screen.getByRole("dialog", { name: "预检并提交审批" });
    const submit = within(preflightDialog).getByRole("button", { name: "预检并提交" });
    fireEvent.change(screen.getByLabelText("核对声明"), { target: { value: "太短" } });
    expect((submit as HTMLButtonElement).disabled).toBe(true);
    const reason = "已核对目标仓库、分支、影响范围和固定回滚能力";
    fireEvent.change(screen.getByLabelText("核对声明"), { target: { value: reason } });
    fireEvent.click(submit);

    await waitFor(() => expect(actions.preflight).toHaveBeenCalledWith("action-1", reason));
    await screen.findByText("等待执行审批");
  });

  it("submits an independent approval decision with the reviewer reason", async () => {
    const pending = approval();
    const waiting = detail("WAITING_APPROVAL", [pending]);
    configureActionLoads(waiting.action, waiting);
    actions.get
      .mockResolvedValueOnce(waiting)
      .mockResolvedValue(detail("APPROVED", [approval("APPROVED")]));
    actions.decide.mockResolvedValue(approval("APPROVED"));
    render(<ActionsView workspace={workspace()} />);
    fireEvent.click(await screen.findByRole("button", { name: "进行审批" }));

    expect(screen.getByRole("dialog", { name: "审批动作执行" })).toBeDefined();
    const reason = "已复核目标和回滚条件";
    fireEvent.change(screen.getByLabelText("审批意见"), { target: { value: reason } });
    fireEvent.click(screen.getByRole("button", { name: "批准" }));

    await waitFor(() =>
      expect(actions.decide).toHaveBeenCalledWith("approval-execute-1", true, reason),
    );
  });

  it("requests rollback with a bounded human reason", async () => {
    const completed = detail("COMPLETED", [approval("APPROVED")]);
    configureActionLoads(completed.action, completed);
    actions.rollback.mockResolvedValue(action("WAITING_ROLLBACK_APPROVAL"));
    actions.get
      .mockResolvedValueOnce(completed)
      .mockResolvedValue(detail("WAITING_ROLLBACK_APPROVAL", [approval("PENDING", "ROLLBACK")]));
    render(<ActionsView workspace={workspace()} />);
    fireEvent.click(await screen.findByRole("button", { name: "申请回滚" }));

    expect(screen.getByRole("dialog", { name: "申请回滚" })).toBeDefined();
    const reason = "验收完成，确认关闭测试拉取请求";
    fireEvent.change(screen.getByLabelText("回滚原因"), { target: { value: reason } });
    fireEvent.click(screen.getByRole("button", { name: "提交回滚审批" }));

    await waitFor(() => expect(actions.rollback).toHaveBeenCalledWith("action-1", reason));
  });

  it("cancels only through the governed action endpoint", async () => {
    render(<ActionsView workspace={workspace()} />);
    await screen.findByText("等待预检");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    await waitFor(() => expect(actions.cancel).toHaveBeenCalledWith("action-1"));
    expect(actions.get).toHaveBeenCalledTimes(2);
    expect(actions.list).toHaveBeenCalledTimes(2);
  });
});
