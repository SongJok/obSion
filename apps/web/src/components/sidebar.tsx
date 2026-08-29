"use client";

import {
  Archive,
  ArchiveRestore,
  BookOpen,
  Bot,
  ChevronDown,
  Database,
  FolderKanban,
  FileChartColumn,
  ListChecks,
  History,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Settings2,
  ShieldCheck,
  Workflow,
} from "lucide-react";
import { useState } from "react";

import type { SessionPrincipal, Thread, ViewName, Workspace } from "@/lib/types";
import { Logo } from "./logo";

interface SidebarProps {
  collapsed: boolean;
  mobileOpen?: boolean;
  onCollapse: () => void;
  workspaces: Workspace[];
  selectedWorkspace?: Workspace;
  onWorkspace: (workspace: Workspace) => void;
  threads: Thread[];
  selectedThreadId?: string;
  onThread: (thread: Thread) => void;
  onManageThread: (thread: Thread) => void;
  showArchivedThreads: boolean;
  onToggleArchivedThreads: () => void;
  threadListLoading?: boolean;
  onNewThread: () => void;
  onNewWorkspace: () => void;
  view: ViewName;
  onView: (view: ViewName) => void;
  principal: SessionPrincipal;
  onSignOut: () => Promise<void>;
}

const NAV_ITEMS = [
  { id: "assistant" as const, label: "智能工作台", icon: Bot },
  { id: "collaboration" as const, label: "任务与决策", icon: ListChecks },
  { id: "automation" as const, label: "自动化", icon: Workflow },
  { id: "actions" as const, label: "受控动作", icon: ShieldCheck },
  { id: "artifacts" as const, label: "产物中心", icon: FolderKanban },
  { id: "knowledge" as const, label: "企业知识", icon: BookOpen },
  { id: "data" as const, label: "数据目录", icon: Database },
  { id: "admin" as const, label: "治理控制台", icon: Settings2 },
];

export function Sidebar({
  collapsed,
  mobileOpen,
  onCollapse,
  workspaces,
  selectedWorkspace,
  onWorkspace,
  threads,
  selectedThreadId,
  onThread,
  onManageThread,
  showArchivedThreads,
  onToggleArchivedThreads,
  threadListLoading,
  onNewThread,
  onNewWorkspace,
  view,
  onView,
  principal,
  onSignOut,
}: SidebarProps) {
  const [signingOut, setSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState("");

  const signOut = async () => {
    if (signingOut) return;
    setSigningOut(true);
    setSignOutError("");
    try {
      await onSignOut();
    } catch {
      setSignOutError("退出失败，请检查连接后重试");
    } finally {
      setSigningOut(false);
    }
  };

  return (
    <aside className={`sidebar ${collapsed ? "is-collapsed" : ""} ${mobileOpen ? "mobile-open" : ""}`}>
      <div className="sidebar-brand">
        <Logo compact={collapsed} />
        <button className="icon-button collapse-button" onClick={onCollapse} aria-label="折叠侧边栏">
          {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
        </button>
      </div>

      <button className="new-task" onClick={onNewThread} aria-label="新建任务">
        <Plus size={17} />
        {!collapsed && <span>新建任务</span>}
      </button>

      {!collapsed && (
        <div className="workspace-switcher-wrap">
          <label>工作空间</label>
          <div className="workspace-switcher">
            <select
              value={selectedWorkspace?.id ?? ""}
              onChange={(event) => {
                const workspace = workspaces.find((item) => item.id === event.target.value);
                if (workspace) onWorkspace(workspace);
              }}
              aria-label="选择工作空间"
            >
              {workspaces.map((workspace) => (
                <option key={workspace.id} value={workspace.id}>
                  {workspace.name}
                </option>
              ))}
            </select>
            <ChevronDown size={15} aria-hidden="true" />
          </div>
        </div>
      )}

      <nav className="primary-nav" aria-label="主要功能">
        {NAV_ITEMS.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.id}
              className={view === item.id ? "active" : ""}
              onClick={() => onView(item.id)}
              title={collapsed ? item.label : undefined}
            >
              <Icon size={18} />
              {!collapsed && <span>{item.label}</span>}
            </button>
          );
        })}
      </nav>

      {!collapsed && view === "assistant" && (
        <section className="thread-section">
          <div className="section-heading">
            <span>{showArchivedThreads ? "已归档任务" : "最近任务"}</span>
            <button
              type="button"
              onClick={onToggleArchivedThreads}
              aria-label={showArchivedThreads ? "返回最近任务" : "查看已归档任务"}
              aria-pressed={showArchivedThreads}
              title={showArchivedThreads ? "返回最近任务" : "查看已归档任务"}
              disabled={threadListLoading}
            >
              {showArchivedThreads ? <ArchiveRestore size={14} /> : <Archive size={14} />}
            </button>
          </div>
          <div className="thread-list">
            {threadListLoading && <div className="thread-list-loading"><i /> 正在载入…</div>}
            {!threadListLoading && threads.length === 0 && (
              <button className="thread-empty" onClick={onNewThread}>
                {showArchivedThreads ? "没有已归档任务" : "还没有任务，开始第一次调查"}
              </button>
            )}
            {threads.map((thread) => (
              <div className="thread-row" key={thread.id}>
                <button
                  className={selectedThreadId === thread.id ? "thread active" : "thread"}
                  onClick={() => onThread(thread)}
                >
                  <span className="thread-icon">
                    {thread.status === "ARCHIVED"
                      ? <Archive size={15} />
                      : <FileChartColumn size={15} />}
                  </span>
                  <span className="thread-copy">
                    <strong>{thread.title}</strong>
                    <small>
                      {thread.status === "ARCHIVED" ? "已归档 · " : ""}
                      {formatRelative(thread.updated_at)}
                    </small>
                  </span>
                </button>
                <button
                  type="button"
                  className="thread-manage"
                  onClick={() => onManageThread(thread)}
                  aria-label={`管理任务：${thread.title}`}
                  title="任务生命周期"
                >
                  <History size={14} />
                </button>
              </div>
            ))}
          </div>
        </section>
      )}

      <div className="sidebar-footer">
        <div className="user-menu" title={collapsed ? principal.display_name : undefined}>
          <span className="avatar">{initials(principal.display_name)}</span>
          {!collapsed && (
            <span>
              <strong>{principal.display_name}</strong>
              <small title={principal.roles.join(", ")}>
                {principal.department || principal.roles.join(" · ") || "已认证用户"}
              </small>
            </span>
          )}
        </div>
        {!collapsed && (
          <button className="workspace-add" onClick={onNewWorkspace} aria-label="新建工作空间">
            <Plus size={15} />
          </button>
        )}
        <button
          type="button"
          className="sign-out-button"
          onClick={() => void signOut()}
          aria-label="退出登录"
          title={signOutError || "退出登录"}
          disabled={signingOut}
        >
          {signingOut ? <i /> : <LogOut size={15} />}
        </button>
        {signOutError && <span className="sign-out-error" role="alert">{signOutError}</span>}
      </div>
    </aside>
  );
}

function formatRelative(value: string) {
  const date = new Date(value);
  const difference = Date.now() - date.getTime();
  const minutes = Math.floor(difference / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return date.toLocaleDateString("zh-CN", { month: "short", day: "numeric" });
}

function initials(displayName: string) {
  const segments = displayName.trim().split(/\s+/).filter(Boolean);
  if (segments.length > 1) {
    return `${segments[0][0] ?? ""}${segments.at(-1)?.[0] ?? ""}`.toUpperCase();
  }
  return Array.from(segments[0] ?? "O").slice(0, 2).join("").toUpperCase();
}
