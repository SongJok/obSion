"use client";

import { Check, CircleAlert, Copy, FileText, RotateCcw, ShieldCheck, ThumbsDown, ThumbsUp } from "lucide-react";
import { FormEvent, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Artifact, MessageBundle, Run, RunFeedback, RunFeedbackRating } from "@/lib/types";
import { ArtifactPreview, artifactIcon, artifactName } from "./artifact-preview";

const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

interface ConversationProps {
  messages: MessageBundle[];
  feedbackByRun: Record<string, RunFeedback | null | undefined>;
  feedbackPendingRunId?: string;
  replayingRunId?: string;
  replayDisabled?: boolean;
  onFeedback: (run: Run, rating: RunFeedbackRating, reason: string) => Promise<boolean>;
  onReplay: (run: Run) => void;
}

export function Conversation({
  messages,
  feedbackByRun,
  feedbackPendingRunId,
  replayingRunId,
  replayDisabled,
  onFeedback,
  onReplay,
}: ConversationProps) {
  const [copiedArtifactId, setCopiedArtifactId] = useState<string>();
  const [copyErrorArtifactId, setCopyErrorArtifactId] = useState<string>();
  const [reasonRunId, setReasonRunId] = useState<string>();
  const [reason, setReason] = useState("");

  const copyAnswer = async (artifact: Artifact, markdown: string) => {
    setCopyErrorArtifactId(undefined);
    try {
      await navigator.clipboard.writeText(markdown);
      setCopiedArtifactId(artifact.id);
      window.setTimeout(() => {
        setCopiedArtifactId((current) => current === artifact.id ? undefined : current);
      }, 1_800);
    } catch {
      setCopyErrorArtifactId(artifact.id);
    }
  };

  const submitImprovement = async (event: FormEvent<HTMLFormElement>, run: Run) => {
    event.preventDefault();
    if (reason.trim().length < 3) return;
    if (await onFeedback(run, "NEEDS_IMPROVEMENT", reason.trim())) {
      setReasonRunId(undefined);
      setReason("");
    }
  };

  return (
    <div className="conversation" aria-live="polite">
      {messages.map(({ turn, run, artifact, artifacts }) => {
        const markdown = artifact?.inline_content?.markdown;
        const feedback = run ? feedbackByRun[run.id] : undefined;
        const feedbackPending = feedbackPendingRunId === run?.id;
        const canAct = Boolean(run && TERMINAL.has(run.status));
        return (
          <div className="message-pair" key={turn.id}>
            <article className="user-message">
              <div>{turn.input_text}</div>
            </article>

            <article className="assistant-message">
              <div className="assistant-avatar">O</div>
              <div className="assistant-body">
                {!artifact && run?.status === "FAILED" && (
                  <div className="run-error">
                    <CircleAlert size={18} />
                    <div>
                      <strong>这次运行未能完成</strong>
                      <p>{run.error_message ?? "请检查能力连接器和访问策略后重试。"}</p>
                    </div>
                  </div>
                )}
                {!artifact && run && !["FAILED", "CANCELLED"].includes(run.status) && (
                  <div className="thinking">
                    <span className="thinking-dot" />
                    <span>{statusCopy(run.status)}</span>
                  </div>
                )}
                {markdown && (
                  <>
                    <div className="markdown-content">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
                    </div>
                    {artifact.inline_content?.verification && (
                      <div
                        className={`verification-strip ${artifact.inline_content.verification.verified ? "verified" : "partial"}`}
                      >
                        <ShieldCheck size={16} />
                        <span>
                          {artifact.inline_content.verification.verified ? "证据验证通过" : "部分证据"}
                        </span>
                        <strong>
                          {Math.round(artifact.inline_content.verification.confidence * 100)}% 置信度
                        </strong>
                      </div>
                    )}
                    <ArtifactOutputs
                      artifacts={(artifacts ?? []).filter((item) => item.id !== artifact.id)}
                    />
                  </>
                )}
                {artifact && !markdown && (
                  <div className="artifact-link">
                    <FileText size={18} />
                    <span>{artifact.title}</span>
                    <Check size={15} />
                  </div>
                )}
                {canAct && run && (
                  <>
                    <div className="message-actions" aria-label="回答操作">
                      {markdown && artifact && (
                        <button
                          onClick={() => void copyAnswer(artifact, markdown)}
                          aria-label={copiedArtifactId === artifact.id ? "回答已复制" : "复制回答"}
                          title={copiedArtifactId === artifact.id ? "已复制" : "复制回答"}
                        >
                          {copiedArtifactId === artifact.id ? <Check size={15} /> : <Copy size={15} />}
                        </button>
                      )}
                      <button
                        onClick={() => onReplay(run)}
                        disabled={replayDisabled || Boolean(replayingRunId)}
                        aria-label="回放此运行快照"
                        title="使用固定证据与产物回放，不重新访问外部系统"
                      >
                        <RotateCcw size={15} className={replayingRunId === run.id ? "spin" : ""} />
                      </button>
                      <button
                        className={feedback?.rating === "HELPFUL" ? "active positive" : ""}
                        onClick={() => {
                          setReasonRunId(undefined);
                          setReason("");
                          void onFeedback(run, "HELPFUL", "");
                        }}
                        disabled={feedbackPending}
                        aria-label="回答有帮助"
                        title="回答有帮助"
                        aria-pressed={feedback?.rating === "HELPFUL"}
                      >
                        <ThumbsUp size={15} />
                      </button>
                      <button
                        className={feedback?.rating === "NEEDS_IMPROVEMENT" ? "active negative" : ""}
                        onClick={() => {
                          setReasonRunId(run.id);
                          setReason(feedback?.rating === "NEEDS_IMPROVEMENT" ? feedback.reason : "");
                        }}
                        disabled={feedbackPending}
                        aria-label="回答需改进"
                        title="回答需改进"
                        aria-pressed={feedback?.rating === "NEEDS_IMPROVEMENT"}
                      >
                        <ThumbsDown size={15} />
                      </button>
                      {feedback && (
                        <span className="feedback-recorded">
                          {feedback.rating === "HELPFUL" ? "已标记有帮助" : "已记录改进意见"}
                        </span>
                      )}
                    </div>
                    {copyErrorArtifactId === artifact?.id && (
                      <p className="message-action-error" role="status">
                        浏览器未允许复制，请选择回答文本后复制。
                      </p>
                    )}
                    {reasonRunId === run.id && (
                      <form
                        className="feedback-editor"
                        onSubmit={(event) => void submitImprovement(event, run)}
                      >
                        <label htmlFor={`feedback-${run.id}`}>哪里可以改进？</label>
                        <textarea
                          id={`feedback-${run.id}`}
                          value={reason}
                          onChange={(event) => setReason(event.target.value)}
                          minLength={3}
                          maxLength={4_000}
                          rows={3}
                          required
                          autoFocus
                          placeholder="例如：缺少关键证据、结论不够清晰，或没有回答核心问题"
                        />
                        <footer>
                          <span>{reason.length}/4000</span>
                          <button
                            type="button"
                            className="secondary-button"
                            onClick={() => { setReasonRunId(undefined); setReason(""); }}
                          >
                            取消
                          </button>
                          <button
                            type="submit"
                            className="primary-button"
                            disabled={feedbackPending || reason.trim().length < 3}
                          >
                            {feedbackPending ? "正在保存…" : "提交反馈"}
                          </button>
                        </footer>
                      </form>
                    )}
                  </>
                )}
              </div>
            </article>
          </div>
        );
      })}
    </div>
  );
}

function ArtifactOutputs({ artifacts }: { artifacts: Artifact[] }) {
  if (!artifacts.length) return null;
  return (
    <section className="result-artifacts" aria-label="运行产物">
      <header><span>可复用产物</span><small>{artifacts.length}</small></header>
      {artifacts.map((item) => (
        <details key={item.id} className={`result-artifact kind-${item.kind.toLowerCase()}`} open={item.kind === "TABLE" || item.kind === "CHART"}>
          <summary>
            <span className="result-artifact-icon">{artifactIcon(item.kind)}</span>
            <span><strong>{item.title}</strong><small>{artifactName(item.kind)} · {item.classification}</small></span>
          </summary>
          <ArtifactPreview artifact={item} />
        </details>
      ))}
    </section>
  );
}

function statusCopy(status: string) {
  if (status === "PENDING") return "正在排队…";
  if (status === "WAITING_APPROVAL") return "等待审批后继续…";
  if (status === "REPLANNING") return "正在重新规划…";
  return "正在理解问题并收集证据…";
}
