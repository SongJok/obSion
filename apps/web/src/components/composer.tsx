"use client";

import { ArrowUp, AtSign, FileText, LoaderCircle, Paperclip, Search, Square, X } from "lucide-react";
import { ChangeEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";

import type { Artifact } from "@/lib/types";

interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onCancel?: () => void;
  running: boolean;
  submitting?: boolean;
  disabled?: boolean;
  placeholder?: string;
  note?: string;
  attachments: Artifact[];
  uploading: boolean;
  onAttach: (files: File[]) => void;
  onRemoveAttachment: (artifactId: string) => void;
  contextArtifacts: Artifact[];
  contextOpen: boolean;
  contextLoading: boolean;
  onOpenContext: () => void;
  onCloseContext: () => void;
  onAddContext: (artifact: Artifact) => void;
}

export function Composer({
  value,
  onChange,
  onSubmit,
  onCancel,
  running,
  submitting = false,
  disabled,
  placeholder = "询问知识、指标、代码，或调查线上异常…",
  note = "Obsion 可能出错。关键结论请检查右侧证据与验证状态。",
  attachments,
  uploading,
  onAttach,
  onRemoveAttachment,
  contextArtifacts,
  contextOpen,
  contextLoading,
  onOpenContext,
  onCloseContext,
  onAddContext,
}: ComposerProps) {
  const textarea = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [contextQuery, setContextQuery] = useState("");

  useEffect(() => {
    const element = textarea.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 180)}px`;
  }, [value]);

  const availableContext = useMemo(() => {
    const selected = new Set(attachments.map((artifact) => artifact.id));
    const term = contextQuery.trim().toLocaleLowerCase("zh-CN");
    return contextArtifacts.filter((artifact) => {
      if (selected.has(artifact.id) || !isReadableContext(artifact)) return false;
      return !term || [artifact.title, artifact.kind, artifact.media_type]
        .join(" ")
        .toLocaleLowerCase("zh-CN")
        .includes(term);
    });
  }, [attachments, contextArtifacts, contextQuery]);

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      if (!running && !submitting && value.trim()) onSubmit();
    }
  };

  const handleFiles = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    if (files.length) onAttach(files);
    event.target.value = "";
  };

  const closeContext = () => {
    setContextQuery("");
    onCloseContext();
  };

  return (
    <div className="composer-shell">
      {contextOpen && (
        <section className="context-picker" role="dialog" aria-label="选择工作区上下文">
          <header>
            <div>
              <AtSign size={16} />
              <span><strong>添加工作区上下文</strong><small>发送后会固化为本次运行的受控证据</small></span>
            </div>
            <button type="button" className="icon-button" onClick={closeContext} aria-label="关闭上下文选择器">
              <X size={16} />
            </button>
          </header>
          <label className="context-search">
            <Search size={15} />
            <input
              autoFocus
              value={contextQuery}
              onChange={(event) => setContextQuery(event.target.value)}
              placeholder="搜索报告、表格、代码或已上传文件"
              aria-label="搜索工作区上下文"
            />
          </label>
          <div className="context-results">
            {contextLoading ? (
              <p><LoaderCircle className="spin" size={15} /> 正在读取可访问产物…</p>
            ) : availableContext.length ? availableContext.map((artifact) => (
              <button
                type="button"
                key={artifact.id}
                onClick={() => onAddContext(artifact)}
                aria-label={`添加上下文 ${artifact.title}`}
              >
                <span><FileText size={15} /></span>
                <span><strong>{artifact.title}</strong><small>{artifact.kind} · {artifact.classification}</small></span>
                <AtSign size={14} />
              </button>
            )) : (
              <p>{contextQuery.trim() ? "没有匹配的可解析产物" : "没有更多可添加的工作区产物"}</p>
            )}
          </div>
        </section>
      )}
      <div className="composer">
        {(attachments.length > 0 || uploading) && (
          <div className="composer-attachments" aria-label="待发送附件">
            {attachments.map((artifact) => (
              <span className="attachment-chip" key={artifact.id}>
                <Paperclip size={12} />
                <span>{artifact.title}</span>
                <button
                  type="button"
                  onClick={() => onRemoveAttachment(artifact.id)}
                  aria-label={`移除附件 ${artifact.title}`}
                >
                  <X size={12} />
                </button>
              </span>
            ))}
            {uploading && (
              <span className="attachment-chip uploading">
                <LoaderCircle size={12} /> 正在安全上传…
              </span>
            )}
          </div>
        )}
        <textarea
          ref={textarea}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          rows={1}
          disabled={disabled}
          aria-label="向 Obsion 提问"
        />
        <div className="composer-tools">
          <div>
            <input
              ref={fileInput}
              className="visually-hidden"
              type="file"
              multiple
              onChange={handleFiles}
              tabIndex={-1}
            />
            <button
              type="button"
              className="icon-button"
              aria-label="添加附件"
              title="添加附件"
              onClick={() => fileInput.current?.click()}
              disabled={disabled || uploading || running || submitting}
            >
              <Paperclip size={18} />
            </button>
            <button
              type="button"
              className={`icon-button ${contextOpen ? "active" : ""}`}
              aria-label="添加上下文"
              title="从工作区产物添加上下文"
              aria-expanded={contextOpen}
              onClick={contextOpen ? closeContext : onOpenContext}
              disabled={disabled || uploading || running || submitting}
            >
              <AtSign size={18} />
            </button>
            <span className="model-pill">自动路由</span>
          </div>
          <button
            className={`send-button ${running ? "stop" : ""}`}
            onClick={running ? onCancel : onSubmit}
            disabled={!running && (!value.trim() || disabled || uploading || submitting)}
            aria-label={running ? "停止运行" : "发送"}
          >
            {running ? <Square size={14} fill="currentColor" /> : <ArrowUp size={18} />}
          </button>
        </div>
      </div>
      <p className="composer-note">{note}</p>
    </div>
  );
}

function isReadableContext(artifact: Artifact) {
  if (artifact.inline_content) return true;
  const mediaType = artifact.media_type.split(";", 1)[0].toLocaleLowerCase("en-US");
  const filename = String(artifact.lineage.filename ?? artifact.title).toLocaleLowerCase("en-US");
  return [
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "text/html",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ].includes(mediaType) || [".txt", ".md", ".markdown", ".html", ".htm", ".pdf", ".docx", ".xlsx"]
    .some((suffix) => filename.endsWith(suffix));
}
