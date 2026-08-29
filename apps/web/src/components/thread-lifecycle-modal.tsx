"use client";

import {
  Archive,
  ArchiveRestore,
  Clock3,
  GitFork,
  History,
  LoaderCircle,
  X,
} from "lucide-react";
import { FormEvent, useState } from "react";

import type { Thread, ThreadEvent } from "@/lib/types";

interface ThreadLifecycleModalProps {
  thread: Thread;
  events: ThreadEvent[];
  loading: boolean;
  pendingAction?: "archive" | "resume" | "fork";
  running: boolean;
  onClose: () => void;
  onArchive: () => void;
  onResume: () => void;
  onFork: (title: string) => void;
}

export function ThreadLifecycleModal({
  thread,
  events,
  loading,
  pendingAction,
  running,
  onClose,
  onArchive,
  onResume,
  onFork,
}: ThreadLifecycleModalProps) {
  const [forkTitle, setForkTitle] = useState(`${thread.title} · 分支`);

  const submitFork = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (forkTitle.trim()) onFork(forkTitle.trim());
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <section
        className="workspace-modal thread-lifecycle-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="thread-lifecycle-title"
      >
        <header>
          <span className="modal-icon"><History size={19} /></span>
          <div>
            <h2 id="thread-lifecycle-title">任务生命周期</h2>
            <p>{thread.title}</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭任务生命周期">
            <X size={18} />
          </button>
        </header>

        <div className="thread-lifecycle-summary">
          <span className={`thread-status ${thread.status.toLowerCase()}`}>
            {thread.status === "ARCHIVED" ? <Archive size={14} /> : <Clock3 size={14} />}
            {thread.status === "ARCHIVED" ? "已归档" : "进行中"}
          </span>
          <span>创建于 {new Date(thread.created_at).toLocaleString("zh-CN")}</span>
          {thread.parent_thread_id && (
            <span title={thread.parent_thread_id}>
              <GitFork size={13} /> 来源任务 {shortId(thread.parent_thread_id)}
            </span>
          )}
          {thread.forked_from_turn_id && (
            <span title={thread.forked_from_turn_id}>
              分支点 {shortId(thread.forked_from_turn_id)}
            </span>
          )}
        </div>

        <section className="thread-event-history" aria-label="任务生命周期事件">
          <div><strong>不可变生命周期记录</strong><small>{events.length} 项</small></div>
          {loading ? (
            <p className="thread-events-empty"><LoaderCircle className="spin" size={15} /> 正在读取事件…</p>
          ) : events.length ? (
            <ol>
              {events.map((event) => (
                <li key={event.id}>
                  <span>{event.sequence}</span>
                  <div>
                    <strong>{eventName(event.name)}</strong>
                    <small>{new Date(event.created_at).toLocaleString("zh-CN")}</small>
                  </div>
                </li>
              ))}
            </ol>
          ) : (
            <p className="thread-events-empty">尚无可见生命周期事件</p>
          )}
        </section>

        <form className="thread-fork-form" onSubmit={submitFork}>
          <label htmlFor={`fork-title-${thread.id}`}>
            <span>建立独立分支</span>
            <small>从此任务的最新轮次保留分支来源，原任务不会被修改。</small>
          </label>
          <div>
            <input
              id={`fork-title-${thread.id}`}
              value={forkTitle}
              onChange={(event) => setForkTitle(event.target.value)}
              minLength={1}
              maxLength={300}
              required
            />
            <button
              type="submit"
              className="secondary-button"
              disabled={Boolean(pendingAction) || !forkTitle.trim()}
            >
              {pendingAction === "fork" ? <LoaderCircle className="spin" size={14} /> : <GitFork size={14} />}
              {pendingAction === "fork" ? "正在建立…" : "建立分支"}
            </button>
          </div>
        </form>

        {running && thread.status === "ACTIVE" && (
          <p className="thread-lifecycle-note">当前运行结束或取消后才能归档此任务。</p>
        )}

        <footer>
          {thread.status === "ARCHIVED" ? (
            <button
              type="button"
              className="primary-button"
              onClick={onResume}
              disabled={Boolean(pendingAction)}
            >
              <ArchiveRestore size={15} />
              {pendingAction === "resume" ? "正在恢复…" : "恢复并继续"}
            </button>
          ) : (
            <button
              type="button"
              className="secondary-button danger-button"
              onClick={onArchive}
              disabled={Boolean(pendingAction) || running}
            >
              <Archive size={15} />
              {pendingAction === "archive" ? "正在归档…" : "归档任务"}
            </button>
          )}
          <button type="button" className="secondary-button" onClick={onClose}>关闭</button>
        </footer>
      </section>
    </div>
  );
}

function eventName(name: string) {
  return {
    "thread.created": "任务已创建",
    "thread.forked": "从来源任务建立分支",
    "thread.archived": "任务已归档",
    "thread.resumed": "任务已恢复",
  }[name] ?? name;
}

function shortId(value: string) {
  return `${value.slice(0, 8)}…${value.slice(-4)}`;
}
