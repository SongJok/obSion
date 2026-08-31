"use client";

import { Code2, FileCode2, GitBranch, Search, ShieldCheck } from "lucide-react";
import { FormEvent, useEffect, useState } from "react";

import { api } from "@/lib/api";
import type { CodeRepository, CodeSymbolHit } from "@/lib/types";

export function CodeView() {
  const [query, setQuery] = useState("");
  const [repositories, setRepositories] = useState<CodeRepository[]>([]);
  const [results, setResults] = useState<CodeSymbolHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .listCodeRepositories()
      .then(setRepositories)
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : "无法读取代码仓库");
      });
  }, []);

  const search = async (event: FormEvent) => {
    event.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    setError("");
    try {
      setResults(await api.searchCodeSymbols(query));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "检索失败");
    } finally {
      setSearching(false);
    }
  };

  return (
    <main className="feature-page">
      <header className="feature-header">
        <div>
          <span className="eyebrow">OBSION CODE GRAPH</span>
          <h1>企业代码图</h1>
          <p>静态索引授权仓库中的符号、调用链与 SQL 引用。检索前完成 ACL，仓库代码永不执行。</p>
        </div>
        <span className="catalog-count">
          <GitBranch size={17} /> {repositories.length} 个授权仓库
        </span>
      </header>

      <form className="feature-search" onSubmit={search}>
        <Search size={19} />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="搜索已授权的符号、API、类或 SQL 表"
        />
        <button disabled={searching || !query.trim()}>{searching ? "检索中" : "搜索"}</button>
      </form>

      {error && (
        <div className="notice error">
          <FileCode2 size={17} />
          <span>{error}</span>
        </div>
      )}

      {!results.length ? (
        <div className="feature-empty-grid">
          <article>
            <Code2 size={22} />
            <strong>静态解析</strong>
            <p>Python AST 与保守的 Java/TypeScript 扫描，从不执行仓库代码。</p>
          </article>
          <article>
            <ShieldCheck size={22} />
            <strong>检索前鉴权</strong>
            <p>无权仓库不会进入排序、上下文、Evidence 或模型。</p>
          </article>
          <article>
            <GitBranch size={22} />
            <strong>调用链证据</strong>
            <p>符号、引用与 callers 通过 Capability Gateway 成为 CODE Evidence。</p>
          </article>
        </div>
      ) : (
        <div className="search-results">
          <div className="results-heading">
            <strong>{results.length} 条授权符号</strong>
            <span>当前快照</span>
          </div>
          {results.map((result) => (
            <article key={result.symbol_id}>
              <div className="result-source">
                <Code2 size={16} />
                <span>{result.repository}</span>
                <small>{result.kind}</small>
              </div>
              <h3>{result.qualified_name}</h3>
              <p>
                {result.path}:{result.start_line}
                {result.commit_id ? ` · ${result.commit_id}` : ""}
              </p>
              <footer>
                <span>{result.language}</span>
                <strong>{result.name}</strong>
              </footer>
            </article>
          ))}
        </div>
      )}
    </main>
  );
}
