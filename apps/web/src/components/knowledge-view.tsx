"use client";

import { BookOpen, File, FileCheck2, Search, ShieldCheck, UploadCloud } from "lucide-react";
import { FormEvent, useRef, useState } from "react";

import { api } from "@/lib/api";
import { KnowledgeSearchHit } from "@/lib/knowledge-citation";
import { KnowledgeProvenance } from "./knowledge-provenance";

export function KnowledgeView() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<KnowledgeSearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [uploadMessage, setUploadMessage] = useState("");
  const [uploading, setUploading] = useState(false);
  const [feishuToken, setFeishuToken] = useState("");
  const [ingestingFeishu, setIngestingFeishu] = useState(false);
  const [feishuSpaceId, setFeishuSpaceId] = useState("");
  const [syncingFeishuSpace, setSyncingFeishuSpace] = useState(false);
  const [confluencePageId, setConfluencePageId] = useState("");
  const [ingestingConfluence, setIngestingConfluence] = useState(false);
  const [dingtalkDocId, setDingtalkDocId] = useState("");
  const [ingestingDingTalk, setIngestingDingTalk] = useState(false);
  const [wecomDocId, setWecomDocId] = useState("");
  const [ingestingWeCom, setIngestingWeCom] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const search = async (event: FormEvent) => {
    event.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    setError("");
    try {
      setResults(await api.knowledgeSearch(query));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "检索失败");
    } finally {
      setSearching(false);
    }
  };

  const upload = async (file: File) => {
    if (uploading) return;
    setUploading(true);
    const form = new FormData();
    form.set("file", file);
    form.set("source", "workbench-upload");
    form.set("external_id", `${file.name}:${file.lastModified}`);
    form.set("title", file.name);
    form.set("classification", "INTERNAL");
    form.set("acl", JSON.stringify({ organization: true }));
    setUploadMessage("正在解析并建立权限索引…");
    setError("");
    try {
      const result = await api.uploadDocument(form);
      setUploadMessage(`已摄取 ${result.document.title}，生成 ${result.chunk_count} 个结构化片段`);
    } catch (caught) {
      setUploadMessage("");
      setError(caught instanceof Error ? caught.message : "上传失败");
    } finally {
      setUploading(false);
      // 允许同名文件在处理完成后再次选择上传。
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <main className="feature-page">
      <header className="feature-header">
        <div>
          <span className="eyebrow">OBSION KNOWLEDGE</span>
          <h1>企业知识</h1>
          <p>文档权限在分块和检索阶段继承。飞书、钉钉、企微与 Confluence 云文档经 Capability Gateway 进入同一条 Knowledge Pipeline，检索结果与回答引用展示连接器溯源，不编造缺失字段。</p>
        </div>
        <button className="primary-button" onClick={() => fileRef.current?.click()} disabled={uploading}>
          <UploadCloud size={17} /> {uploading ? "正在上传…" : "上传文档"}
        </button>
        <input
          ref={fileRef}
          hidden
          type="file"
          accept=".pdf,.docx,.xlsx,.md,.txt,.html"
          disabled={uploading}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void upload(file);
          }}
        />
      </header>

      <form
        className="feature-search"
        onSubmit={async (event) => {
          event.preventDefault();
          if (!feishuToken.trim()) return;
          setIngestingFeishu(true);
          setError("");
          setUploadMessage("正在经 Gateway 拉取飞书文档并建立权限索引…");
          try {
            const result = await api.ingestFeishuDocument({ document_id: feishuToken.trim() });
            setUploadMessage(
              `已摄取飞书文档 ${result.document.title}，生成 ${result.chunk_count} 个结构化片段`,
            );
            setFeishuToken("");
          } catch (caught) {
            setUploadMessage("");
            setError(caught instanceof Error ? caught.message : "飞书文档摄取失败");
          } finally {
            setIngestingFeishu(false);
          }
        }}
      >
        <BookOpen size={19} />
        <input
          value={feishuToken}
          onChange={(event) => setFeishuToken(event.target.value)}
          placeholder="飞书文档或知识库节点 token"
        />
        <button disabled={ingestingFeishu || !feishuToken.trim()}>
          {ingestingFeishu ? "摄取中" : "摄取飞书文档"}
        </button>
      </form>

      <form
        className="feature-search"
        onSubmit={async (event) => {
          event.preventDefault();
          if (!dingtalkDocId.trim()) return;
          setIngestingDingTalk(true);
          setError("");
          setUploadMessage("正在经 Gateway 拉取钉钉文档并建立权限索引…");
          try {
            const result = await api.ingestDingTalkDocument({
              document_id: dingtalkDocId.trim(),
            });
            setUploadMessage(
              `已摄取钉钉文档 ${result.document.title}，生成 ${result.chunk_count} 个结构化片段`,
            );
            setDingtalkDocId("");
          } catch (caught) {
            setUploadMessage("");
            setError(caught instanceof Error ? caught.message : "钉钉文档摄取失败");
          } finally {
            setIngestingDingTalk(false);
          }
        }}
      >
        <BookOpen size={19} />
        <input
          value={dingtalkDocId}
          onChange={(event) => setDingtalkDocId(event.target.value)}
          placeholder="钉钉文档 document id"
        />
        <button disabled={ingestingDingTalk || !dingtalkDocId.trim()}>
          {ingestingDingTalk ? "摄取中" : "摄取钉钉文档"}
        </button>
      </form>

      <form
        className="feature-search"
        onSubmit={async (event) => {
          event.preventDefault();
          if (!wecomDocId.trim()) return;
          setIngestingWeCom(true);
          setError("");
          setUploadMessage("正在经 Gateway 拉取企微文档并建立权限索引…");
          try {
            const result = await api.ingestWeComDocument({
              document_id: wecomDocId.trim(),
            });
            setUploadMessage(
              `已摄取企微文档 ${result.document.title}，生成 ${result.chunk_count} 个结构化片段`,
            );
            setWecomDocId("");
          } catch (caught) {
            setUploadMessage("");
            setError(caught instanceof Error ? caught.message : "企微文档摄取失败");
          } finally {
            setIngestingWeCom(false);
          }
        }}
      >
        <BookOpen size={19} />
        <input
          value={wecomDocId}
          onChange={(event) => setWecomDocId(event.target.value)}
          placeholder="企微文档 docid"
        />
        <button disabled={ingestingWeCom || !wecomDocId.trim()}>
          {ingestingWeCom ? "摄取中" : "摄取企微文档"}
        </button>
      </form>

      <form
        className="feature-search"
        onSubmit={async (event) => {
          event.preventDefault();
          if (!feishuSpaceId.trim()) return;
          setSyncingFeishuSpace(true);
          setError("");
          setUploadMessage("正在经 Gateway 同步飞书知识库空间并建立权限索引…");
          try {
            const result = await api.syncFeishuSpace(feishuSpaceId.trim());
            setUploadMessage(
              `已同步飞书知识库 ${result.space_id}：摄取 ${result.ingested_count}，跳过 ${result.skipped_count}，失败 ${result.failed_count}`,
            );
            setFeishuSpaceId("");
          } catch (caught) {
            setUploadMessage("");
            setError(caught instanceof Error ? caught.message : "飞书知识库同步失败");
          } finally {
            setSyncingFeishuSpace(false);
          }
        }}
      >
        <BookOpen size={19} />
        <input
          value={feishuSpaceId}
          onChange={(event) => setFeishuSpaceId(event.target.value)}
          placeholder="飞书知识库空间 ID"
        />
        <button disabled={syncingFeishuSpace || !feishuSpaceId.trim()}>
          {syncingFeishuSpace ? "同步中" : "同步飞书知识库"}
        </button>
      </form>

      <form
        className="feature-search"
        onSubmit={async (event) => {
          event.preventDefault();
          if (!confluencePageId.trim()) return;
          setIngestingConfluence(true);
          setError("");
          setUploadMessage("正在经 Gateway 拉取 Confluence 页面并建立权限索引…");
          try {
            const result = await api.ingestConfluencePage({ page_id: confluencePageId.trim() });
            setUploadMessage(
              `已摄取 Confluence 页面 ${result.document.title}，生成 ${result.chunk_count} 个结构化片段`,
            );
            setConfluencePageId("");
          } catch (caught) {
            setUploadMessage("");
            setError(caught instanceof Error ? caught.message : "Confluence 页面摄取失败");
          } finally {
            setIngestingConfluence(false);
          }
        }}
      >
        <BookOpen size={19} />
        <input
          value={confluencePageId}
          onChange={(event) => setConfluencePageId(event.target.value)}
          placeholder="Confluence Cloud 页面 ID"
        />
        <button disabled={ingestingConfluence || !confluencePageId.trim()}>
          {ingestingConfluence ? "摄取中" : "摄取 Confluence"}
        </button>
      </form>

      <form className="feature-search" onSubmit={search}>
        <Search size={19} />
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索已授权的制度、PRD、SOP 与技术文档" />
        <button disabled={searching || !query.trim()}>{searching ? "检索中" : "搜索"}</button>
      </form>

      {(uploadMessage || error) && (
        <div className={`notice ${error ? "error" : "success"}`}>
          {error ? <File size={17} /> : <FileCheck2 size={17} />}
          <span>{error || uploadMessage}</span>
        </div>
      )}

      {!results.length ? (
        <div className="feature-empty-grid">
          <article><BookOpen size={22} /><strong>结构化摄取</strong><p>保留标题层级、来源版本与内容指纹。</p></article>
          <article><ShieldCheck size={22} /><strong>检索前鉴权</strong><p>无权内容不会进入排序、上下文或模型。</p></article>
          <article><FileCheck2 size={22} /><strong>Evidence 引用</strong><p>每条结果展示来源、连接器、外部 ID 与修订；缺失字段保持为空。</p></article>
        </div>
      ) : (
        <div className="search-results">
          <div className="results-heading"><strong>{results.length} 条授权结果</strong><span>含溯源引用</span></div>
          {results.map((result) => (
            <article key={result.chunk_id}>
              <div className="result-source">
                <BookOpen size={16} />
                <span>{result.source}</span>
                <small>v{result.version}</small>
                {result.classification ? <small>{result.classification}</small> : null}
              </div>
              <h3>{result.title}</h3>
              <p>{result.content}</p>
              <KnowledgeProvenance fields={result} />
              <footer>
                <span>
                  {result.heading_path.length ? result.heading_path.join(" / ") : "文档正文"}
                </span>
                <strong>{Number(result.score).toFixed(2)}</strong>
              </footer>
            </article>
          ))}
        </div>
      )}
    </main>
  );
}
