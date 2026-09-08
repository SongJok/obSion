"use client";

import { CircleHelp, Clock3, LoaderCircle, Square } from "lucide-react";
import { FormEvent, useMemo, useState } from "react";

import type {
  ClarificationAnswer,
  ClarificationAnswerSubmission,
  ClarificationGap,
  PendingClarification,
} from "@/lib/types";

interface ClarificationFormProps {
  clarification: PendingClarification;
  submitting: boolean;
  error?: string;
  onSubmit: (submission: ClarificationAnswerSubmission) => void;
  onCancel: () => void;
}

type AnswerDraft =
  | { kind: "OPTION"; optionId: string }
  | { kind: "VALUE"; text: string; start: string; end: string };

export function ClarificationForm({
  clarification,
  submitting,
  error,
  onSubmit,
  onCancel,
}: ClarificationFormProps) {
  const [answers, setAnswers] = useState<Record<string, AnswerDraft>>({});
  const formId = `clarification-${clarification.id}`;
  const complete = useMemo(
    () => clarification.gaps.every((gap) => isComplete(gap, answers[gap.slot])),
    [answers, clarification.gaps],
  );

  const chooseOption = (slot: string, optionId: string) => {
    setAnswers((current) => ({
      ...current,
      [slot]: { kind: "OPTION", optionId },
    }));
  };

  const updateText = (slot: string, text: string) => {
    setAnswers((current) => ({
      ...current,
      [slot]: {
        kind: "VALUE",
        text,
        start: current[slot]?.kind === "VALUE" ? current[slot].start : "",
        end: current[slot]?.kind === "VALUE" ? current[slot].end : "",
      },
    }));
  };

  const updateTimeRange = (slot: string, boundary: "start" | "end", value: string) => {
    setAnswers((current) => {
      const previous = current[slot]?.kind === "VALUE"
        ? current[slot]
        : { kind: "VALUE" as const, text: "", start: "", end: "" };
      return {
        ...current,
        [slot]: { ...previous, [boundary]: value },
      };
    });
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!complete || submitting) return;
    const submittedAnswers: ClarificationAnswer[] = clarification.gaps.map((gap) => {
      const draft = answers[gap.slot];
      if (draft?.kind === "OPTION") {
        return { slot: gap.slot, option_id: draft.optionId };
      }
      if (gap.value_type === "TIME_RANGE" && draft?.kind === "VALUE") {
        const timezone = resolvedTimezone();
        return {
          slot: gap.slot,
          value: {
            start: new Date(draft.start).toISOString(),
            end: new Date(draft.end).toISOString(),
            ...(timezone ? { timezone } : {}),
          },
        };
      }
      return { slot: gap.slot, value: draft?.kind === "VALUE" ? draft.text.trim() : "" };
    });
    onSubmit({
      expected_intent_revision: clarification.intent_revision,
      answers: submittedAnswers,
    });
  };

  return (
    <section
      className="clarification-shell"
      aria-labelledby={`${formId}-title`}
      aria-describedby={`${formId}-summary`}
    >
      <header className="clarification-header">
        <span className="clarification-icon" aria-hidden="true"><CircleHelp size={18} /></span>
        <div>
          <span className="clarification-eyebrow">需要确认 · 第 {clarification.round} 轮</span>
          <h2 id={`${formId}-title`}>补充信息后继续运行</h2>
          <p id={`${formId}-summary`}>{clarification.question}</p>
        </div>
        <time dateTime={clarification.expires_at} title={clarification.expires_at}>
          <Clock3 size={13} aria-hidden="true" />
          {formatDeadline(clarification.expires_at)} 前有效
        </time>
      </header>

      <form onSubmit={submit} aria-busy={submitting}>
        <div className="clarification-fields">
          {clarification.gaps.map((gap, index) => {
            const draft = answers[gap.slot];
            const hasChoice = gap.options.length > 0;
            const freeTextSelected = draft?.kind === "VALUE";
            const groupName = `${formId}-${gap.slot}`;
            const fieldHintId = `${groupName}-hint`;
            const rangeInvalid = gap.value_type === "TIME_RANGE"
              && draft?.kind === "VALUE"
              && Boolean(draft.start && draft.end)
              && Date.parse(draft.start) >= Date.parse(draft.end);
            return (
              <fieldset key={gap.slot}>
                <legend><span aria-hidden="true">{index + 1}</span>{gap.prompt}</legend>
                <p id={fieldHintId} className="clarification-field-hint">
                  {gap.allow_free_text
                    ? "请选择一个已识别选项，或自行填写明确值。"
                    : "请选择一个已识别选项。"}
                </p>
                {hasChoice && (
                  <div className="clarification-options" role="radiogroup" aria-describedby={fieldHintId}>
                    {gap.options.map((option) => (
                      <label key={option.id}>
                        <input
                          type="radio"
                          name={groupName}
                          value={option.id}
                          checked={draft?.kind === "OPTION" && draft.optionId === option.id}
                          onChange={() => chooseOption(gap.slot, option.id)}
                          required
                        />
                        <span>{option.label}</span>
                      </label>
                    ))}
                    {gap.allow_free_text && (
                      <label>
                        <input
                          type="radio"
                          name={groupName}
                          value="free-text"
                          checked={freeTextSelected}
                          onChange={() => setAnswers((current) => ({
                            ...current,
                            [gap.slot]: { kind: "VALUE", text: "", start: "", end: "" },
                          }))}
                          required
                        />
                        <span>自行填写</span>
                      </label>
                    )}
                  </div>
                )}
                {gap.allow_free_text && gap.value_type === "STRING" && (
                  <label className="clarification-free-text" htmlFor={`${groupName}-value`}>
                    <span>具体内容</span>
                    <input
                      id={`${groupName}-value`}
                      type="text"
                      value={draft?.kind === "VALUE" ? draft.text : ""}
                      onFocus={() => {
                        if (!freeTextSelected) updateText(gap.slot, "");
                      }}
                      onChange={(event) => updateText(gap.slot, event.target.value)}
                      required={freeTextSelected || !hasChoice}
                      maxLength={4_000}
                      placeholder="输入可被准确识别的名称或标识"
                      aria-describedby={fieldHintId}
                    />
                  </label>
                )}
                {gap.allow_free_text && gap.value_type === "TIME_RANGE" && (
                  <div className="clarification-time-range" aria-describedby={fieldHintId}>
                    <label htmlFor={`${groupName}-start`}>
                      <span>开始时间</span>
                      <input
                        id={`${groupName}-start`}
                        type="datetime-local"
                        value={draft?.kind === "VALUE" ? draft.start : ""}
                        onFocus={() => {
                          if (!freeTextSelected) updateTimeRange(gap.slot, "start", "");
                        }}
                        onChange={(event) => updateTimeRange(gap.slot, "start", event.target.value)}
                        required={freeTextSelected || !hasChoice}
                      />
                    </label>
                    <label htmlFor={`${groupName}-end`}>
                      <span>结束时间</span>
                      <input
                        id={`${groupName}-end`}
                        type="datetime-local"
                        value={draft?.kind === "VALUE" ? draft.end : ""}
                        onFocus={() => {
                          if (!freeTextSelected) updateTimeRange(gap.slot, "end", "");
                        }}
                        onChange={(event) => updateTimeRange(gap.slot, "end", event.target.value)}
                        required={freeTextSelected || !hasChoice}
                        aria-invalid={rangeInvalid}
                        aria-describedby={rangeInvalid ? `${groupName}-range-error` : fieldHintId}
                      />
                    </label>
                    {rangeInvalid && (
                      <p id={`${groupName}-range-error`} className="clarification-field-error">
                        结束时间必须晚于开始时间。
                      </p>
                    )}
                  </div>
                )}
              </fieldset>
            );
          })}
        </div>

        {error && <p className="clarification-error" role="alert">{error}</p>}
        <footer>
          <p>答案仅用于恢复当前 Run，不会创建新的对话轮次。</p>
          <button
            type="button"
            className="secondary-button clarification-cancel"
            onClick={onCancel}
            disabled={submitting}
          >
            <Square size={12} fill="currentColor" aria-hidden="true" />
            停止本次运行
          </button>
          <button type="submit" className="primary-button" disabled={!complete || submitting}>
            {submitting ? <><LoaderCircle className="spin" size={14} /> 正在继续…</> : "提交并继续"}
          </button>
        </footer>
      </form>
    </section>
  );
}

function isComplete(gap: ClarificationGap, draft: AnswerDraft | undefined) {
  if (draft?.kind === "OPTION") {
    return gap.options.some((option) => option.id === draft.optionId);
  }
  if (!gap.allow_free_text || draft?.kind !== "VALUE") return false;
  if (gap.value_type === "STRING") return Boolean(draft.text.trim());
  if (!draft.start || !draft.end) return false;
  const start = Date.parse(draft.start);
  const end = Date.parse(draft.end);
  return Number.isFinite(start) && Number.isFinite(end) && start < end;
}

function formatDeadline(value: string) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "当前请求";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(parsed);
}

function resolvedTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return "";
  }
}
