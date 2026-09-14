"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { CodeRepository, CodeupReadRequest, CodeupReadResult } from "@/lib/types";

const GUIDANCE: Record<string, string> = {
  codeup_configuration_invalid: "请管理员在连接器中填写云效组织、仓库映射和密钥引用，再为此项目绑定只读查询能力。",
  credential_unavailable: "请管理员在服务端密钥管理中补充云效只读访问令牌，并检查密钥引用；无需在此处输入令牌。",
  codeup_repository_denied: "请项目管理员核对你的仓库访问权限，以及连接器中此项目的仓库映射。",
  codeup_upstream_denied: "请管理员核对云效中的仓库成员关系、只读令牌权限，以及查询的提交或文件是否存在。",
  codeup_rate_limited: "请稍后重新查询，或减少单次查询数量。",
  capability_rate_limited: "此连接已达到查询额度，请稍后重试。",
  codeup_response_invalid: "结果未通过校验，请管理员检查云效连接；本次内容不能用作回答依据。",
  codeup_response_too_large: "请缩小查询范围；文件仅支持 256 KiB 以内的文本。",
};

export function CodeupReader({ repositories }: { repositories: CodeRepository[] }) {
  const [selected, setSelected] = useState("");
  const repository = repositories.find((item) => item.id === selected);
  return (
    <section className="codeup-reader" aria-labelledby="codeup-title">
      <h2 id="codeup-title">云效项目查询</h2>
      <p>只读查询已连接项目的仓库信息、提交记录、目录和指定版本的文本文件。</p>
      <label>
        授权仓库
        <select value={repository?.id ?? ""} onChange={(event) => setSelected(event.target.value)}>
          <option value="">请选择仓库</option>
          {repositories.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>
      {repository ? <RepositoryReader key={repository.id} repository={repository} /> : (
        <p>{repositories.length ? "选择仓库后可发起查询。" : "暂无授权仓库，请项目管理员登记项目并授予访问权限。"}</p>
      )}
    </section>
  );
}

function RepositoryReader({ repository }: { repository: CodeRepository }) {
  const [operation, setOperation] = useState<CodeupReadRequest["operation"]>("codeup.repository.get");
  const [refName, setRefName] = useState(repository.default_branch || "main");
  const [commit, setCommit] = useState("");
  const [path, setPath] = useState("");
  const [result, setResult] = useState<CodeupReadResult | null>(null);
  const [lastRequest, setLastRequest] = useState<CodeupReadRequest | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => {
    generation.current += 1;
    controller.current?.abort();
  }, []);

  const clear = () => {
    generation.current += 1;
    controller.current?.abort();
    setBusy(false);
    setResult(null);
    setLastRequest(null);
    setError(null);
  };
  const execute = async (input: CodeupReadRequest) => {
    controller.current?.abort();
    const active = ++generation.current;
    const pending = new AbortController();
    controller.current = pending;
    setBusy(true);
    setResult(null);
    setError(null);
    setLastRequest(input);
    try {
      const response = await api.readCodeupRepository(repository.id, input, pending.signal);
      if (active !== generation.current) return;
      if (response.repository_id !== repository.id || response.operation !== input.operation) {
        throw new Error("查询结果与当前项目不一致，请重新查询。");
      }
      if ("commit_id" in input && response.items.some((item) => !("commit_id" in item) || item.commit_id !== input.commit_id)) {
        throw new Error("查询结果与指定提交不一致，请重新查询。");
      }
      if (input.operation === "codeup.tree.list" && response.operation === "codeup.tree.list"
        && response.items.some((item) => item.parent_path !== input.path
          || item.path !== `${input.path ? `${input.path}/` : ""}${item.name}`
          || !item.name || item.name.includes("/") || item.name.includes("\\")
          || [...item.name].some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)
          || [".", ".."].includes(item.name))) {
        throw new Error("目录结果与查询路径不一致，请重新查询。");
      }
      if (input.operation === "codeup.file.read" && response.operation === "codeup.file.read"
        && response.items.some((item) => item.path !== input.path)) {
        throw new Error("文件结果与查询路径不一致，请重新查询。");
      }
      setResult(response);
    } catch (caught) {
      if (active === generation.current) setError(caught instanceof Error ? caught : new Error("查询失败，请重试。"));
    } finally {
      if (active === generation.current) setBusy(false);
    }
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    let input: CodeupReadRequest;
    if (operation === "codeup.commits.list") input = { operation, ref: refName.trim(), page: 1, limit: 20 };
    else if (operation === "codeup.commit.get") input = { operation, commit_id: commit.trim() };
    else if (operation === "codeup.file.read" || operation === "codeup.tree.list") input = { operation, commit_id: commit.trim(), path: path.trim() };
    else input = { operation };
    void execute(input);
  };
  const navigate = (input: Extract<CodeupReadRequest, { operation: "codeup.tree.list" | "codeup.file.read" }>) => {
    setOperation(input.operation);
    setCommit(input.commit_id);
    setPath(input.path);
    void execute(input);
  };
  const needsCommit = operation === "codeup.commit.get" || operation === "codeup.file.read" || operation === "codeup.tree.list";
  const valid = (!needsCommit || /^[a-f0-9]{40}$/.test(commit.trim()))
    && (operation !== "codeup.commits.list" || Boolean(refName.trim()))
    && (operation !== "codeup.file.read" || Boolean(path.trim()));
  const next = result?.next_page;
  return (
    <>
      <form className="codeup-form" onSubmit={submit}>
        <label>查询内容
          <select value={operation} onChange={(event) => { clear(); setOperation(event.target.value as CodeupReadRequest["operation"]); }}>
            <option value="codeup.repository.get">仓库信息</option>
            <option value="codeup.commits.list">提交记录</option>
            <option value="codeup.commit.get">指定提交</option>
            <option value="codeup.tree.list">目录浏览</option>
            <option value="codeup.file.read">文件内容</option>
          </select>
        </label>
        {operation === "codeup.commits.list" && <label>分支或标签
          <input required maxLength={200} value={refName} onChange={(event) => { clear(); setRefName(event.target.value); }} />
        </label>}
        {needsCommit && <label>完整提交编号
          <input required maxLength={40} pattern="[a-f0-9]{40}" placeholder="从提交记录复制完整的 40 位编号" value={commit} onChange={(event) => { clear(); setCommit(event.target.value); }} />
        </label>}
        {operation === "codeup.file.read" && <label>文件路径
          <input required maxLength={1024} placeholder="例如 src/main.py" value={path} onChange={(event) => { clear(); setPath(event.target.value); }} />
        </label>}
        {operation === "codeup.tree.list" && <label>目录路径
          <input maxLength={1024} placeholder="留空查询根目录，例如 src" value={path} onChange={(event) => { clear(); setPath(event.target.value); }} />
        </label>}
        <button disabled={busy || !valid} type="submit">{busy ? "查询中…" : "查询云效"}</button>
      </form>
      <p className="codeup-help">首次使用需由管理员配置云效只读连接、仓库映射和项目权限。目录和文件查询需使用完整提交编号，可先从提交记录获取。</p>
      {busy && <p role="status">正在读取 {repository.name}…</p>}
      {error && <div className="notice error" role="alert"><div>
        <strong>{error.message}</strong>
        {error instanceof ApiError && GUIDANCE[error.code] && <p>{GUIDANCE[error.code]}</p>}
        {error instanceof ApiError && error.correlationId && <small>查询编号：{error.correlationId}</small>}
      </div></div>}
      {result && <div className="codeup-result" aria-live="polite">
        <p>已读取 {result.repository} · {result.count} 条结果</p>
        {lastRequest?.operation === "codeup.tree.list" && <p>当前目录：{lastRequest.path || "/"} · 提交编号：<code>{lastRequest.commit_id}</code></p>}
        {(lastRequest?.operation === "codeup.tree.list" || lastRequest?.operation === "codeup.file.read") && lastRequest.path && <button
          type="button" onClick={() => navigate({ operation: "codeup.tree.list", commit_id: lastRequest.commit_id, path: lastRequest.path.split("/").slice(0, -1).join("/") })}
        >{lastRequest.operation === "codeup.file.read" ? "返回所在目录" : "上级目录"}</button>}
        <ReadResult result={result} navigate={navigate} />
        {result.operation === "codeup.tree.list" && <p>仅列出当前目录的直接条目；子目录需单独读取，文件是否可读以实际查询结果为准。</p>}
        {!result.complete && <p>{result.operation === "codeup.tree.list" ? "当前目录达到读取上限，条目可能不完整。" : next ? "当前仅显示一页记录，可继续读取下一页。" : "已达到本轮分页上限，记录尚未全部读取。请缩小查询范围。"}</p>}
        {next && lastRequest?.operation === "codeup.commits.list" && <button type="button" onClick={() => void execute({ ...lastRequest, page: next })}>下一页</button>}
      </div>}
    </>
  );
}

function ReadResult({ result, navigate }: {
  result: CodeupReadResult;
  navigate: (input: Extract<CodeupReadRequest, { operation: "codeup.tree.list" | "codeup.file.read" }>) => void;
}) {
  if (!result.items.length) return <p>本次查询没有返回记录。</p>;
  if (result.operation === "codeup.repository.get") return <>{result.items.map((item) => (
    <article key={item.id}><h3>{item.name}</h3><p>默认分支：{item.default_branch}</p><p>可见范围：{item.visibility === "private" ? "私有" : "组织内部"}</p></article>
  ))}</>;
  if (result.operation === "codeup.file.read") return <>{result.items.map((item) => (
    <article key={item.path}>
      <h3>{item.path}</h3><p>提交编号：<code>{item.commit_id}</code></p>
      <p>{item.source_size_bytes} 字节{item.redacted ? " · 检出的敏感内容已隐藏" : ""}</p>
      <pre tabIndex={0}><code>{item.content}</code></pre>
    </article>
  ))}</>;
  if (result.operation === "codeup.tree.list") return <ul>{result.items.map((item) => (
    <li key={item.path}>
      {item.type === "tree" && item.mode === "040000" ? <button type="button" onClick={() => navigate({ operation: "codeup.tree.list", commit_id: item.commit_id, path: item.path })}>打开目录 {item.name}</button>
        : item.type === "blob" && ["100644", "100755"].includes(item.mode) && !item.is_lfs && item.can_read_content
          ? <button type="button" onClick={() => navigate({ operation: "codeup.file.read", commit_id: item.commit_id, path: item.path })}>读取文件 {item.name}</button>
          : <span>{item.name} · {item.is_lfs ? "LFS 文件，仅显示信息" : item.mode === "120000" ? "符号链接，仅显示信息" : item.type === "commit" ? "子模块，仅显示信息" : "受保护条目，仅显示信息"}</span>}
    </li>
  ))}</ul>;
  return <>{result.items.map((item) => (
    <article key={item.commit_id}>
      <h3>{item.title || "无提交标题"}</h3><p><code>{item.commit_id}</code></p>
      <time dateTime={item.committed_at}>{item.committed_at}</time><p className="codeup-message">{item.message}</p>
    </article>
  ))}</>;
}
