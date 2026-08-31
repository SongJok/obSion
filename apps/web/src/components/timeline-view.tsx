"use client";

import { History, RefreshCw, ShieldCheck, X } from "lucide-react";
import { useCallback, useState } from "react";

import { useWorkspaceCollection } from "@/hooks/use-workspace-collection";
import { api } from "@/lib/api";
import type { RunEvent, Workspace } from "@/lib/types";

export function TimelineView({ workspace }: { workspace?: Workspace }) {
  const [selected, setSelected] = useState<RunEvent>();
  const workspaceId = workspace?.id;
  const query = useCallback(
    () => workspaceId ? api.listWorkspaceTimeline(workspaceId) : Promise.resolve([]),
    [workspaceId],
  );
  const { items: events, loading, error, refresh } = useWorkspaceCollection(
    workspaceId,
    query,
    "无法读取运行时间线",
  );

  const detail = selected ? events.find((item) => item.id === selected.id) : undefined;
  const runs = new Set(events.map((item) => item.run_id).filter(Boolean)).size;

  return (
    <main className="feature-page artifact-page">
      <header className="feature-header">
        <div>
          <span className="eyebrow">OBSION WORKSPACE</span>
          <h1>运行时间线</h1>
          <p>只收录已持久化的 Run Event。这里不伪造时间线，也不把 Kafka 或直方图当成步骤。</p>
        </div>
        <div className="artifact-header-actions">
          <button className="secondary-button" onClick={refresh} disabled={loading}>
            <RefreshCw size={16} className={loading ? "spin" : ""} /> 刷新
          </button>
        </div>
      </header>

      <section className="artifact-stats" aria-label="时间线摘要">
        <article><History size={18} /><div><strong>{events.length}</strong><span>事件</span></div></article>
        <article><ShieldCheck size={18} /><div><strong>{runs}</strong><span>运行</span></div></article>
      </section>

      {error && <div className="notice error"><History size={17} /><span>{error}</span></div>}

      {loading && !events.length ? (
        <div className="artifact-center-empty"><RefreshCw className="spin" size={24} /><p>正在读取运行时间线…</p></div>
      ) : !events.length ? (
        <div className="artifact-center-empty">
          <History size={30} />
          <h2>工作区还没有运行事件</h2>
          <p>Harness 循环写入的 Event 会出现在这里。运行时检查器仍按 Run 查看同一行。</p>
        </div>
      ) : (
        <section className="artifact-library">
          <div className="artifact-grid">
            {events.map((item) => (
              <button
                key={item.id}
                className={detail?.id === item.id ? "artifact-card selected" : "artifact-card"}
                onClick={() => setSelected(item)}
              >
                <span className="artifact-card-icon">{item.run_sequence ?? item.sequence}</span>
                <span className="artifact-card-copy">
                  <strong>{item.name}</strong>
                  <small>{item.aggregate_type} · {item.classification}</small>
                </span>
                <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString("zh-CN")}</time>
              </button>
            ))}
          </div>
          {detail && (
            <aside className="artifact-detail" aria-label="事件详情">
              <header>
                <span className="artifact-card-icon">{detail.run_sequence ?? detail.sequence}</span>
                <div>
                  <strong>{detail.name}</strong>
                  <small>{detail.run_id ?? detail.aggregate_id}</small>
                </div>
                <button className="icon-button" onClick={() => setSelected(undefined)} aria-label="关闭详情">
                  <X size={17} />
                </button>
              </header>
              <div className="artifact-detail-meta">
                <span>{detail.actor_type}</span>
                <span>{detail.correlation_id}</span>
              </div>
              <div className="artifact-detail-preview">
                <pre className="sql-preview">{JSON.stringify(detail.payload, null, 2)}</pre>
              </div>
              <footer>
                <code>{detail.id}</code>
              </footer>
            </aside>
          )}
        </section>
      )}
    </main>
  );
}
