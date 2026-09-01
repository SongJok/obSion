import { describe, expect, it } from "vitest";

import {
  MAX_ATTRIBUTE_ENTRIES,
  asNumber,
  asString,
  changeItemView,
  classifyEvidence,
  codeItemView,
  formatAttributeValue,
  observabilityEventView,
  recordArray,
  stringArray,
} from "@/lib/typed-evidence";

describe("classifyEvidence", () => {
  it("dispatches observability events[] envelopes", () => {
    expect(classifyEvidence("METRIC", { operation: "metric.query", events: [] })).toBe("events");
    expect(classifyEvidence("LOG", { operation: "log.search", events: [{}] })).toBe("events");
    expect(classifyEvidence("TRACE", { operation: "trace.search", events: [] })).toBe("events");
  });

  it("dispatches engineering items[] envelopes and specializes CODE", () => {
    expect(classifyEvidence("GIT", { operation: "git.diff", items: [] })).toBe("items");
    expect(classifyEvidence("CONFIG", { operation: "config.diff", items: [] })).toBe("items");
    expect(classifyEvidence("DEPLOYMENT", { operation: "deployment.commit", items: [] })).toBe("items");
    expect(classifyEvidence("CODE", { operation: "code.search", items: [] })).toBe("code-items");
  });

  it("dispatches data tables, explain plans, knowledge hits, and documents", () => {
    expect(classifyEvidence("DATA", { columns: ["a"], rows: [{ a: 1 }] })).toBe("data-table");
    expect(classifyEvidence("DATA", { plan: { columns: [] }, validation: {} })).toBe("explain-plan");
    expect(classifyEvidence("DOCUMENT", { query: "q", hits: [] })).toBe("knowledge-hits");
    expect(classifyEvidence("DOCUMENT", { title: "t", text: "body" })).toBe("document-text");
  });

  it("keeps unknown payloads on the generic fallback", () => {
    expect(classifyEvidence("TOOL", { protocol: "mcp", echo: {} })).toBe("generic");
    expect(classifyEvidence("SQL", { unexpected: true })).toBe("generic");
    expect(classifyEvidence("DATA", {})).toBe("generic");
  });

  it("checks explain plans before anything else", () => {
    expect(
      classifyEvidence("DATA", { plan: { columns: ["x"], rows: [] }, validation: {}, hits: [] }),
    ).toBe("explain-plan");
  });
});

describe("type-guard accessors", () => {
  it("asString rejects non-strings and blanks", () => {
    expect(asString("value")).toBe("value");
    expect(asString("   ")).toBeUndefined();
    expect(asString(42)).toBeUndefined();
    expect(asString(null)).toBeUndefined();
  });

  it("asNumber accepts finite numbers and numeric strings only", () => {
    expect(asNumber(3.5)).toBe(3.5);
    expect(asNumber("2")).toBe(2);
    expect(asNumber("abc")).toBeUndefined();
    expect(asNumber(Number.NaN)).toBeUndefined();
    expect(asNumber(Infinity)).toBeUndefined();
  });

  it("recordArray and stringArray filter foreign shapes", () => {
    expect(recordArray([{ a: 1 }, "x", null, [1]])).toEqual([{ a: 1 }]);
    expect(recordArray("nope")).toEqual([]);
    expect(stringArray(["a", 1, "b"])).toEqual(["a", "b"]);
  });

  it("formatAttributeValue serializes without throwing and truncates long JSON", () => {
    expect(formatAttributeValue("s")).toBe("s");
    expect(formatAttributeValue(7)).toBe("7");
    expect(formatAttributeValue(true)).toBe("true");
    expect(formatAttributeValue(null)).toBe("");
    const long = formatAttributeValue({ blob: "x".repeat(500) });
    expect(long.length).toBeLessThanOrEqual(160);
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(formatAttributeValue(circular)).toBe("[object Object]");
  });
});

describe("observabilityEventView", () => {
  it("prefers the log message as headline and keeps remaining attributes bounded", () => {
    const attributes: Record<string, unknown> = { message: "支付网关超时", operation: "log.error" };
    for (let index = 0; index < MAX_ATTRIBUTE_ENTRIES + 5; index += 1) {
      attributes[`extra_${index}`] = index;
    }
    const view = observabilityEventView({
      timestamp: "2026-08-21T10:00:00Z",
      service: "payments",
      environment: "prod",
      severity: "ERROR",
      attributes,
    });
    expect(view.headline).toBe("支付网关超时");
    expect(view.service).toBe("payments");
    expect(view.severity).toBe("ERROR");
    expect(view.attributes.length).toBeLessThanOrEqual(MAX_ATTRIBUTE_ENTRIES);
    expect(view.attributes.some((entry) => entry.key === "message")).toBe(false);
  });

  it("renders metric values with units and spans with durations", () => {
    const metric = observabilityEventView({ attributes: { metric: "payment_success_rate", value: 0.97, unit: "ratio" } });
    expect(metric.headline).toBe("payment_success_rate = 0.97 ratio");
    const span = observabilityEventView({ attributes: { span_name: "charge", duration_ms: 182 } });
    expect(span.headline).toBe("charge");
    expect(span.detail).toBe("182 ms");
  });

  it("tolerates entries without attributes", () => {
    const view = observabilityEventView({ timestamp: "2026-08-21T10:00:00Z" });
    expect(view.headline).toBeUndefined();
    expect(view.attributes).toEqual([]);
  });
});

describe("changeItemView", () => {
  it("picks patch or diff payloads and config before/after pairs", () => {
    const git = changeItemView({
      title: "修复重试",
      commit_id: "abcdef123456",
      attributes: { patch: "@@ -1 +1 @@\n-old\n+new" },
    });
    expect(git.diff).toContain("-old");
    expect(git.commit).toBe("abcdef123456");
    const config = changeItemView({ attributes: { key: "timeout_ms", previous: 100, current: 200 } });
    expect(config.configKey).toBe("timeout_ms");
    expect(config.previous).toBe("100");
    expect(config.current).toBe("200");
  });
});

describe("codeItemView", () => {
  it("projects persisted Code Graph fields only", () => {
    const view = codeItemView({
      name: "charge",
      qualified_name: "payments.service.charge",
      kind: "function",
      path: "src/payments/service.py",
      language: "python",
      repository: "obsion",
      commit_id: "deadbeef99",
      start_line: 42,
      end_line: 87,
    });
    expect(view.qualifiedName).toBe("payments.service.charge");
    expect(view.startLine).toBe(42);
    expect(view.endLine).toBe(87);
  });
});
