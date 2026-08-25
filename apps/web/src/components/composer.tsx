"use client";

import { ArrowUp, AtSign, LoaderCircle, Paperclip, Square, X } from "lucide-react";
import { ChangeEvent, KeyboardEvent, useEffect, useRef } from "react";

import type { Artifact } from "@/lib/types";

interface ComposerProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onCancel?: () => void;
  running: boolean;
  disabled?: boolean;
  attachments: Artifact[];
  uploading: boolean;
  onAttach: (files: File[]) => void;
  onRemoveAttachment: (artifactId: string) => void;
}

export function Composer({
  value,
  onChange,
  onSubmit,
  onCancel,
  running,
  disabled,
  attachments,
  uploading,
  onAttach,
  onRemoveAttachment,
}: ComposerProps) {
  const textarea = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const element = textarea.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${Math.min(element.scrollHeight, 180)}px`;
  }, [value]);

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      if (!running && value.trim()) onSubmit();
    }
  };

  const handleFiles = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    if (files.length) onAttach(files);
    event.target.value = "";
  };

  return (
    <div className="composer-shell">
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
          placeholder="询问知识、指标、代码，或调查线上异常…"
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
              disabled={disabled || uploading || running}
            >
              <Paperclip size={18} />
            </button>
            <button className="icon-button" aria-label="添加上下文" title="添加上下文">
              <AtSign size={18} />
            </button>
            <span className="model-pill">自动路由</span>
          </div>
          <button
            className={`send-button ${running ? "stop" : ""}`}
            onClick={running ? onCancel : onSubmit}
            disabled={!running && (!value.trim() || disabled || uploading)}
            aria-label={running ? "停止运行" : "发送"}
          >
            {running ? <Square size={14} fill="currentColor" /> : <ArrowUp size={18} />}
          </button>
        </div>
      </div>
      <p className="composer-note">Obsion 可能出错。关键结论请检查右侧证据与验证状态。</p>
    </div>
  );
}
