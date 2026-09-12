import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { Conversation } from "@/components/conversation";
import type { Artifact, MessageBundle, Run, Turn } from "@/lib/types";

afterEach(cleanup);

it("hides cached answers and copy/replay actions after source access is withdrawn", () => {
  render(<Conversation messages={[{
    turn: { id: "turn", input_text: "查询采购制度" } as Turn,
    run: { id: "run", status: "COMPLETED", source_content_available: false } as Run,
    artifact: { id: "answer", title: "缓存旧答案", inline_content: { markdown: "已经撤权的企业正文" } } as Artifact,
  }]} feedbackByRun={{}} onFeedback={vi.fn()} onReplay={vi.fn()} />);
  expect(screen.getByText("资料权限或版本已变化")).toBeTruthy();
  expect(screen.queryByText("已经撤权的企业正文")).toBeNull();
  expect(screen.queryByText("缓存旧答案")).toBeNull();
  expect(screen.queryByLabelText("复制回答")).toBeNull();
  expect(screen.queryByLabelText("回放此运行快照")).toBeNull();
});

it.each(["GENERAL", "KNOWLEDGE"])("labels %s answers without fabricating evidence validation", (kind) => {
  const artifact = {
    id: "answer", workspace_id: "workspace", run_id: null, kind: "TEXT", title: "Obsion answer",
    media_type: "text/markdown", storage_key: null, classification: "INTERNAL", lineage: {},
    created_at: "2026-09-11T08:00:00Z", inline_content: {
      markdown: "机器学习从例子中学习规律。", response_kind: kind,
      verification: { verified: false, confidence: 0, coverage: 0, missing_evidence: [], checks: {} },
    },
  } as Artifact;
  const messages: MessageBundle[] = [{
    turn: { id: "turn", input_text: "解释机器学习" } as Turn,
    run: undefined, artifact,
  }];
  render(<Conversation messages={messages} feedbackByRun={{}} onFeedback={vi.fn()} onReplay={vi.fn()} />);
  expect(screen.getByText("机器学习从例子中学习规律。")).toBeTruthy();
  if (kind === "GENERAL") {
    expect(screen.getByText("通用回答")).toBeTruthy();
    expect(screen.queryByText("部分证据")).toBeNull();
    expect(screen.queryByText(/置信度/)).toBeNull();
  } else {
    expect(screen.getByText("部分证据")).toBeTruthy();
    expect(screen.queryByText("通用回答")).toBeNull();
  }
  expect(screen.queryByText("证据验证通过")).toBeNull();
});

it.each([[true, true], [false, false], [true, false]])(
  "requires both source review and final verification before showing success (%s/%s)",
  (accepted, verified) => {
    const artifact = {
      id: "answer", workspace_id: "workspace", run_id: null, kind: "TEXT", title: "Obsion answer",
      media_type: "text/markdown", storage_key: null, classification: "INTERNAL", lineage: {},
      created_at: "2026-09-11T08:00:00Z", inline_content: {
        markdown: "交通报销需要审批。",
        grounding: { version: "knowledge-grounding.v1", method: "model_review_with_exact_quotes", accepted, reason_code: "test" },
        verification: { verified, confidence: 0.9, coverage: 1, missing_evidence: [], checks: {} },
      },
    } as Artifact;
    render(<Conversation messages={[{ turn: { id: "turn", input_text: "交通报销如何审批？" } as Turn, run: undefined, artifact }]}
      feedbackByRun={{}} onFeedback={vi.fn()} onReplay={vi.fn()} />);
    expect(screen.getByText(accepted && verified ? "已对照原文复核" : "未通过原文复核")).toBeTruthy();
    expect(screen.queryByText(/置信度/)).toBeNull();
    expect(screen.queryByText("证据验证通过")).toBeNull();
  },
);
