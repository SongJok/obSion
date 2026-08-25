"use client";

import { Check, CircleAlert, Copy, FileText, RotateCcw, ShieldCheck, ThumbsDown, ThumbsUp } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Artifact, MessageBundle } from "@/lib/types";
import { ArtifactPreview, artifactIcon, artifactName } from "./artifact-preview";

export function Conversation({ messages }: { messages: MessageBundle[] }) {
  return (
    <div className="conversation" aria-live="polite">
      {messages.map(({ turn, run, artifact, artifacts }) => (
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
              {artifact?.inline_content?.markdown && (
                <>
                  <div className="markdown-content">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {artifact.inline_content.markdown}
                    </ReactMarkdown>
                  </div>
                  {artifact.inline_content.verification && (
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
                  <div className="message-actions">
                    <button aria-label="复制回答" title="复制回答">
                      <Copy size={15} />
                    </button>
                    <button aria-label="重新运行" title="重新运行">
                      <RotateCcw size={15} />
                    </button>
                    <button aria-label="回答有帮助" title="回答有帮助">
                      <ThumbsUp size={15} />
                    </button>
                    <button aria-label="回答需改进" title="回答需改进">
                      <ThumbsDown size={15} />
                    </button>
                  </div>
                </>
              )}
              {artifact && !artifact.inline_content?.markdown && (
                <div className="artifact-link">
                  <FileText size={18} />
                  <span>{artifact.title}</span>
                  <Check size={15} />
                </div>
              )}
            </div>
          </article>
        </div>
      ))}
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
