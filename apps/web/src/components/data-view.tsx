"use client";

import { ArrowRight, BadgeCheck, Database, GitBranch, Search, Sigma, TableProperties, X } from "lucide-react";
import { KeyboardEvent as ReactKeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { Metric, MetricLineage } from "@/lib/types";

type DetailMode = "definition" | "lineage";

const DETAIL_MODES: readonly DetailMode[] = ["definition", "lineage"];

export function DataView() {
  const [metrics, setMetrics] = useState<Metric[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<Metric>();
  const [detailMode, setDetailMode] = useState<DetailMode>("definition");
  const [lineage, setLineage] = useState<MetricLineage>();
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const lineageGeneration = useRef(0);

  useEffect(() => {
    let cancelled = false;
    api
      .listMetrics()
      .then((next) => {
        if (!cancelled) setMetrics(next);
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "无法读取指标目录");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
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

  const showDefinition = (metric: Metric) => {
    ++lineageGeneration.current;
    setSelected(metric);
    setDetailMode("definition");
    setLineage(undefined);
    setDetailLoading(false);
    setDetailError("");
  };

  const showLineage = async (metric: Metric) => {
    const generation = ++lineageGeneration.current;
    setSelected(metric);
    setDetailMode("lineage");
    setLineage(undefined);
    setDetailLoading(true);
    setDetailError("");
    try {
      const next = await api.getMetricLineage(metric.id);
      if (generation === lineageGeneration.current) setLineage(next);
    } catch (caught) {
      if (generation === lineageGeneration.current) {
        setDetailError(caught instanceof Error ? caught.message : "无法读取指标血缘");
      }
    } finally {
      if (generation === lineageGeneration.current) setDetailLoading(false);
    }
  };

  const closeDetail = () => {
    ++lineageGeneration.current;
    setSelected(undefined);
    setLineage(undefined);
    setDetailLoading(false);
    setDetailError("");
  };

  const changeDetailMode = (nextMode: DetailMode) => {
    if (!selected) return;
    if (nextMode === "definition") showDefinition(selected);
    else void showLineage(selected);
  };

  const handleDetailKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    const currentIndex = DETAIL_MODES.indexOf(detailMode);
    let nextIndex: number | undefined;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % DETAIL_MODES.length;
    if (event.key === "ArrowLeft") {
      nextIndex = (currentIndex - 1 + DETAIL_MODES.length) % DETAIL_MODES.length;
    }
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = DETAIL_MODES.length - 1;
    if (nextIndex === undefined) return;
    event.preventDefault();
    const nextMode = DETAIL_MODES[nextIndex];
    changeDetailMode(nextMode);
    event.currentTarget.parentElement
      ?.querySelector<HTMLButtonElement>(`[data-metric-detail="${nextMode}"]`)
      ?.focus();
  };

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

      {loading ? (
        <div className="catalog-empty">
          <div><Database size={29} /></div>
          <h2>正在加载指标目录…</h2>
          <p>正在读取已验证指标与负责人信息。</p>
        </div>
      ) : !metrics.length && !error ? (
        <div className="catalog-empty">
          <div><Database size={29} /></div>
          <h2>还没有已验证指标</h2>
          <p>管理员可通过治理 API 接入只读数据源，登记表、列、指标定义与负责人。</p>
        </div>
      ) : !filtered.length ? (
        <div className="catalog-empty">
          <div><Search size={29} /></div>
          <h2>没有匹配的已验证指标</h2>
          <p>请调整关键词，或联系管理员登记新的指标定义。</p>
        </div>
      ) : (
        <div className="metric-grid">
          {filtered.map((metric) => (
            <article key={metric.id}>
              <header><span className="metric-icon"><Sigma size={18} /></span><BadgeCheck size={17} className="verified-icon" /></header>
              <h3>{metric.display_name}</h3>
              <code>{metric.name}</code>
              <div className="metric-meta"><span>v{metric.version}</span><span>{metric.owner}</span></div>
              <footer><button type="button" onClick={() => void showLineage(metric)}><GitBranch size={15} /> 查看血缘</button><button type="button" onClick={() => showDefinition(metric)}><TableProperties size={15} /> 查看定义</button></footer>
            </article>
          ))}
        </div>
      )}

      {selected && (
        <div className="modal-backdrop" role="presentation">
          <section className="workspace-modal metric-detail-modal" role="dialog" aria-modal="true" aria-label={`${selected.display_name} 指标详情`}>
            <header>
              <span className="modal-icon">{detailMode === "lineage" ? <GitBranch size={19} /> : <TableProperties size={19} />}</span>
              <div><h2>{selected.display_name}</h2><p>{selected.name} · v{selected.version} · {selected.owner}</p></div>
              <button type="button" className="icon-button" onClick={closeDetail} aria-label="关闭指标详情"><X size={18} /></button>
            </header>
            <div className="metric-detail-tabs" role="tablist" aria-label="指标详情页签">
              <button type="button" role="tab" id="metric-detail-definition" aria-controls="metric-detail-panel" aria-selected={detailMode === "definition"} tabIndex={detailMode === "definition" ? 0 : -1} data-metric-detail="definition" className={detailMode === "definition" ? "active" : ""} onKeyDown={handleDetailKeyDown} onClick={() => changeDetailMode("definition")}>指标定义</button>
              <button type="button" role="tab" id="metric-detail-lineage" aria-controls="metric-detail-panel" aria-selected={detailMode === "lineage"} tabIndex={detailMode === "lineage" ? 0 : -1} data-metric-detail="lineage" className={detailMode === "lineage" ? "active" : ""} onKeyDown={handleDetailKeyDown} onClick={() => changeDetailMode("lineage")}>数据血缘</button>
            </div>
            <div role="tabpanel" id="metric-detail-panel" aria-labelledby={`metric-detail-${detailMode}`}>
            {detailMode === "definition" ? (
              <div className="metric-definition">
                <dl>
                  <div><dt>表达式</dt><dd><code>{selected.expression}</code></dd></div>
                  <div><dt>时间列</dt><dd><code>{selected.time_column}</code></dd></div>
                  <div><dt>负责人</dt><dd>{selected.owner}</dd></div>
                  <div><dt>验证状态</dt><dd>{selected.validated ? "已验证" : "未验证"}</dd></div>
                  <div><dt>同义词</dt><dd>{selected.synonyms.length ? selected.synonyms.join("、") : "—"}</dd></div>
                  <div><dt>固定筛选</dt><dd><code>{JSON.stringify(selected.filters)}</code></dd></div>
                </dl>
              </div>
            ) : detailLoading ? (
              <div className="metric-detail-loading"><i /><span>正在解析受控血缘…</span></div>
            ) : detailError ? (
              <div className="notice error"><Database size={16} />{detailError}</div>
            ) : lineage && (
              <div className="metric-lineage-flow" aria-label="指标数据血缘">
                <LineageNode icon={<Database size={17} />} label="只读数据源" name={lineage.data_source.name} meta={`${lineage.data_source.environment} · ${lineage.data_source.read_only ? "只读" : "非只读"}`} />
                <ArrowRight size={18} />
                <LineageNode icon={<TableProperties size={17} />} label="来源表" name={lineage.table.name} meta={`负责人 ${lineage.table.owner}`} />
                <ArrowRight size={18} />
                <LineageNode icon={<Sigma size={17} />} label="业务指标" name={lineage.metric.name} meta={`版本 ${lineage.metric.version}`} />
              </div>
            )}
            </div>
          </section>
        </div>
      )}
    </main>
  );
}

function LineageNode({ icon, label, name, meta }: { icon: React.ReactNode; label: string; name: string; meta: string }) {
  return <article><span>{icon}</span><small>{label}</small><strong>{name}</strong><p>{meta}</p></article>;
}
