"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { KnowledgeSource, KnowledgeSourceConnection, KnowledgeSourceItem, sourceIssue, sourceStateLabels } from "@/lib/knowledge-sources";

function mergeSources(previous: KnowledgeSource[], incoming: KnowledgeSource[]) {
  const byId = new Map(previous.map((source) => [source.id, source]));
  for (const source of incoming) byId.set(source.id, source);
  return [...byId.values()];
}

export function KnowledgeSources() {
  const [open, setOpen] = useState(false);
  const [connections, setConnections] = useState<KnowledgeSourceConnection[]>([]);
  const [selected, setSelected] = useState("");
  const [sources, setSources] = useState<KnowledgeSource[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [details, setDetails] = useState<{ id: string; items: KnowledgeSourceItem[]; cursor: string | null } | null>(null);
  const sequence = useRef(0);
  const detailSequence = useRef(0);
  const changing = useRef(false);

  const refresh = useCallback(async (more?: string) => {
    if (changing.current) return;
    const revision = ++sequence.current;
    setBusy(true);
    try {
      const [available, page] = await Promise.all([api.knowledgeSourceConnections(), api.knowledgeSources(more)]);
      if (revision !== sequence.current) return;
      setConnections(available);
      setSelected((value) => available.some((item) => item.connector_id === value) ? value : available.length === 1 ? available[0].connector_id : "");
      setSources((previous) => more ? mergeSources(previous, page.items) : page.items);
      if (!more) { detailSequence.current++; setDetails(null); }
      setCursor(page.next_cursor);
      setError("");
    } catch (caught) {
      if (revision === sequence.current) {
        setError(caught instanceof Error ? caught.message : "无法读取同步状态");
        setSources([]); setConnections([]); setSelected(""); setCursor(null);
        detailSequence.current++; setDetails(null);
      }
    } finally {
      if (revision === sequence.current) setBusy(false);
    }
  }, []);

  const invalidate = useCallback(() => { sequence.current++; detailSequence.current++; }, []);

  useEffect(() => {
    if (!open) return;
    const timer = window.setInterval(() => { if (!document.hidden && !details) void refresh(); }, 10_000);
    return () => { window.clearInterval(timer); invalidate(); };
  }, [open, refresh, invalidate, details]);

  async function change(action: () => Promise<KnowledgeSource>, message: string) {
    if (changing.current) return;
    changing.current = true;
    sequence.current++;
    detailSequence.current++;
    setBusy(true); setError(""); setNotice(""); setDetails(null);
    try {
      const updated = await action();
      setSources((previous) => mergeSources(previous, [updated]));
      setNotice(message);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "来源操作未完成");
    } finally { changing.current = false; setBusy(false); }
  }

  async function inspect(id: string, more?: string) {
    const revision = ++detailSequence.current;
    try {
      const page = await api.knowledgeSourceItems(id, more);
      if (revision !== detailSequence.current) return;
      setDetails((previous) => ({ id, items: more && previous?.id === id ? [...previous.items, ...page.items] : page.items, cursor: page.next_cursor }));
      setError("");
    } catch (caught) {
      if (revision === detailSequence.current) setError(caught instanceof Error ? caught.message : "无法读取文档状态");
    }
  }

  return <section className="knowledge-sources" aria-label="钉钉自动同步">
    <div className="knowledge-source-heading">
      <div><h2>钉钉文档自动同步</h2><p>按已绑定的组织读取文档，持续更新有权使用的知识。</p></div>
      <button type="button" onClick={() => { setOpen(!open); if (!open) void refresh(); }} aria-expanded={open}>
        {open ? "收起来源" : "管理同步来源"}
      </button>
    </div>
    {open && <>
      <form className="knowledge-source-connect" onSubmit={(event) => {
        event.preventDefault();
        const connection = connections.find((item) => item.connector_id === selected);
        if (connection) void change(async () => {
          const registered = await api.registerKnowledgeSource({ connector_id: connection.connector_id, binding_id: connection.binding_id });
          return registered.active ? registered : api.controlKnowledgeSource(registered.id, "resume");
        }, "来源已登记；后台处理后，可用文档会显示在这里。");
      }}>
        <label>选择已授权连接<select aria-label="钉钉组织连接" value={selected} onChange={(event) => setSelected(event.target.value)} disabled={busy}>
          {!connections.length && <option value="">暂无可用连接</option>}
          {connections.length > 1 && <option value="">请选择组织连接</option>}
          {connections.map((connection) => <option key={connection.connector_id} value={connection.connector_id}>{connection.name}</option>)}
        </select></label>
        <button disabled={busy || !selected}>启用自动同步</button>
        <button type="button" disabled={busy} onClick={() => void refresh()}>刷新状态</button>
      </form>
      {!busy && !connections.length && !error && <p>尚未配置与你当前账号绑定的钉钉连接。请由管理员完成连接配置和账号绑定。</p>}
      {error && <p role="alert" className="notice error">{error}</p>}
      {notice && <p role="status" className="notice success">{notice}</p>}
      {busy && <p role="status">正在更新同步信息…</p>}
      <div className="knowledge-source-list">
        {sources.map((source) => <article key={source.id} aria-label={`${source.name}同步状态`}>
          <div className="knowledge-source-heading"><h3>{source.name}</h3><span>{sourceStateLabels[source.state]}</span></div>
          <p className="knowledge-source-counts"><strong>{source.counts.available}</strong> 篇可用于问答 · 已发现 {source.counts.files} 个文件</p>
          <p>待处理 {source.counts.pending} · 内容不完整或格式不支持 {source.counts.partial} · 失败 {source.counts.failed} · 等待权限复核 {source.counts.awaiting_access_check}</p>
          {source.issue && <p className="knowledge-source-issue">{sourceIssue(source.issue)}</p>}
          <p className="knowledge-source-time">最近扫描结束：{source.last_scan_completed_at ? new Date(source.last_scan_completed_at).toLocaleString("zh-CN") : "尚未完成"}</p>
          {source.state === "QUEUED" && <p>正在等待后台处理；如果长时间不变，请检查同步服务是否运行。</p>}
          <div className="knowledge-source-actions">
            <button disabled={busy} onClick={() => void change(() => api.controlKnowledgeSource(source.id, source.active ? "pause" : "resume"), source.active ? "已暂停，后续检索将不再使用这一来源。" : "已恢复，文档须重新读取和核验后才可使用。")}>{source.active ? "暂停同步" : "恢复同步"}</button>
            <button disabled={busy || !source.active} onClick={() => void change(() => api.controlKnowledgeSource(source.id, "sync"), "已安排重新扫描，后台将逐步更新文档。")}>重新扫描</button>
            <button disabled={busy} onClick={() => void inspect(source.id)}>查看文档状态</button>
          </div>
          {details?.id === source.id && <div className="knowledge-source-items">
            <p>文档状态为本次查看时的结果；点击“刷新状态”重新核对。</p>
            {details.items.map((item) => <div key={item.id}><strong>{item.title}</strong><span>{item.available ? "可用" : item.kind === "folder" ? "目录" : item.status === "PENDING" ? "等待处理" : "暂不可用"}</span>
              {!item.available && item.issues.length > 0 && <p>{[...new Set(item.issues.map(sourceIssue))].join(" ")}</p>}
            </div>)}
            {!details.items.length && <p>尚未发现文档。</p>}
            {details.cursor && <button onClick={() => void inspect(source.id, details.cursor ?? undefined)}>更多文档</button>}
          </div>}
        </article>)}
      </div>
      {cursor && <button disabled={busy} onClick={() => void refresh(cursor)}>更多来源</button>}
      <p className="knowledge-source-note">扫描结束不代表所有内容均已导入；只有“可用”文档参与新的检索。</p>
    </>}
  </section>;
}
