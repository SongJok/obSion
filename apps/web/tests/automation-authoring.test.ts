import { describe, expect, it } from "vitest";

import {
  CRON_PRESETS,
  DEFAULT_NOTIFY_BODY,
  DEFAULT_REVIEW_INSTRUCTIONS,
  buildSchedulePayload,
  buildSpecFromDraft,
  cronIsValid,
  cronLabel,
  draftFromSpec,
  outputRefLabel,
  artifactOutputRefs,
  parseInputPayload,
  parseWorkflowSpec,
  sortedVersions,
  versionStepSummary,
  type AuthoringDraft,
} from "@/lib/automation-authoring";
import type { WorkflowVersion } from "@/lib/types";

function draft(partial: Partial<AuthoringDraft> = {}): AuthoringDraft {
  return {
    prompt: "  分析过去 24 小时支付成功率  ",
    review: false,
    reviewInstructions: "",
    disallowSelfReview: false,
    notifyTitle: "",
    notifyBody: "",
    ...partial,
  };
}

function version(partial: Partial<WorkflowVersion> = {}): WorkflowVersion {
  return {
    id: "ver-1",
    workflow_id: "wf-1",
    version: 1,
    spec: { steps: [] },
    checksum_sha256: "abc123",
    created_by: "owner-1",
    published_at: null,
    created_at: "2026-08-20T08:00:00Z",
    ...partial,
  };
}

describe("buildSpecFromDraft", () => {
  it("builds the analyze-notify chain without a review gate", () => {
    const spec = buildSpecFromDraft(draft(), "每日支付分析");
    expect(spec.steps.map((step) => step.id)).toEqual(["analyze", "notify"]);
    expect(spec.steps[0]).toMatchObject({
      type: "ANALYSIS",
      depends_on: [],
      prompt: "分析过去 24 小时支付成功率",
    });
    expect(spec.steps[1]).toMatchObject({
      type: "NOTIFICATION",
      depends_on: ["analyze"],
      title: "每日支付分析 已完成",
      body: DEFAULT_NOTIFY_BODY,
    });
  });

  it("routes notification through the review gate when enabled", () => {
    const spec = buildSpecFromDraft(
      draft({ review: true, reviewInstructions: "  核对证据覆盖  ", disallowSelfReview: true }),
      "每日支付分析",
    );
    expect(spec.steps.map((step) => step.id)).toEqual(["analyze", "review", "notify"]);
    expect(spec.steps[1]).toMatchObject({
      type: "HUMAN_REVIEW",
      depends_on: ["analyze"],
      review_instructions: "核对证据覆盖",
      disallow_self_review: true,
    });
    expect(spec.steps[2].depends_on).toEqual(["review"]);
  });

  it("falls back to the default review instructions and notification title", () => {
    const spec = buildSpecFromDraft(draft({ review: true }), "  ");
    expect(spec.steps[1].review_instructions).toBe(DEFAULT_REVIEW_INSTRUCTIONS);
    expect(spec.steps[2].title).toBe(" 已完成");
  });
});

describe("draftFromSpec", () => {
  it("round-trips a reviewed spec back into the authoring draft", () => {
    const spec = buildSpecFromDraft(
      draft({
        review: true,
        reviewInstructions: "核对证据覆盖",
        disallowSelfReview: true,
        notifyTitle: "支付晨报",
        notifyBody: "请查收",
      }),
      "每日支付分析",
    );
    expect(draftFromSpec(spec)).toEqual({
      prompt: "分析过去 24 小时支付成功率",
      review: true,
      reviewInstructions: "核对证据覆盖",
      disallowSelfReview: true,
      notifyTitle: "支付晨报",
      notifyBody: "请查收",
    });
  });

  it("returns safe defaults for a spec without optional steps", () => {
    expect(draftFromSpec({ steps: [] })).toEqual({
      prompt: "",
      review: false,
      reviewInstructions: "",
      disallowSelfReview: false,
      notifyTitle: "",
      notifyBody: "",
    });
  });
});

describe("parseWorkflowSpec", () => {
  it("accepts a well-formed spec dictionary", () => {
    const spec = buildSpecFromDraft(draft({ review: true }), "wf");
    expect(parseWorkflowSpec(spec)).toEqual(spec);
  });

  it("rejects malformed payloads", () => {
    expect(parseWorkflowSpec(null)).toBeNull();
    expect(parseWorkflowSpec([])).toBeNull();
    expect(parseWorkflowSpec({ steps: [] })).toBeNull();
    expect(parseWorkflowSpec({ steps: [{ id: "a" }] })).toBeNull();
    expect(
      parseWorkflowSpec({
        steps: [{ id: "a", name: "A", type: "UNKNOWN", depends_on: [] }],
      }),
    ).toBeNull();
  });
});

describe("versionStepSummary", () => {
  it("summarizes the step chain for version rows", () => {
    const spec = buildSpecFromDraft(draft({ review: true }), "wf");
    expect(versionStepSummary(spec)).toBe("分析 → 人工确认 → 通知");
    expect(versionStepSummary(null)).toBe("步骤定义不可用");
  });
});

describe("parseInputPayload", () => {
  it("treats blank input as an empty payload", () => {
    expect(parseInputPayload("  ")).toEqual({ ok: true, payload: {} });
  });

  it("parses a JSON object", () => {
    expect(parseInputPayload('{"day": "2026-09-01"}')).toEqual({
      ok: true,
      payload: { day: "2026-09-01" },
    });
  });

  it("rejects arrays, scalars, and invalid JSON", () => {
    expect(parseInputPayload("[1, 2]").ok).toBe(false);
    expect(parseInputPayload('"text"').ok).toBe(false);
    expect(parseInputPayload("{broken").ok).toBe(false);
  });
});

describe("schedules", () => {
  it("labels preset crons and falls back to the raw expression", () => {
    expect(cronLabel("0 9 * * *")).toBe("每天 09:00");
    expect(cronLabel("0 9 * * 1")).toBe("每周一 09:00");
    expect(cronLabel("17 4 * * 3")).toBe("17 4 * * 3");
    expect(CRON_PRESETS.every((preset) => cronIsValid(preset.cron))).toBe(true);
  });

  it("validates five-field cron expressions", () => {
    expect(cronIsValid("0 9 * * *")).toBe(true);
    expect(cronIsValid("0 9 * *")).toBe(false);
    expect(cronIsValid("")).toBe(false);
  });

  it("builds a schedule payload that follows the active version", () => {
    const result = buildSchedulePayload({
      name: " 每工作日晨报 ",
      cron: "0 9 * * 1-5",
      timezone: "Asia/Shanghai",
      misfirePolicy: "FIRE_ONCE",
      workflowVersion: null,
      inputPayload: {},
    });
    expect(result).toEqual({
      ok: true,
      payload: {
        name: "每工作日晨报",
        cron_expression: "0 9 * * 1-5",
        timezone: "Asia/Shanghai",
        misfire_policy: "FIRE_ONCE",
        misfire_grace_seconds: 300,
        input_payload: {},
        enabled: true,
      },
    });
  });

  it("pins a fixed version only when requested", () => {
    const result = buildSchedulePayload({
      name: "固定版本",
      cron: "0 * * * *",
      timezone: "UTC",
      misfirePolicy: "SKIP",
      workflowVersion: 3,
      inputPayload: { window: "1h" },
    });
    expect(result.ok && result.payload.workflow_version).toBe(3);
    expect(result.ok && result.payload.input_payload).toEqual({ window: "1h" });
  });

  it("rejects missing names and malformed crons", () => {
    expect(
      buildSchedulePayload({
        name: " ",
        cron: "0 9 * * *",
        timezone: "UTC",
        misfirePolicy: "SKIP",
        workflowVersion: null,
        inputPayload: {},
      }).ok,
    ).toBe(false);
    expect(
      buildSchedulePayload({
        name: "坏周期",
        cron: "0 9",
        timezone: "UTC",
        misfirePolicy: "SKIP",
        workflowVersion: null,
        inputPayload: {},
      }).ok,
    ).toBe(false);
  });
});

describe("versions and output refs", () => {
  it("sorts versions newest first", () => {
    expect(
      sortedVersions([version({ version: 2 }), version({ version: 5 }), version({ version: 1 })]).map(
        (item) => item.version,
      ),
    ).toEqual([5, 2, 1]);
  });

  it("labels artifact and notification output refs", () => {
    expect(outputRefLabel({ type: "artifact", artifact_id: "a1", kind: "REPORT" })).toBe("REPORT 产物");
    expect(outputRefLabel({ type: "notification", notification_id: "n1" })).toBe("通知投递");
    expect(outputRefLabel({ type: "other" })).toBe("输出引用");
    expect(
      artifactOutputRefs([
        { type: "artifact", artifact_id: "a1", kind: "REPORT" },
        { type: "notification", notification_id: "n1" },
      ]),
    ).toEqual([{ type: "artifact", artifact_id: "a1", kind: "REPORT" }]);
  });
});
