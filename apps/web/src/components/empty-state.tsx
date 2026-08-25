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
      <h1>今天想调查什么？</h1>
      <p>一个入口连接知识、数据、代码与可观测性。每个结论都保留证据和执行轨迹。</p>
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
    </div>
  );
}
