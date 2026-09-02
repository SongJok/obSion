import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DataView } from "@/components/data-view";
import { api } from "@/lib/api";
import type { Metric, MetricLineage } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listMetrics: vi.fn(),
      getMetricLineage: vi.fn(),
    },
  };
});

const listMetrics = vi.mocked(api.listMetrics);
const getMetricLineage = vi.mocked(api.getMetricLineage);

afterEach(() => {
  cleanup();
});

function metric(partial: Partial<Metric> = {}): Metric {
  return {
    id: "metric-paid-rate",
    name: "new_user_paid_rate",
    display_name: "新用户付费率",
    version: 3,
    expression: "paid_users / new_users",
    filters: { pay_status: "SUCCESS" },
    time_column: "paid_at",
    source_table_id: "table-payment-order",
    owner: "payment-team",
    synonyms: ["付费转化率", "充值转化"],
    validated: true,
    updated_at: "2026-09-01T08:00:00Z",
    ...partial,
  };
}

function lineage(metricId = "metric-paid-rate", suffix = "payment"): MetricLineage {
  return {
    metric: { id: metricId, name: `${suffix}_rate`, version: 3 },
    table: { id: `table-${suffix}`, name: `${suffix}_orders`, owner: `${suffix}-team` },
    data_source: {
      id: `source-${suffix}`,
      name: `${suffix}-replica`,
      environment: "production",
      read_only: true,
    },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  vi.clearAllMocks();
  listMetrics.mockResolvedValue([
    metric(),
    metric({
      id: "metric-gmv",
      name: "gross_merchandise_value",
      display_name: "成交总额",
      expression: "SUM(amount)",
      owner: "commerce-team",
      synonyms: ["GMV"],
    }),
  ]);
  getMetricLineage.mockResolvedValue(lineage());
});

describe("DataView interactions", () => {
  it("filters only verified metrics by name, synonym, or owner", async () => {
    render(<DataView />);
    await screen.findByText("新用户付费率");
    const search = screen.getByPlaceholderText("按指标、同义词或负责人搜索");

    fireEvent.change(search, { target: { value: "充值转化" } });
    expect(screen.getByText("新用户付费率")).toBeDefined();
    expect(screen.queryByText("成交总额")).toBeNull();

    fireEvent.change(search, { target: { value: "commerce-team" } });
    expect(screen.getByText("成交总额")).toBeDefined();
    expect(screen.queryByText("新用户付费率")).toBeNull();

    fireEvent.change(search, { target: { value: "未注册指标" } });
    expect(screen.getByText("没有匹配的已验证指标")).toBeDefined();
  });

  it("opens a governed metric definition in a named dialog", async () => {
    render(<DataView />);
    await screen.findByText("新用户付费率");
    fireEvent.click(screen.getAllByRole("button", { name: "查看定义" })[0]);

    expect(screen.getByRole("dialog", { name: "新用户付费率 指标详情" })).toBeDefined();
    expect(screen.getByText("paid_users / new_users")).toBeDefined();
    expect(screen.getByText("paid_at")).toBeDefined();
    expect(screen.getByText("付费转化率、充值转化")).toBeDefined();
    expect(screen.getByText('{"pay_status":"SUCCESS"}')).toBeDefined();
    expect(screen.getByRole("tab", { name: "指标定义" }).getAttribute("aria-selected")).toBe("true");
  });

  it("uses roving tabs and renders read-only lineage from the lineage API", async () => {
    render(<DataView />);
    await screen.findByText("新用户付费率");
    fireEvent.click(screen.getAllByRole("button", { name: "查看定义" })[0]);
    const definitionTab = screen.getByRole("tab", { name: "指标定义" });
    definitionTab.focus();
    fireEvent.keyDown(definitionTab, { key: "ArrowRight" });

    await waitFor(() => expect(getMetricLineage).toHaveBeenCalledWith("metric-paid-rate"));
    const lineageTab = screen.getByRole("tab", { name: "数据血缘" });
    expect(lineageTab.getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(lineageTab);
    await screen.findByText("payment-replica");
    expect(screen.getByText("production · 只读")).toBeDefined();
    expect(screen.getByText("payment_orders")).toBeDefined();
  });

  it("surfaces a lineage failure without inventing a graph", async () => {
    getMetricLineage.mockRejectedValue(new Error("血缘服务暂时不可用"));
    render(<DataView />);
    await screen.findByText("新用户付费率");
    fireEvent.click(screen.getAllByRole("button", { name: "查看血缘" })[0]);

    await screen.findByText("血缘服务暂时不可用");
    expect(screen.queryByLabelText("指标数据血缘")).toBeNull();
  });

  it("ignores a slower stale lineage response after switching metrics", async () => {
    const paid = deferred<MetricLineage>();
    const gmv = deferred<MetricLineage>();
    getMetricLineage.mockImplementation((metricId) =>
      metricId === "metric-paid-rate" ? paid.promise : gmv.promise,
    );
    render(<DataView />);
    await screen.findByText("新用户付费率");
    const lineageButtons = screen.getAllByRole("button", { name: "查看血缘" });
    fireEvent.click(lineageButtons[0]);
    fireEvent.click(screen.getByLabelText("关闭指标详情"));
    fireEvent.click(lineageButtons[1]);

    await act(async () => {
      gmv.resolve(lineage("metric-gmv", "gmv"));
      await gmv.promise;
    });
    await screen.findByText("gmv-replica");

    await act(async () => {
      paid.resolve(lineage("metric-paid-rate", "stale-payment"));
      await paid.promise;
    });
    expect(screen.getByText("gmv-replica")).toBeDefined();
    expect(screen.queryByText("stale-payment-replica")).toBeNull();
  });
});
