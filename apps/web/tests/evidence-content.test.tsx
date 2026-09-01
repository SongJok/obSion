import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { EvidenceContent, EvidenceMeta } from "@/components/evidence-content";
import type { Evidence } from "@/lib/types";

function evidence(partial: Partial<Evidence>): Evidence {
  return {
    id: "ev-1",
    run_id: "run-abcdef123456",
    step_id: "step-12345678",
    evidence_type: "TOOL",
    source: "unit-test",
    resource: "resource://fixture",
    observed_at: "2026-08-21T10:00:00Z",
    ingested_at: "2026-08-21T10:00:01Z",
    content: {},
    content_fingerprint: "a".repeat(64),
    confidence: "0.95",
    classification: "INTERNAL",
    permissions: ["artifact.read"],
    lineage: { connector_id: "conn-1" },
    ...partial,
  };
}

afterEach(cleanup);

describe("EvidenceContent", () => {
  it("renders observability events with severity and headlines", () => {
    render(
      <EvidenceContent
        evidence={evidence({
          evidence_type: "LOG",
          content: {
            operation: "log.error",
            count: 1,
            events: [
              {
                timestamp: "2026-08-21T10:00:00Z",
                service: "payments",
                severity: "ERROR",
                attributes: { message: "支付网关超时" },
              },
            ],
          },
        })}
      />,
    );
    expect(screen.getByText("支付网关超时")).toBeTruthy();
    expect(screen.getByText("payments")).toBeTruthy();
    expect(screen.getByText("ERROR")).toBeTruthy();
    expect(screen.getByText("log.error")).toBeTruthy();
  });

  it("renders git diffs inside engineering items", () => {
    const { container } = render(
      <EvidenceContent
        evidence={evidence({
          evidence_type: "GIT",
          content: {
            operation: "git.diff",
            count: 1,
            items: [
              {
                title: "修复重试",
                commit_id: "abcdef1234567890",
                repository: "obsion",
                attributes: { patch: "@@ -1 +1 @@\n-old\n+new" },
              },
            ],
          },
        })}
      />,
    );
    expect(screen.getByText("修复重试")).toBeTruthy();
    expect(container.querySelector("pre.diff-view")?.textContent).toContain("+new");
  });

  it("renders config diffs as before/after pairs", () => {
    render(
      <EvidenceContent
        evidence={evidence({
          evidence_type: "CONFIG",
          content: {
            operation: "config.diff",
            items: [{ attributes: { key: "timeout_ms", previous: 100, current: 200 } }],
          },
        })}
      />,
    );
    expect(screen.getByText("变更前")).toBeTruthy();
    expect(screen.getByText("变更后")).toBeTruthy();
    expect(screen.getByText("200")).toBeTruthy();
  });

  it("renders query results as a real table with a truncation note", () => {
    const rows = Array.from({ length: 120 }, (_, index) => ({ day: `08-${index}`, rate: index }));
    render(
      <EvidenceContent
        evidence={evidence({
          evidence_type: "DATA",
          content: { columns: ["day", "rate"], rows, row_count: 120 },
        })}
      />,
    );
    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.getByText(/显示前 100 行，共 120 行/)).toBeTruthy();
  });

  it("renders code symbols with file locations", () => {
    render(
      <EvidenceContent
        evidence={evidence({
          evidence_type: "CODE",
          content: {
            operation: "code.symbol",
            items: [
              {
                name: "charge",
                qualified_name: "payments.service.charge",
                kind: "function",
                path: "src/payments/service.py",
                start_line: 42,
                end_line: 87,
              },
            ],
          },
        })}
      />,
    );
    expect(screen.getByText("charge")).toBeTruthy();
    expect(screen.getByText("src/payments/service.py:42-87")).toBeTruthy();
  });

  it("keeps the citation provenance for knowledge hits", () => {
    render(
      <EvidenceContent
        evidence={evidence({
          evidence_type: "DOCUMENT",
          content: {
            query: "退款",
            hits: [{ chunk_id: "c1", title: "退款政策", source: "feishu" }],
          },
        })}
      />,
    );
    expect(screen.getByText("引用溯源")).toBeTruthy();
    expect(screen.getByText(/退款政策/)).toBeTruthy();
  });

  it("falls back to raw JSON for unknown payloads", () => {
    const { container } = render(
      <EvidenceContent
        evidence={evidence({ evidence_type: "TOOL", content: { protocol: "mcp", tool: "echo" } })}
      />,
    );
    expect(container.querySelector("pre.evidence-raw-json")?.textContent).toContain('"protocol": "mcp"');
  });
});

describe("EvidenceMeta", () => {
  it("renders only persisted ledger fields", () => {
    render(<EvidenceMeta evidence={evidence({})} />);
    expect(screen.getByText("观测时间")).toBeTruthy();
    expect(screen.getByText("入库时间")).toBeTruthy();
    expect(screen.getByText("95%")).toBeTruthy();
    expect(screen.getByText("INTERNAL")).toBeTruthy();
    expect(screen.getByText("artifact.read")).toBeTruthy();
    expect(screen.getByText("aaaaaaaaaaaa")).toBeTruthy();
    expect(screen.getByText("血缘")).toBeTruthy();
    expect(screen.getByText("conn-1")).toBeTruthy();
  });

  it("omits the step row when step_id is null", () => {
    render(<EvidenceMeta evidence={evidence({ step_id: null })} />);
    expect(screen.queryByText("产生步骤")).toBeNull();
  });
});
