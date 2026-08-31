"use client";

import { FileChartColumn, RefreshCw, ShieldCheck, X } from "lucide-react";
import { useCallback, useState } from "react";

import { useWorkspaceCollection } from "@/hooks/use-workspace-collection";
import { api } from "@/lib/api";
import type { Artifact, Workspace } from "@/lib/types";
import { ArtifactPreview, artifactIcon, artifactName } from "./artifact-preview";

export function ReportsView({ workspace }: { workspace?: Workspace }) {
  const [selected, setSelected] = useState<Artifact>();
  const workspaceId = workspace?.id;
  const query = useCallback(
    () => workspaceId ? api.listWorkspaceReports(workspaceId) : Promise.resolve([]),
    [workspaceId],
  );
  const { items: reports, loading, error, refresh } = useWorkspaceCollection(
    workspaceId,
    query,
    "无法读取工作区报告",
  );

  const detail = selected ? reports.find((item) => item.id === selected.id) : undefined;
  const verified = reports.filter((item) => item.inline_content?.verification?.verified === true).length;

  return (
    <main className="feature-page artifact-page">
      <header className="feature-header">
        <div>
          <span className="eyebrow">OBSION WORKSPACE</span>
          <h1>工作区报告</h1>
          <p>仅收录 Harness 发布的 REPORT 产物。对话寒暄不会生成报告；报告不是 SYSTEM 指令，也不伪造仪表盘。</p>
        </div>
        <div className="artifact-header-actions">
          <button className="secondary-button" onClick={refresh} disabled={loading}>
            <RefreshCw size={16} className={loading ? "spin" : ""} /> 刷新
          </button>
        </div>
      </header>

      <section className="artifact-stats" aria-label="报告摘要">
        <article><FileChartColumn size={18} /><div><strong>{reports.length}</strong><span>报告</span></div></article>
        <article><ShieldCheck size={18} /><div><strong>{verified}</strong><span>Critic 已验证</span></div></article>
      </section>

      {error && <div className="notice error"><FileChartColumn size={17} /><span>{error}</span></div>}

      {loading && !reports.length ? (
        <div className="artifact-center-empty"><RefreshCw className="spin" size={24} /><p>正在读取工作区报告…</p></div>
      ) : !reports.length ? (
        <div className="artifact-center-empty">
          <FileChartColumn size={30} />
          <h2>工作区还没有报告</h2>
          <p>带引用、数据表或事故融合的已完成 Run 会发布 REPORT。工程调查已有的证据报告也会出现在这里。</p>
        </div>
      ) : (
        <section className="artifact-library">
          <div className="artifact-grid">
            {reports.map((report) => (
              <button
                key={report.id}
                className={detail?.id === report.id ? "artifact-card selected" : "artifact-card"}
                onClick={() => setSelected(report)}
              >
                <span className="artifact-card-icon kind-report">{artifactIcon(report.kind, 19)}</span>
                <span className="artifact-card-copy">
                  <strong>{report.title}</strong>
                  <small>{artifactName(report.kind)} · {report.classification}</small>
                </span>
                <time dateTime={report.created_at}>{new Date(report.created_at).toLocaleDateString("zh-CN")}</time>
              </button>
            ))}
          </div>
          {detail && (
            <aside className="artifact-detail" aria-label="报告详情">
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
              <div className="artifact-detail-preview"><ArtifactPreview artifact={detail} /></div>
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
