"use client";

import { FileCheck2, RefreshCw, ShieldCheck, X } from "lucide-react";
import { useCallback, useState } from "react";

import { useWorkspaceCollection } from "@/hooks/use-workspace-collection";
import { api } from "@/lib/api";
import type { Evidence, Workspace } from "@/lib/types";

export function EvidenceView({ workspace }: { workspace?: Workspace }) {
  const [selected, setSelected] = useState<Evidence>();
  const workspaceId = workspace?.id;
  const query = useCallback(
    () => workspaceId ? api.listWorkspaceEvidence(workspaceId) : Promise.resolve([]),
    [workspaceId],
  );
  const { items, loading, error, refresh } = useWorkspaceCollection(
    workspaceId,
    query,
    "无法读取工作区证据",
  );

  const detail = selected ? items.find((item) => item.id === selected.id) : undefined;
  const types = new Set(items.map((item) => item.evidence_type)).size;

  return (
    <main className="feature-page artifact-page">
      <header className="feature-header">
        <div>
          <span className="eyebrow">OBSION WORKSPACE</span>
          <h1>工作区证据</h1>
          <p>只收录 Harness 已持久化的 Evidence 行。寒暄不会产生证据；这里不伪造证据。</p>
        </div>
        <div className="artifact-header-actions">
          <button className="secondary-button" onClick={refresh} disabled={loading}>
            <RefreshCw size={16} className={loading ? "spin" : ""} /> 刷新
          </button>
        </div>
      </header>

      <section className="artifact-stats" aria-label="证据摘要">
        <article><FileCheck2 size={18} /><div><strong>{items.length}</strong><span>证据</span></div></article>
        <article><ShieldCheck size={18} /><div><strong>{types}</strong><span>类型</span></div></article>
      </section>

      {error && <div className="notice error"><FileCheck2 size={17} /><span>{error}</span></div>}

      {loading && !items.length ? (
        <div className="artifact-center-empty"><RefreshCw className="spin" size={24} /><p>正在读取工作区证据…</p></div>
      ) : !items.length ? (
        <div className="artifact-center-empty">
          <FileCheck2 size={30} />
          <h2>工作区还没有证据</h2>
          <p>知识检索、数据查询或工程调查会写入 Evidence。运行时检查器仍按 Run 查看同一行，不会另造一份。</p>
        </div>
      ) : (
        <section className="artifact-library">
          <div className="artifact-grid">
            {items.map((item) => (
              <button
                key={item.id}
                className={detail?.id === item.id ? "artifact-card selected" : "artifact-card"}
                onClick={() => setSelected(item)}
              >
                <span className={`artifact-card-icon type-${item.evidence_type.toLowerCase()}`}>
                  {item.evidence_type.slice(0, 1)}
                </span>
                <span className="artifact-card-copy">
                  <strong>{item.source}</strong>
                  <small>{item.evidence_type} · {item.classification}</small>
                </span>
                <time dateTime={item.observed_at}>{new Date(item.observed_at).toLocaleDateString("zh-CN")}</time>
              </button>
            ))}
          </div>
          {detail && (
            <aside className="artifact-detail" aria-label="证据详情">
              <header>
                <span className="artifact-card-icon">{detail.evidence_type.slice(0, 1)}</span>
                <div>
                  <strong>{detail.source}</strong>
                  <small>{detail.resource}</small>
                </div>
                <button className="icon-button" onClick={() => setSelected(undefined)} aria-label="关闭详情">
                  <X size={17} />
                </button>
              </header>
              <div className="artifact-detail-meta">
                <span>{detail.classification}</span>
                <span>{detail.run_id ? "运行写入" : "无 Run"}</span>
              </div>
              <div className="artifact-detail-preview">
                <pre className="sql-preview">{JSON.stringify(detail.content, null, 2)}</pre>
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
