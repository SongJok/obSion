"use client";

import { FileCode, RefreshCw, ShieldCheck, X } from "lucide-react";
import { useCallback, useState } from "react";

import { useWorkspaceCollection } from "@/hooks/use-workspace-collection";
import { api } from "@/lib/api";
import type { Artifact, Workspace } from "@/lib/types";
import { ArtifactPreview, artifactIcon, artifactName } from "./artifact-preview";

export function SqlView({ workspace }: { workspace?: Workspace }) {
  const [selected, setSelected] = useState<Artifact>();
  const workspaceId = workspace?.id;
  const query = useCallback(
    () => workspaceId ? api.listWorkspaceSql(workspaceId) : Promise.resolve([]),
    [workspaceId],
  );
  const { items: statements, loading, error, refresh } = useWorkspaceCollection(
    workspaceId,
    query,
    "无法读取工作区 SQL",
  );

  const detail = selected ? statements.find((item) => item.id === selected.id) : undefined;
  const validated = statements.filter((item) => item.inline_content?.validation?.valid === true).length;

  return (
    <main className="feature-page artifact-page">
      <header className="feature-header">
        <div>
          <span className="eyebrow">OBSION WORKSPACE</span>
          <h1>工作区 SQL</h1>
          <p>只收录已发布的 SQL 产物。寒暄和知识回答不伪造仓库行；数据目录也不会在这里发明 SELECT。</p>
        </div>
        <div className="artifact-header-actions">
          <button className="secondary-button" onClick={refresh} disabled={loading}>
            <RefreshCw size={16} className={loading ? "spin" : ""} /> 刷新
          </button>
        </div>
      </header>

      <section className="artifact-stats" aria-label="SQL 摘要">
        <article><FileCode size={18} /><div><strong>{statements.length}</strong><span>SQL</span></div></article>
        <article><ShieldCheck size={18} /><div><strong>{validated}</strong><span>已校验</span></div></article>
      </section>

      {error && <div className="notice error"><FileCode size={17} /><span>{error}</span></div>}

      {loading && !statements.length ? (
        <div className="artifact-center-empty"><RefreshCw className="spin" size={24} /><p>正在读取工作区 SQL…</p></div>
      ) : !statements.length ? (
        <div className="artifact-center-empty">
          <FileCode size={30} />
          <h2>工作区还没有 SQL</h2>
          <p>受治理的数据 Run 会发布已校验 SQL。这里不执行仓库查询，也不发明 SELECT。</p>
        </div>
      ) : (
        <section className="artifact-library">
          <div className="artifact-grid">
            {statements.map((statement) => (
              <button
                key={statement.id}
                className={detail?.id === statement.id ? "artifact-card selected" : "artifact-card"}
                onClick={() => setSelected(statement)}
              >
                <span className="artifact-card-icon kind-sql">{artifactIcon(statement.kind, 19)}</span>
                <span className="artifact-card-copy">
                  <strong>{statement.title}</strong>
                  <small>{artifactName(statement.kind)} · {statement.classification}</small>
                </span>
                <time dateTime={statement.created_at}>{new Date(statement.created_at).toLocaleDateString("zh-CN")}</time>
              </button>
            ))}
          </div>
          {detail && (
            <aside className="artifact-detail" aria-label="SQL 详情">
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
