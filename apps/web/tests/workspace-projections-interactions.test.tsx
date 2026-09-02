import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DashboardsView } from "@/components/dashboards-view";
import { EvidenceView } from "@/components/evidence-view";
import { ReportsView } from "@/components/reports-view";
import { SqlView } from "@/components/sql-view";
import { TimelineView } from "@/components/timeline-view";
import { api } from "@/lib/api";
import type { Artifact, Evidence, RunEvent, Workspace } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listWorkspaceReports: vi.fn(),
      listWorkspaceDashboards: vi.fn(),
      listWorkspaceSql: vi.fn(),
      listWorkspaceEvidence: vi.fn(),
      listWorkspaceTimeline: vi.fn(),
      getArtifact: vi.fn(),
    },
  };
});

const listWorkspaceReports = vi.mocked(api.listWorkspaceReports);
const listWorkspaceDashboards = vi.mocked(api.listWorkspaceDashboards);
const listWorkspaceSql = vi.mocked(api.listWorkspaceSql);
const listWorkspaceEvidence = vi.mocked(api.listWorkspaceEvidence);
const listWorkspaceTimeline = vi.mocked(api.listWorkspaceTimeline);
const getArtifact = vi.mocked(api.getArtifact);

afterEach(() => {
  cleanup();
});

function workspace(): Workspace {
  return {
    id: "ws-1",
    name: "支付平台",
    description: "",
    classification: "CONFIDENTIAL",
    visibility: "PRIVATE",
    updated_at: "2026-09-01T08:00:00Z",
  };
}

function artifact(partial: Partial<Artifact> = {}): Artifact {
  return {
    id: "report-1",
    workspace_id: "ws-1",
    run_id: "run-1",
    kind: "REPORT",
    title: "支付事故报告",
    media_type: "text/markdown",
    inline_content: {
      markdown: "# 支付事故报告\n\n根因与证据。",
      verification: {
        verified: true,
        confidence: 0.94,
        coverage: 1,
        missing_evidence: [],
        checks: { citations: true },
      },
    },
    storage_key: null,
    classification: "CONFIDENTIAL",
    lineage: { source_run_id: "run-1" },
    created_at: "2026-09-01T08:00:00Z",
    ...partial,
  };
}

function evidence(partial: Partial<Evidence> = {}): Evidence {
  return {
    id: "evidence-1",
    run_id: "run-1",
    step_id: "step-1",
    evidence_type: "TOOL",
    source: "metric.query",
    resource: "payment_success_rate",
    observed_at: "2026-09-01T08:00:00Z",
    ingested_at: "2026-09-01T08:00:01Z",
    content: { value: 0.82, baseline: 0.96 },
    content_fingerprint: "a".repeat(64),
    confidence: "HIGH",
    classification: "CONFIDENTIAL",
    permissions: ["metrics.read"],
    lineage: { connector: "prometheus" },
    ...partial,
  };
}

function event(partial: Partial<RunEvent> = {}): RunEvent {
  return {
    id: "event-1",
    event_id: "event-envelope-1",
    organization_id: "org-1",
    aggregate_type: "run",
    aggregate_id: "run-1",
    sequence: 8,
    name: "run.completed",
    run_id: "run-1",
    run_sequence: 8,
    causation_id: "event-previous",
    correlation_id: "run-1",
    actor_type: "SYSTEM",
    actor_id: null,
    schema_version: 1,
    classification: "INTERNAL",
    payload: { verification: "VERIFIED", evidence_count: 3 },
    created_at: "2026-09-01T08:05:00Z",
    ...partial,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  listWorkspaceReports.mockResolvedValue([artifact()]);
  listWorkspaceDashboards.mockResolvedValue([]);
  listWorkspaceSql.mockResolvedValue([
    artifact({
      id: "sql-1",
      kind: "SQL",
      title: "支付成功率查询",
      media_type: "application/sql",
      inline_content: {
        sql: "SELECT * FROM payments LIMIT 100",
        validation: { valid: true },
      },
    }),
  ]);
  listWorkspaceEvidence.mockResolvedValue([
    evidence(),
    evidence({ id: "evidence-2", evidence_type: "LOG", source: "loki", resource: "payment-service" }),
  ]);
  listWorkspaceTimeline.mockResolvedValue([
    event(),
    event({
      id: "event-2",
      event_id: "event-envelope-2",
      aggregate_id: "run-2",
      run_id: "run-2",
      run_sequence: 1,
      sequence: 1,
      name: "run.started",
      correlation_id: "run-2",
      payload: { worker: "worker-1" },
    }),
  ]);
});

describe("Workspace artifact projections", () => {
  it("renders only persisted REPORT artifacts and keeps refreshed detail by id", async () => {
    const refreshed = artifact({ title: "支付事故报告 v2" });
    listWorkspaceReports.mockResolvedValueOnce([artifact()]).mockResolvedValue([refreshed]);
    render(<ReportsView workspace={workspace()} />);
    fireEvent.click(await screen.findByRole("button", { name: /支付事故报告/ }));
    expect(within(screen.getByLabelText("报告详情")).getByText("根因与证据。")).toBeDefined();
    expect(within(screen.getByLabelText("报告摘要")).getAllByText("1", { selector: "strong" })).toHaveLength(2);

    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    await screen.findByRole("button", { name: /支付事故报告 v2/ });
    expect(within(screen.getByLabelText("报告详情")).getByText("支付事故报告 v2")).toBeDefined();
  });

  it("loads unique valid Dashboard panel references without inventing panels", async () => {
    const dashboard = artifact({
      id: "dashboard-1",
      kind: "DASHBOARD",
      title: "支付经营仪表盘",
      inline_content: {
        panels: [
          { artifact_id: " chart-1 " },
          { artifact_id: "chart-1" },
          { artifact_id: 42 },
          { artifact_id: "table-1" },
        ],
        chart_artifact_ids: ["chart-1"],
      },
    });
    const chart = artifact({
      id: "chart-1",
      kind: "CHART",
      title: "支付成功率趋势",
      inline_content: {
        mark: "line",
        data: { values: [{ day: "2026-09-01", success_rate: 0.82 }] },
        encoding: {
          x: { field: "day", type: "temporal" },
          y: { field: "success_rate", type: "quantitative" },
        },
      },
    });
    const table = artifact({
      id: "table-1",
      kind: "TABLE",
      title: "渠道明细",
      inline_content: { columns: ["channel", "rate"], rows: [{ channel: "A", rate: 0.82 }] },
    });
    listWorkspaceDashboards.mockResolvedValue([dashboard]);
    getArtifact.mockImplementation(async (id) => id === "chart-1" ? chart : table);
    render(<DashboardsView workspace={workspace()} />);
    fireEvent.click(await screen.findByRole("button", { name: /支付经营仪表盘/ }));

    await screen.findByText("支付成功率趋势");
    expect(screen.getByText("渠道明细")).toBeDefined();
    expect(getArtifact.mock.calls.map(([id]) => id)).toEqual(["chart-1", "table-1"]);
  });

  it("clears a failed Dashboard panel error when a new dashboard succeeds", async () => {
    const failed = artifact({
      id: "dashboard-failed",
      kind: "DASHBOARD",
      title: "失败仪表盘",
      inline_content: { panels: [{ artifact_id: "missing-panel" }] },
    });
    const healthy = artifact({
      id: "dashboard-healthy",
      kind: "DASHBOARD",
      title: "健康仪表盘",
      inline_content: { panels: [{ artifact_id: "table-healthy" }] },
    });
    const panel = artifact({ id: "table-healthy", kind: "TABLE", title: "健康面板" });
    listWorkspaceDashboards.mockResolvedValue([failed, healthy]);
    getArtifact.mockImplementation(async (id) => {
      if (id === "missing-panel") throw new Error("面板不存在");
      return panel;
    });
    render(<DashboardsView workspace={workspace()} />);
    fireEvent.click(await screen.findByRole("button", { name: /失败仪表盘/ }));
    await screen.findByText("面板不存在");

    fireEvent.click(screen.getByRole("button", { name: /健康仪表盘/ }));
    await screen.findByText("健康面板");
    expect(screen.queryByText("面板不存在")).toBeNull();
  });

  it("renders only persisted validated SQL without executing it", async () => {
    render(<SqlView workspace={workspace()} />);
    fireEvent.click(await screen.findByRole("button", { name: /支付成功率查询/ }));

    expect(within(screen.getByLabelText("SQL 摘要")).getAllByText("1")).toHaveLength(2);
    expect(screen.getByText("SELECT * FROM payments LIMIT 100")).toBeDefined();
  });
});

describe("Workspace Evidence and Event projections", () => {
  it("renders persisted Evidence types and the selected immutable envelope", async () => {
    render(<EvidenceView workspace={workspace()} />);
    fireEvent.click(await screen.findByRole("button", { name: /metric\.query.*TOOL/ }));

    const summary = screen.getByLabelText("证据摘要");
    expect(within(summary).getAllByText("2", { selector: "strong" })).toHaveLength(2);
    const detail = screen.getByLabelText("证据详情");
    expect(within(detail).getByText("payment_success_rate")).toBeDefined();
    expect(within(detail).getByText(/Run run-1 写入/)).toBeDefined();
    expect(within(detail).getByText("metric.query")).toBeDefined();
  });

  it("renders persisted Event payloads and counts distinct Runs", async () => {
    render(<TimelineView workspace={workspace()} />);
    fireEvent.click(await screen.findByRole("button", { name: /run\.completed.*run.*INTERNAL/ }));

    const summary = screen.getByLabelText("时间线摘要");
    expect(within(summary).getAllByText("2", { selector: "strong" })).toHaveLength(2);
    const detail = screen.getByLabelText("事件详情");
    expect(within(detail).getAllByText("run-1")).toHaveLength(2);
    expect(within(detail).getByText(/"verification": "VERIFIED"/)).toBeDefined();
    expect(within(detail).getByText("event-1")).toBeDefined();
  });
});
