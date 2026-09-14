"use client";

import { SlidersHorizontal } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";

export type TaskContextDraft = {
  documents?: Array<{ id: string; title: string }>;
  output_format?: "AUTO" | "TABLE" | "BULLETS" | "REPORT";
  constraints?: string[];
};

export function taskContextReferences(value: TaskContextDraft): Array<Record<string, unknown>> {
  if (!Object.keys(value).length) return [];
  return [{ type: "task_context",
    ...(value.documents !== undefined ? { document_ids: value.documents.map((item) => item.id) } : {}),
    ...(value.output_format !== undefined ? { output_format: value.output_format } : {}),
    ...(value.constraints !== undefined ? { constraints: value.constraints } : {}),
  }];
}

export function TaskContextPicker({ value, onChange, disabled }: {
  value: TaskContextDraft;
  onChange: (value: TaskContextDraft) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Array<{ id: string; title: string }>>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [searched, setSearched] = useState(false);
  const generation = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => () => { ++generation.current; controller.current?.abort(); }, []);
  const close = () => {
    ++generation.current;
    controller.current?.abort();
    setOpen(false);
    setResults([]);
    setLoading(false);
    setSearched(false);
    setError("");
    trigger.current?.focus();
  };
  const search = async () => {
    if (disabled || !query.trim()) return;
    const active = ++generation.current;
    controller.current?.abort();
    const pending = new AbortController();
    controller.current = pending;
    setResults([]);
    setLoading(true);
    setError("");
    setSearched(true);
    try {
      const hits = await api.knowledgeSearch(query.trim(), pending.signal);
      if (active !== generation.current) return;
      const documents = new Map(hits.map((hit) => [hit.document_id, { id: hit.document_id, title: hit.title }]));
      setResults([...documents.values()]);
    } catch (caught) {
      if (active === generation.current) setError(caught instanceof Error ? caught.message : "无法搜索授权资料");
    } finally {
      if (active === generation.current) setLoading(false);
    }
  };
  return <div className="repository-picker" onKeyDown={(event) => {
    if (event.key === "Escape" && open) { event.stopPropagation(); close(); }
  }}>
    <button ref={trigger} type="button" className="icon-button composer-tool" aria-label="设置任务资料与输出"
      aria-expanded={open} disabled={disabled} onClick={open ? close : () => setOpen(true)}>
      <SlidersHorizontal size={18} /><span>{Object.keys(value).length ? "已设要求" : "资料与输出"}</span>
    </button>
    {open && <section className="repository-picker-panel task-context-panel" role="dialog" aria-label="任务资料与输出要求">
      <label>搜索授权资料<input value={query} maxLength={512} disabled={disabled} onChange={(event) => {
        ++generation.current; controller.current?.abort(); setLoading(false); setResults([]);
        setSearched(false); setError(""); setQuery(event.target.value);
      }} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void search(); } }} /></label>
      <button type="button" disabled={disabled || loading || !query.trim()} onClick={() => void search()}>搜索资料</button>
      {loading && <p role="status">正在搜索授权资料…</p>}
      {error && <p role="alert">{error}</p>}
      {searched && !loading && !error && !results.length && <p>没有找到可选资料，请调整关键词。</p>}
      {results.map((item) => <label className="task-document-option" key={item.id}>
        <input type="checkbox" checked={Boolean(value.documents?.some((selected) => selected.id === item.id))}
          disabled={disabled || ((value.documents?.length ?? 0) >= 4 && !value.documents?.some((selected) => selected.id === item.id))}
          onChange={(event) => onChange({ ...value, documents: event.target.checked
            ? [...(value.documents ?? []), item]
            : value.documents?.filter((selected) => selected.id !== item.id) ?? [] })} />{item.title}
      </label>)}
      {value.documents?.map((item) => <p key={item.id}>{item.title} <button type="button" disabled={disabled}
        aria-label={`移除资料 ${item.title}`} onClick={() => onChange({ ...value,
          documents: value.documents?.filter((selected) => selected.id !== item.id) })}>移除</button></p>)}
      <p>{value.documents === undefined ? "追问沿用已选资料。" : value.documents.length ? `仅在这 ${value.documents.length} 份资料中查找答案。` : "本轮解除已选资料范围。"} 每次读取都会重新检查权限。</p>
      <button type="button" disabled={disabled} onClick={() => onChange({ ...value, documents: [] })}>解除资料限定</button>
      <label>输出形式<select disabled={disabled} value={value.output_format ?? "INHERIT"} onChange={(event) => {
        const next = { ...value };
        if (event.target.value === "INHERIT") delete next.output_format;
        else next.output_format = event.target.value as TaskContextDraft["output_format"];
        onChange(next);
      }}>
        <option value="INHERIT">追问沿用原要求</option><option value="AUTO">自动</option>
        <option value="TABLE">表格</option><option value="BULLETS">列表</option><option value="REPORT">报告</option>
      </select></label>
      <label>补充要求<textarea disabled={disabled} maxLength={1000} value={value.constraints?.join("\n") ?? ""}
        placeholder="未填写时，追问沿用原要求" onChange={(event) => onChange({ ...value,
          constraints: event.target.value.trim() ? [event.target.value] : [] })} /></label>
      {value.constraints?.length === 0 && <p>本轮清除原补充要求。</p>}
      <p>新话题不会继承旧任务的设置。</p>
      <button type="button" disabled={disabled} onClick={() => onChange({})}>撤销本轮设置</button>
      <button type="button" onClick={close}>完成设置</button>
    </section>}
  </div>;
}

export function TaskContextSummary({ context }: { context: unknown }) {
  if (!context || typeof context !== "object" || Array.isArray(context)) return null;
  const fields = context as Record<string, unknown>;
  const documents = Array.isArray(fields.selected_documents) ? fields.selected_documents.length : 0;
  const format = typeof fields.output_format === "string"
    ? ({ TABLE: "表格", BULLETS: "列表", REPORT: "报告" } as Record<string, string>)[fields.output_format]
    : undefined;
  const constraints = Array.isArray(fields.task_constraints)
    ? fields.task_constraints.filter((item): item is string => typeof item === "string") : [];
  const labels = [...(documents ? [`${documents} 份选定资料`] : []), ...(format ? [format] : []), ...constraints];
  if (!labels.length) return null;
  return <p className="task-context-summary">当前任务要求：{labels.join(" · ")}。指代追问继续沿用，可在“资料与输出”中修改。</p>;
}
