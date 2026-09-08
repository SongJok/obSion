"use client";

import { BarChart3, BookOpen, GitCompareArrows, Sparkles } from "lucide-react";

const suggestions = [
  {
    icon: BookOpen,
    title: "理解业务与制度",
    text: "公司的指标治理原则是什么？",
    tone: "violet",
  },
  {
    icon: BarChart3,
    title: "分析业务指标",
    text: "最近 30 天新用户付费率有什么变化？",
    tone: "blue",
  },
  {
    icon: GitCompareArrows,
    title: "调查线上异常",
    text: "昨天发布后为什么支付延迟升高？",
    tone: "amber",
  },
];

export function EmptyState({ onSuggestion }: { onSuggestion: (value: string) => void }) {
  return (
    <div className="empty-state">
      <div className="assistant-orb" aria-hidden="true">
        <Sparkles size={25} />
      </div>
      <p className="welcome-eyebrow">你的企业 AI 工作伙伴</p>
      <h1>从一个问题，开始今天的工作</h1>
      <p>查知识、看数据、定位问题。描述你的目标，让 Obsion 帮你梳理思路与证据。</p>
      <div className="suggestions">
        {suggestions.map((suggestion) => {
          const Icon = suggestion.icon;
          return (
            <button key={suggestion.title} onClick={() => onSuggestion(suggestion.text)}>
              <span className={`suggestion-icon ${suggestion.tone}`}>
                <Icon size={18} />
              </span>
              <span>
                <strong>{suggestion.title}</strong>
                <small>{suggestion.text}</small>
              </span>
            </button>
          );
        })}
      </div>
      <p className="welcome-hint">选择一个示例填入输入框，修改后再发送；也可以直接描述你的需求。</p>
    </div>
  );
}
