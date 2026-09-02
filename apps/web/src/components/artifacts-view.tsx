"use client";

import { Download, FileArchive, FolderKanban, RefreshCw, Search, ShieldCheck, UploadCloud, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { Artifact, Workspace } from "@/lib/types";
import { ArtifactPreview, artifactIcon, artifactName } from "./artifact-preview";

const FILTERS = [
  { id: "ALL", label: "全部", kinds: [] },
  { id: "FILE", label: "文件", kinds: ["FILE"] },
  { id: "REPORT", label: "报告与仪表盘", kinds: ["REPORT", "DASHBOARD"] },
  { id: "VISUAL", label: "表格与图表", kinds: ["TABLE", "CHART", "DIAGRAM"] },
  { id: "CODE", label: "代码与 SQL", kinds: ["CODE", "DIFF", "SQL"] },
  { id: "TEXT", label: "答案与证据", kinds: ["TEXT"] },
] as const;

export function ArtifactsView({ workspace }: { workspace?: Workspace }) {
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [selected, setSelected] = useState<Artifact>();
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["id"]>("ALL");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(Boolean(workspace));
  const [uploading, setUploading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    if (!workspace) {
      setArtifacts([]);
      return;
    }
    setLoading(true);
    setError("");
    try {
      const next = await api.listWorkspaceArtifacts(workspace.id);
      setArtifacts(next);
      setSelected((current) => current ? next.find((item) => item.id === current.id) : undefined);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取工作区产物");
    } finally {
      setLoading(false);
    }
  }, [workspace]);

  useEffect(() => {
    if (!workspace) return;
    let active = true;
    api.listWorkspaceArtifacts(workspace.id)
      .then((next) => {
        if (active) setArtifacts(next);
      })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof Error ? caught.message : "无法读取工作区产物");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [workspace]);

  const visible = useMemo(() => {
    const group = FILTERS.find((item) => item.id === filter);
    const term = query.trim().toLocaleLowerCase("zh-CN");
    return artifacts.filter((artifact) => {
      if (group?.kinds.length && !(group.kinds as readonly string[]).includes(artifact.kind)) return false;
      return !term || [artifact.title, artifact.kind, artifact.media_type, artifact.classification]
        .join(" ").toLocaleLowerCase("zh-CN").includes(term);
    });
  }, [artifacts, filter, query]);
  const detailArtifact = selected && visible.some((artifact) => artifact.id === selected.id)
    ? selected
    : undefined;

  const upload = async (file: File) => {
    if (!workspace) return;
    setUploading(true);
    setError("");
    try {
      const form = new FormData();
      form.set("file", file);
      form.set("title", file.name);
      form.set("kind", "FILE");
      form.set("classification", workspace.classification || "INTERNAL");
      form.set("lineage", JSON.stringify({ source: "workspace-artifact-center", filename: file.name }));
      const created = await api.uploadArtifact(workspace.id, form);
      setArtifacts((items) => [created, ...items]);
      setSelected(created);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "文件上传失败");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const download = async (artifact: Artifact) => {
    setDownloading(true);
    setError("");
    try {
      const blob = await api.downloadArtifact(artifact.id);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = String(artifact.lineage.filename ?? artifact.title);
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "产物下载失败");
    } finally {
      setDownloading(false);
    }
  };

  const generated = artifacts.filter((item) => Boolean(item.run_id)).length;
  const downloadable = artifacts.filter((item) => Boolean(item.storage_key)).length;
  const verified = artifacts.filter((item) => item.inline_content?.verification?.verified === true).length;

  return (
    <main className="feature-page artifact-page">
      <header className="feature-header">
        <div>
          <span className="eyebrow">OBSION WORKSPACE</span>
          <h1>产物中心</h1>
          <p>集中管理任务生成的答案、报告、图表、代码与 SQL，以及人工上传的工作文件。</p>
        </div>
        <div className="artifact-header-actions">
          <button className="secondary-button" onClick={() => void load()} disabled={loading}>
            <RefreshCw size={16} className={loading ? "spin" : ""} /> 刷新
          </button>
          <button className="primary-button" onClick={() => fileRef.current?.click()} disabled={!workspace || uploading}>
            <UploadCloud size={17} /> {uploading ? "上传中…" : "上传文件"}
          </button>
          <input ref={fileRef} hidden type="file" aria-label="上传工作区产物" onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void upload(file);
          }} />
        </div>
      </header>

      <section className="artifact-stats" aria-label="产物摘要">
        <article><FolderKanban size={18} /><div><strong>{artifacts.length}</strong><span>全部产物</span></div></article>
        <article><ShieldCheck size={18} /><div><strong>{verified}</strong><span>证据已验证</span></div></article>
        <article><FileArchive size={18} /><div><strong>{generated}</strong><span>运行生成</span></div></article>
        <article><Download size={18} /><div><strong>{downloadable}</strong><span>可下载文件</span></div></article>
      </section>

      {error && <div className="notice error"><FileArchive size={17} /><span>{error}</span></div>}

      <section className="artifact-toolbar">
        <div className="artifact-search"><Search size={17} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题、类型或密级" /></div>
        <div className="artifact-filters" aria-label="产物类型筛选">
          {FILTERS.map((item) => <button key={item.id} className={filter === item.id ? "active" : ""} onClick={() => setFilter(item.id)}>{item.label}</button>)}
        </div>
      </section>

      {loading && !artifacts.length ? (
        <div className="artifact-center-empty"><RefreshCw className="spin" size={24} /><p>正在读取受控产物目录…</p></div>
      ) : !visible.length ? (
        <div className="artifact-center-empty"><FolderKanban size={30} /><h2>{artifacts.length ? "没有匹配的产物" : "工作区还没有产物"}</h2><p>运行调查任务或上传文件后，资产会在这里长期留存。</p></div>
      ) : (
        <section className="artifact-library">
          <div className="artifact-grid">
            {visible.map((artifact) => (
              <button key={artifact.id} className={detailArtifact?.id === artifact.id ? "artifact-card selected" : "artifact-card"} onClick={() => setSelected(artifact)}>
                <span className={`artifact-card-icon kind-${artifact.kind.toLowerCase()}`}>{artifactIcon(artifact.kind, 19)}</span>
                <span className="artifact-card-copy"><strong>{artifact.title}</strong><small>{artifactName(artifact.kind)} · {artifact.classification}</small></span>
                <time dateTime={artifact.created_at}>{formatDate(artifact.created_at)}</time>
              </button>
            ))}
          </div>

          {detailArtifact && (
            <aside className="artifact-detail" aria-label="产物详情">
              <header>
                <span className="artifact-card-icon">{artifactIcon(detailArtifact.kind, 19)}</span>
                <div><strong>{detailArtifact.title}</strong><small>{artifactName(detailArtifact.kind)} · {detailArtifact.media_type}</small></div>
                <button className="icon-button" onClick={() => setSelected(undefined)} aria-label="关闭详情"><X size={17} /></button>
              </header>
              <div className="artifact-detail-meta">
                <span>{detailArtifact.classification}</span>
                <span>{detailArtifact.run_id ? "运行生成" : "人工上传"}</span>
                <time dateTime={detailArtifact.created_at}>{new Date(detailArtifact.created_at).toLocaleString("zh-CN")}</time>
              </div>
              <div className="artifact-detail-preview"><ArtifactPreview artifact={detailArtifact} /></div>
              <footer>
                <code>{detailArtifact.id}</code>
                {detailArtifact.storage_key && <button className="primary-button" disabled={downloading} onClick={() => void download(detailArtifact)}><Download size={15} />{downloading ? "下载中…" : "下载原文件"}</button>}
              </footer>
            </aside>
          )}
        </section>
      )}
    </main>
  );
}

function formatDate(value: string) {
  return new Date(value).toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
}
