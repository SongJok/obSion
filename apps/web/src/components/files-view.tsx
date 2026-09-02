"use client";

import { Download, Files, RefreshCw, ShieldCheck, UploadCloud, X } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";

import { useWorkspaceCollection } from "@/hooks/use-workspace-collection";
import { api } from "@/lib/api";
import type { Artifact, Workspace } from "@/lib/types";
import { ArtifactPreview, artifactIcon } from "./artifact-preview";

export function FilesView({ workspace }: { workspace?: Workspace }) {
  const [selected, setSelected] = useState<Artifact>();
  const [path, setPath] = useState("");
  const [showHistory, setShowHistory] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const workspaceId = workspace?.id;
  const query = useCallback(
    () => workspaceId ? api.listWorkspaceFiles(workspaceId, showHistory) : Promise.resolve([]),
    [showHistory, workspaceId],
  );
  const scopeKey = workspaceId ? `${workspaceId}:${showHistory ? "history" : "current"}` : undefined;
  const { items: files, loading, error, refresh, reportError } = useWorkspaceCollection(
    scopeKey,
    query,
    "无法读取工作区文件",
  );

  const current = useMemo(
    () => files.filter((item) => !item.superseded_at),
    [files],
  );
  const detail = selected ? files.find((item) => item.id === selected.id) : undefined;

  const upload = async (file: File) => {
    if (!workspace) return;
    setUploading(true);
    reportError("");
    try {
      const form = new FormData();
      form.set("file", file);
      form.set("title", file.name);
      form.set("kind", "FILE");
      form.set("classification", workspace.classification || "INTERNAL");
      form.set("path", path.trim() || defaultPath(file.name));
      form.set("lineage", JSON.stringify({ source: "workspace-files", filename: file.name }));
      await api.uploadArtifact(workspace.id, form);
      refresh();
    } catch (caught) {
      reportError(caught instanceof Error ? caught.message : "文件上传失败");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const download = async (artifact: Artifact) => {
    setDownloading(true);
    reportError("");
    try {
      const blob = await api.downloadArtifact(artifact.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = String(artifact.path ?? artifact.lineage.filename ?? artifact.title);
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (caught) {
      reportError(caught instanceof Error ? caught.message : "文件下载失败");
    } finally {
      setDownloading(false);
    }
  };

  return (
    <main className="feature-page artifact-page">
      <header className="feature-header">
        <div>
          <span className="eyebrow">OBSION WORKSPACE</span>
          <h1>工作区文件</h1>
          <p>路径化、版本化的 FILE 产物。文件不会自动进入 SYSTEM 或 Skill 指令，必须作为附件进入 Evidence。</p>
        </div>
        <div className="artifact-header-actions">
          <button className="secondary-button" onClick={refresh} disabled={loading}>
            <RefreshCw size={16} className={loading ? "spin" : ""} /> 刷新
          </button>
          <button
            className="primary-button"
            onClick={() => fileRef.current?.click()}
            disabled={!workspace || uploading}
          >
            <UploadCloud size={17} /> {uploading ? "上传中…" : "上传到路径"}
          </button>
          <input
            ref={fileRef}
            hidden
            type="file"
            aria-label="上传工作区文件"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) {
                if (!path.trim()) setPath(defaultPath(file.name));
                void upload(file);
              }
            }}
          />
        </div>
      </header>

      <section className="artifact-stats" aria-label="文件摘要">
        <article><Files size={18} /><div><strong>{current.length}</strong><span>当前路径</span></div></article>
        <article><ShieldCheck size={18} /><div><strong>{files.length}</strong><span>{showHistory ? "含历史版本" : "当前账本"}</span></div></article>
      </section>

      {error && <div className="notice error"><Files size={17} /><span>{error}</span></div>}

      <section className="artifact-toolbar">
        <label className="artifact-search">
          <span>路径</span>
          <input
            value={path}
            onChange={(event) => setPath(event.target.value)}
            placeholder="/reports/incident.md"
          />
        </label>
        <div className="artifact-filters" aria-label="文件历史">
          <button className={showHistory ? "" : "active"} onClick={() => setShowHistory(false)}>仅当前</button>
          <button className={showHistory ? "active" : ""} onClick={() => setShowHistory(true)}>含历史版本</button>
        </div>
      </section>

      {loading && !files.length ? (
        <div className="artifact-center-empty"><RefreshCw className="spin" size={24} /><p>正在读取工作区文件…</p></div>
      ) : !files.length ? (
        <div className="artifact-center-empty">
          <Files size={30} />
          <h2>工作区还没有路径化文件</h2>
          <p>上传时指定绝对路径。同路径新版本会取代当前文件，旧版本可在历史中查看。</p>
        </div>
      ) : (
        <section className="artifact-library">
          <div className="artifact-grid">
            {files.map((artifact) => (
              <button
                key={artifact.id}
                className={detail?.id === artifact.id ? "artifact-card selected" : "artifact-card"}
                onClick={() => setSelected(artifact)}
              >
                <span className="artifact-card-icon kind-file">{artifactIcon(artifact.kind, 19)}</span>
                <span className="artifact-card-copy">
                  <strong>{artifact.path ?? artifact.title}</strong>
                  <small>
                    v{artifact.file_version ?? "—"}
                    {artifact.superseded_at ? " · 已取代" : " · 当前"}
                    {" · "}
                    {artifact.classification}
                  </small>
                </span>
              </button>
            ))}
          </div>
          {detail && (
            <aside className="artifact-detail" aria-label="文件详情">
              <header>
                <span className="artifact-card-icon">{artifactIcon(detail.kind, 19)}</span>
                <div>
                  <strong>{detail.path ?? detail.title}</strong>
                  <small>v{detail.file_version ?? "—"} · {detail.media_type}</small>
                </div>
                <button className="icon-button" onClick={() => setSelected(undefined)} aria-label="关闭详情">
                  <X size={17} />
                </button>
              </header>
              <div className="artifact-detail-meta">
                <span>{detail.classification}</span>
                <span>{detail.superseded_at ? "历史版本" : "当前版本"}</span>
              </div>
              <div className="artifact-detail-preview"><ArtifactPreview artifact={detail} /></div>
              <footer>
                <code>{detail.id}</code>
                {detail.storage_key && (
                  <button className="primary-button" disabled={downloading} onClick={() => void download(detail)}>
                    <Download size={15} />{downloading ? "下载中…" : "下载"}
                  </button>
                )}
              </footer>
            </aside>
          )}
        </section>
      )}
    </main>
  );
}

function defaultPath(name: string): string {
  const safe = name.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "file";
  return `/uploads/${safe}`;
}
