import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AdminView } from "@/components/admin-view";
import { api } from "@/lib/api";
import type { ImBinding, RuntimeSlo } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      admin: {
        users: vi.fn(),
        roles: vi.fn(),
        departments: vi.fn(),
        connectors: vi.fn(),
        probeConnectorHealth: vi.fn(),
        discoverConnector: vi.fn(),
        scanConnectorPlugin: vi.fn(),
        promoteConnectorPlugin: vi.fn(),
        capabilities: vi.fn(),
        modelProfiles: vi.fn(),
        agents: vi.fn(),
        skills: vi.fn(),
        dataSources: vi.fn(),
        dataCatalog: vi.fn(),
        policies: vi.fn(),
        approvals: vi.fn(),
        evaluations: vi.fn(),
        costs: vi.fn(),
        feedbackSummary: vi.fn(),
        runtimeSlo: vi.fn(),
        prompts: vi.fn(),
        knowledge: vi.fn(),
        secrets: vi.fn(),
        audit: vi.fn(),
        operatorInvocations: vi.fn(),
        imBindings: vi.fn(),
        createImBinding: vi.fn(),
        revokeImBinding: vi.fn(),
      },
    },
  };
});

const admin = vi.mocked(api.admin);

afterEach(() => {
  cleanup();
});

function slo(): RuntimeSlo {
  return {
    source: "postgresql",
    runs: { terminal: 10, completed: 9, failed: 1, cancelled: 0, success_rate: 0.9 },
    latency: {
      average_ms: 420,
      count: 10,
      ttft: { available: false, metric: "obsion.run.ttft", reason: "histogram-only" },
      model: { average_ms: 180, count: 10 },
      tool: { average_ms: 120, count: 8, source: "capability-steps" },
    },
    steps: { average: 3.2, count: 10 },
    tokens: { input: 1200, output: 600 },
    cost: { amount: "1.25" },
    replans: { events: 1, rate: 0.1 },
    approvals: { requested: 2, approved: 1, rejected: 0, pending: 1, approval_rate: 0.5 },
    satisfaction: { total: 4, helpful: 3, needs_improvement: 1, helpful_rate: 0.75 },
    evidence_coverage: { average: 0.92, count: 9 },
  };
}

function binding(partial: Partial<ImBinding> = {}): ImBinding {
  return {
    id: "binding-1",
    channel: "feishu",
    sender_id: "ou_test_sender",
    user_id: "user-1",
    active: true,
    created_by: "admin-1",
    created_at: "2026-09-01T08:00:00Z",
    updated_at: "2026-09-01T08:00:00Z",
    revoked_at: null,
    ...partial,
  };
}

function connector() {
  return {
    id: "connector-1",
    name: "knowledge-sdk",
    type: "SDK",
    environment: "DEVELOPMENT",
    status: "ACTIVE",
    spi: true,
    health: { status: "ready" },
    plugin: { status: "passed", lifecycle: "SCANNED" },
  };
}

function configureAdminLoads() {
  admin.users.mockResolvedValue([
    { id: "user-1", display_name: "王晓", email: "wangxiao@example.com", active: true },
  ]);
  admin.roles.mockResolvedValue([{ id: "role-1", name: "admin" }]);
  admin.departments.mockResolvedValue([{ id: "department-1", name: "支付平台" }]);
  admin.connectors.mockResolvedValue([connector()]);
  admin.capabilities.mockResolvedValue([{ id: "capability-1", name: "knowledge.search" }]);
  admin.modelProfiles.mockResolvedValue([{ id: "profile-1", name: "reasoning-high" }]);
  admin.agents.mockResolvedValue([{ id: "agent-1", name: "general-agent" }]);
  admin.skills.mockResolvedValue([{ id: "skill-1", name: "knowledge-research" }]);
  admin.dataSources.mockResolvedValue([{ id: "source-1", name: "analytics-replica" }]);
  admin.dataCatalog.mockResolvedValue({ metrics: 2, dimensions: 1 });
  admin.policies.mockResolvedValue([{ id: "policy-1", effect: "DENY" }]);
  admin.approvals.mockResolvedValue([{ id: "approval-1", status: "PENDING" }]);
  admin.evaluations.mockResolvedValue([]);
  admin.costs.mockResolvedValue([{ cost_amount: "1.25" }]);
  admin.feedbackSummary.mockResolvedValue({
    total: 4,
    helpful: 3,
    needs_improvement: 1,
    helpful_rate: 0.75,
  });
  admin.runtimeSlo.mockResolvedValue(slo());
  admin.prompts.mockResolvedValue([{ id: "prompt-1", name: "general" }]);
  admin.knowledge.mockResolvedValue([{ id: "document-1", title: "支付 SOP" }]);
  admin.secrets.mockResolvedValue([{ id: "secret-1", name: "feishu-app", value: undefined }]);
  admin.audit.mockResolvedValue([]);
  admin.operatorInvocations.mockResolvedValue([]);
  admin.imBindings.mockResolvedValue([]);
  admin.probeConnectorHealth.mockResolvedValue({ id: "connector-1", status: "ready" });
  admin.discoverConnector.mockResolvedValue({
    id: "connector-1",
    discovery: { operations: [{ capability: "knowledge.search" }] },
  });
  admin.scanConnectorPlugin.mockResolvedValue({ id: "connector-1", status: "passed" });
  admin.promoteConnectorPlugin.mockResolvedValue({ id: "connector-1", status: "ACTIVE" });
}

beforeEach(() => {
  vi.clearAllMocks();
  configureAdminLoads();
});

describe("AdminView interactions", () => {
  it("loads governance projections and preserves partial-domain degradation", async () => {
    admin.secrets.mockRejectedValue(new Error("secret metadata unavailable"));
    render(<AdminView />);

    await screen.findByText(/部分治理域暂时不可用：Secrets/);
    expect(screen.getByText("90%")).toBeDefined();
    expect(screen.getByText("0.92")).toBeDefined();
    expect(screen.getByText("3")).toBeDefined();
    expect(admin.users).toHaveBeenCalledTimes(1);
    expect(admin.runtimeSlo).toHaveBeenCalledTimes(1);
  });

  it("creates an IM principal binding with the stable sender id and refreshes", async () => {
    admin.createImBinding.mockResolvedValue(binding());
    admin.imBindings.mockResolvedValueOnce([]).mockResolvedValue([binding()]);
    render(<AdminView />);
    await screen.findByText("尚未绑定 IM 发送者");

    fireEvent.change(screen.getByLabelText("通道"), { target: { value: "feishu" } });
    fireEvent.change(screen.getByLabelText("sender_id"), {
      target: { value: "  ou_test_sender  " },
    });
    fireEvent.change(screen.getByLabelText("用户"), { target: { value: "user-1" } });
    fireEvent.click(screen.getByRole("button", { name: "绑定" }));

    await waitFor(() =>
      expect(admin.createImBinding).toHaveBeenCalledWith({
        channel: "feishu",
        sender_id: "ou_test_sender",
        user_id: "user-1",
      }),
    );
    await screen.findByText("feishu:ou_test_sender");
    expect(admin.imBindings).toHaveBeenCalledTimes(2);
  });

  it("revokes an active IM binding and removes it from the refreshed projection", async () => {
    admin.imBindings.mockResolvedValueOnce([binding()]).mockResolvedValue([]);
    admin.revokeImBinding.mockResolvedValue(
      binding({ active: false, revoked_at: "2026-09-01T08:10:00Z" }),
    );
    render(<AdminView />);
    await screen.findByText("feishu:ou_test_sender");

    fireEvent.click(screen.getByRole("button", { name: "撤销" }));

    await waitFor(() => expect(admin.revokeImBinding).toHaveBeenCalledWith("binding-1"));
    await screen.findByText("尚未绑定 IM 发送者");
  });

  it("shows connector discovery without auto-binding a capability", async () => {
    render(<AdminView />);
    await screen.findByText("knowledge-sdk");
    fireEvent.click(screen.getByRole("button", { name: "发现" }));

    await screen.findByText(/knowledge\.search/);
    expect(screen.getByText(/发现不自动绑定/)).toBeDefined();
    expect(admin.discoverConnector).toHaveBeenCalledWith("connector-1");
    expect(admin.connectors).toHaveBeenCalledTimes(1);
  });

  it("runs connector health, scan, and promotion operations with a refresh after each", async () => {
    render(<AdminView />);
    await screen.findByText("knowledge-sdk");

    fireEvent.click(screen.getByRole("button", { name: "探测" }));
    await waitFor(() => expect(admin.connectors).toHaveBeenCalledTimes(2));
    expect(admin.probeConnectorHealth).toHaveBeenCalledWith("connector-1");

    fireEvent.click(screen.getByRole("button", { name: "扫描" }));
    await waitFor(() => expect(admin.connectors).toHaveBeenCalledTimes(3));
    expect(admin.scanConnectorPlugin).toHaveBeenCalledWith("connector-1");

    fireEvent.click(screen.getByRole("button", { name: "晋升" }));
    await waitFor(() => expect(admin.connectors).toHaveBeenCalledTimes(4));
    expect(admin.promoteConnectorPlugin).toHaveBeenCalledWith("connector-1");
  });
});
