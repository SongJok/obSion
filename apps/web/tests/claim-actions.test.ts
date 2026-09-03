import { describe, expect, it } from "vitest";

import {
  CLAIM_EVIDENCE_LINES_MAX,
  CLAIM_TITLE_MAX,
  claimActionTitle,
  claimDecisionPayload,
  claimEvidenceLines,
  claimTaskPayload,
  truncateClaim,
} from "@/lib/claim-actions";
import type { Claim, Evidence } from "@/lib/types";

function claim(partial: Partial<Claim> = {}): Claim {
  return {
    id: "claim-1",
    run_id: "run-1",
    ordinal: 1,
    statement: "支付成功率下降主要由渠道 B 的 5xx 激增导致",
    confidence: "HIGH",
    verification_status: "VERIFIED",
    critic_notes: {},
    evidence_ids: ["ev-1", "ev-2"],
    ...partial,
  };
}

function evidence(partial: Partial<Evidence> = {}): Evidence {
  return {
    id: "ev-1",
    run_id: "run-1",
    evidence_type: "DATA",
    source: "payments 数据集",
    resource: "sql://payments/success_rate",
    content: {},
    ...partial,
  } as Evidence;
}

const RUN_ID = "123e4567-e89b-42d3-a456-426614174000";

describe("truncateClaim", () => {
  it("keeps short statements and truncates long ones with an ellipsis", () => {
    expect(truncateClaim("短结论")).toBe("短结论");
    const long = "支付".repeat(80);
    const truncated = truncateClaim(long);
    expect(truncated.endsWith("…")).toBe(true);
    expect(truncated.length).toBeLessThanOrEqual(CLAIM_TITLE_MAX);
  });

  it("collapses whitespace before measuring", () => {
    expect(truncateClaim("  结论\n  内容  ")).toBe("结论 内容");
  });
});

describe("claimActionTitle", () => {
  it("prefixes the claim ordinal", () => {
    expect(claimActionTitle(claim(), 0)).toBe("结论 C1：支付成功率下降主要由渠道 B 的 5xx 激增导致");
    expect(claimActionTitle(claim(), 2).startsWith("结论 C3：")).toBe(true);
  });
});

describe("claimEvidenceLines", () => {
  it("labels known evidence and skips dangling ids", () => {
    const items = [
      evidence(),
      evidence({ id: "ev-2", evidence_type: "OBSERVABILITY", source: "网关日志", resource: "log://gateway/5xx" }),
    ];
    expect(claimEvidenceLines(claim(), items)).toEqual([
      "DATA · payments 数据集 — sql://payments/success_rate",
      "OBSERVABILITY · 网关日志 — log://gateway/5xx",
    ]);
    expect(claimEvidenceLines(claim({ evidence_ids: ["ev-missing"] }), items)).toEqual([]);
  });

  it("caps the number of lines", () => {
    const ids = Array.from({ length: CLAIM_EVIDENCE_LINES_MAX + 3 }, (_, index) => `ev-${index}`);
    const items = ids.map((id) => evidence({ id }));
    expect(claimEvidenceLines(claim({ evidence_ids: ids }), items)).toHaveLength(CLAIM_EVIDENCE_LINES_MAX);
  });
});

describe("claimTaskPayload", () => {
  it("carries the statement, provenance, and source Run", () => {
    const payload = claimTaskPayload(claim(), RUN_ID, 0);
    expect(payload.title).toBe("结论 C1：支付成功率下降主要由渠道 B 的 5xx 激增导致");
    expect(payload.source_run_id).toBe(RUN_ID);
    expect(String(payload.description)).toContain("支付成功率下降主要由渠道 B 的 5xx 激增导致");
    expect(String(payload.description)).toContain("Run 123e4567");
    expect(String(payload.description)).toContain("2 项证据支撑");
    expect(String(payload.description)).toContain("VERIFIED");
  });
});

describe("claimDecisionPayload", () => {
  it("builds summary and evidence-backed rationale with the source Run", () => {
    const items = [evidence(), evidence({ id: "ev-2", source: "网关日志", resource: "log://gateway/5xx" })];
    const payload = claimDecisionPayload(claim(), items, RUN_ID, 1);
    expect(String(payload.title).startsWith("结论 C2：")).toBe(true);
    expect(payload.summary).toBe("支付成功率下降主要由渠道 B 的 5xx 激增导致");
    expect(payload.source_run_id).toBe(RUN_ID);
    const rationale = String(payload.rationale);
    expect(rationale).toContain("VERIFIED");
    expect(rationale).toContain("置信度 HIGH");
    expect(rationale).toContain("- DATA · payments 数据集 — sql://payments/success_rate");
  });

  it("notes evidence beyond the display cap", () => {
    const ids = Array.from({ length: CLAIM_EVIDENCE_LINES_MAX + 2 }, (_, index) => `ev-${index}`);
    const items = ids.map((id) => evidence({ id }));
    const payload = claimDecisionPayload(claim({ evidence_ids: ids }), items, RUN_ID, 0);
    expect(String(payload.rationale)).toContain("另有 2 项证据见运行详情");
  });
});
