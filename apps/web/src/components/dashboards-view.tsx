"use client";

import { LayoutDashboard, RefreshCw, BarChart3, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";
import { useWorkspaceCollection } from "@/hooks/use-workspace-collection";
import type { Artifact, Workspace } from "@/lib/types";
import { ArtifactPreview, artifactIcon, artifactName } from "./artifact-preview";

export function DashboardsView({ workspace }: { workspace?: Workspace }) {
  const [selected, setSelected] = useState<Artifact>();
  const [panelState, setPanelState] = useState<{ dashboardId: string; items: Artifact[] }>({
    dashboardId: "",
    items: [],
  });
  const workspaceId = workspace?.id;
  const query = useCallback(
    () => workspaceId ? api.listWorkspaceDashboards(workspaceId) : Promise.resolve([]),
    [workspaceId],
  );
  const {
    items: dashboards,
    loading,
    error,
    refresh,
    reportError,
  } = useWorkspaceCollection(workspaceId, query, "无法读取工作区仪表盘");

  const detail = selected ? dashboards.find((item) => item.id === selected.id) : undefined;

  useEffect(() => {
    if (!detail) return;
    const refs = Array.isArray(detail.inline_content?.panels)
      ? detail.inline_content.panels
      : [];
    const ids = refs
      .map((panel) => (panel as { artifact_id?: string }).artifact_id)
      .filter((id): id is string => Boolean(id));
    let cancelled = false;
    void Promise.all(ids.map((id) => api.getArtifact(id)))
      .then((loaded) => {
        if (!cancelled) setPanelState({ dashboardId: detail.id, items: loaded });
      })
      .catch((caught) => {
        if (!cancelled) {
          setPanelState({ dashboardId: detail.id, items: [] });
          reportError(caught instanceof Error ? caught.message : "无法读取仪表盘面板");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [detail, reportError]);

  const panels = detail && panelState.dashboardId === detail.id ? panelState.items : [];

  const chartCount = dashboards.reduce((count, item) => {
    const ids = item.inline_content?.chart_artifact_ids;
    return count + (Array.isArray(ids) ? ids.length : 0);
  }, 0);

  return (
    <main className="feature-page artifact-page">
      <header className="feature-header">
        <div>
          <span className="eyebrow">OBSION WORKSPACE</span>
          <h1>工作区仪表盘</h1>
          <p>只组合 Harness 已发布的 CHART / TABLE / SQL。没有图表的 Run 不会生成仪表盘；这里不伪造数据系列。</p>
        </div>
        <div className="artifact-header-actions">
          <button className="secondary-button" onClick={refresh} disabled={loading}>
            <RefreshCw size={16} className={loading ? "spin" : ""} /> 刷新
          </button>
        </div>
      </header>

      <section className="artifact-stats" aria-label="仪表盘摘要">
        <article><LayoutDashboard size={18} /><div><strong>{dashboards.length}</strong><span>仪表盘</span></div></article>
        <article><BarChart3 size={18} /><div><strong>{chartCount}</strong><span>引用图表</span></div></article>
      </section>

      {error && <div className="notice error"><LayoutDashboard size={17} /><span>{error}</span></div>}

      {loading && !dashboards.length ? (
        <div className="artifact-center-empty"><RefreshCw className="spin" size={24} /><p>正在读取工作区仪表盘…</p></div>
      ) : !dashboards.length ? (
        <div className="artifact-center-empty">
          <LayoutDashboard size={30} />
          <h2>工作区还没有仪表盘</h2>
          <p>带真实 CHART 产物的数据 Run 会发布一份只引用这些产物的 DASHBOARD。寒暄和知识回答不会发明图表。</p>
        </div>
      ) : (
        <section className="artifact-library">
          <div className="artifact-grid">
            {dashboards.map((dashboard) => (
              <button
                key={dashboard.id}
                className={detail?.id === dashboard.id ? "artifact-card selected" : "artifact-card"}
                onClick={() => setSelected(dashboard)}
              >
                <span className="artifact-card-icon kind-dashboard">{artifactIcon(dashboard.kind, 19)}</span>
                <span className="artifact-card-copy">
                  <strong>{dashboard.title}</strong>
                  <small>{artifactName(dashboard.kind)} · {dashboard.classification}</small>
                </span>
                <time dateTime={dashboard.created_at}>{new Date(dashboard.created_at).toLocaleDateString("zh-CN")}</time>
              </button>
            ))}
          </div>
          {detail && (
            <aside className="artifact-detail" aria-label="仪表盘详情">
              <header>
                <span className="artifact-card-icon">{artifactIcon(detail.kind, 19)}</span>
                <div>
                  <strong>{detail.title}</strong>
                  <small>{detail.media_type}</small>
                </div>
                <button className="icon-button" onClick={() => setSelected(undefined)} aria-label="关闭详情">
                  <X size={17} />
                </button>
              </header>
              <div className="artifact-detail-meta">
                <span>{detail.classification}</span>
                <span>{detail.run_id ? "运行发布" : "人工上传"}</span>
              </div>
              <div className="artifact-detail-preview">
                {panels.length ? panels.map((panel) => (
                  <section key={panel.id} className="dashboard-panel">
                    <h3>{panel.title}</h3>
                    <ArtifactPreview artifact={panel} />
                  </section>
                )) : <ArtifactPreview artifact={detail} />}
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
