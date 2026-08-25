"use client";

import { BadgeCheck, Database, GitBranch, Search, Sigma, TableProperties } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import type { Metric } from "@/lib/types";

export function DataView() {
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api.listMetrics().then(setMetrics).catch((caught: unknown) => {
      setError(caught instanceof Error ? caught.message : "无法读取指标目录");
    });
  }, []);

  const filtered = useMemo(() => {
    const term = query.trim().toLowerCase();
    if (!term) return metrics;
    return metrics.filter((metric) =>
      [metric.name, metric.display_name, metric.owner, ...metric.synonyms]
        .join(" ")
        .toLowerCase()
        .includes(term),
    );
  }, [metrics, query]);

  return (
    <main className="feature-page">
      <header className="feature-header">
        <div>
          <span className="eyebrow">OBSION DATA</span>
          <h1>语义数据目录</h1>
          <p>把业务问题解析为已验证指标与逻辑计划，再通过只读 Query Gateway 执行。</p>
        </div>
        <span className="catalog-count"><Sigma size={17} /> {metrics.length} 个已验证指标</span>
      </header>

      <div className="feature-search static">
        <Search size={19} />
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="按指标、同义词或负责人搜索" />
      </div>
      {error && <div className="notice error"><Database size={17} />{error}</div>}

      {!metrics.length && !error ? (
        <div className="catalog-empty">
          <div><Database size={29} /></div>
          <h2>还没有已验证指标</h2>
          <p>管理员可通过治理 API 接入只读数据源，登记表、列、指标定义与负责人。</p>
        </div>
      ) : (
        <div className="metric-grid">
          {filtered.map((metric) => (
            <article key={metric.id}>
              <header><span className="metric-icon"><Sigma size={18} /></span><BadgeCheck size={17} className="verified-icon" /></header>
              <h3>{metric.display_name}</h3>
              <code>{metric.name}</code>
              <div className="metric-meta"><span>v{metric.version}</span><span>{metric.owner}</span></div>
              <footer><button><GitBranch size={15} /> 查看血缘</button><button><TableProperties size={15} /> 查看定义</button></footer>
            </article>
          ))}
        </div>
      )}
    </main>
  );
}
