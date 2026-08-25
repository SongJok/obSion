"use client";

import { BookOpen, File, FileCheck2, Search, ShieldCheck, UploadCloud } from "lucide-react";
import { FormEvent, useRef, useState } from "react";

import { api } from "@/lib/api";

export function KnowledgeView() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Array<Record<string, unknown>>>([]);
  const [searching, setSearching] = useState(false);
  const [uploadMessage, setUploadMessage] = useState("");
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
    }
  };

  return (
    <main className="feature-page">
      <header className="feature-header">
        <div>
          <span className="eyebrow">OBSION KNOWLEDGE</span>
          <h1>企业知识</h1>
          <p>文档权限在分块和检索阶段继承，答案引用可追溯到具体版本。</p>
        </div>
        <button className="primary-button" onClick={() => fileRef.current?.click()}>
          <UploadCloud size={17} /> 上传文档
        </button>
        <input
          ref={fileRef}
          hidden
          type="file"
          accept=".pdf,.docx,.xlsx,.md,.txt,.html"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) void upload(file);
          }}
        />
      </header>

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
          <article><FileCheck2 size={22} /><strong>Evidence 引用</strong><p>每条回答可回到文档、版本和片段。</p></article>
        </div>
      ) : (
        <div className="search-results">
          <div className="results-heading"><strong>{results.length} 条授权结果</strong><span>按相关性排序</span></div>
          {results.map((result) => (
            <article key={String(result.chunk_id)}>
              <div className="result-source"><BookOpen size={16} /><span>{String(result.source)}</span><small>v{String(result.version)}</small></div>
              <h3>{String(result.title)}</h3>
              <p>{String(result.content)}</p>
              <footer><span>{Array.isArray(result.heading_path) ? result.heading_path.join(" / ") : "文档正文"}</span><strong>{Number(result.score).toFixed(2)}</strong></footer>
            </article>
          ))}
        </div>
      )}
    </main>
  );
}
