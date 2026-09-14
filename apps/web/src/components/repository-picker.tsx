"use client";

import { GitBranch, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type { CodeRepository } from "@/lib/types";

export function RepositoryPicker({ value, onChange, disabled }: {
  value?: string;
  onChange: (value: string | undefined) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [repositories, setRepositories] = useState<CodeRepository[]>([]);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const trigger = useRef<HTMLButtonElement>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => {
    ++generation.current;
    controller.current?.abort();
  }, []);

  const close = () => {
    ++generation.current;
    controller.current?.abort();
    setOpen(false);
    setLoading(false);
    setRepositories([]);
    setError("");
    trigger.current?.focus();
  };
  const browse = async () => {
    const active = ++generation.current;
    controller.current?.abort();
    const pending = new AbortController();
    controller.current = pending;
    setOpen(true);
    setLoading(true);
    setRepositories([]);
    setError("");
    onChange(undefined);
    try {
      const result = await api.listCodeRepositories(pending.signal);
      if (active === generation.current) setRepositories(result);
    } catch (caught) {
      if (active === generation.current) setError(caught instanceof Error ? caught.message : "无法读取授权仓库");
    } finally {
      if (active === generation.current) setLoading(false);
    }
  };

  return <div className="repository-picker" onKeyDown={(event) => {
    if (event.key === "Escape" && open) { event.stopPropagation(); close(); }
  }}>
    <button ref={trigger} type="button" className="icon-button composer-tool" aria-label="选择代码仓库"
      aria-expanded={open} disabled={disabled} onClick={open ? close : () => void browse()}>
      <GitBranch size={18} /><span title={value}>{value || "仓库"}</span>
    </button>
    {value && <button type="button" className="icon-button" disabled={disabled}
      aria-label="清除本轮仓库指定" onClick={() => onChange(undefined)}><X size={12} /></button>}
    {open && <section className="repository-picker-panel" role="dialog" aria-label="指定本轮代码仓库">
      <label>本轮仓库
        <select value={value ?? ""} disabled={disabled || loading || Boolean(error)} onChange={(event) => {
          const selected = repositories.find((item) => item.name === event.target.value);
          onChange(selected?.name);
          close();
        }}>
          <option value="">不指定，追问沿用任务上下文</option>
          {repositories.map((item) => <option key={item.id} value={item.name}>{item.name}</option>)}
        </select>
      </label>
      {loading && <p role="status">正在读取授权仓库…</p>}
      {error && <p role="alert">{error}</p>}
      {!loading && !error && !repositories.length && <p>暂无可选的授权仓库。</p>}
      <p>仅用于本轮代码调查。每次读取仍会检查权限；追问由任务上下文继续保持选择。</p>
      <button type="button" onClick={close}>关闭仓库选择</button>
    </section>}
  </div>;
}
