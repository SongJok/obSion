"use client";

import {
  BookOpen,
  Bot,
  ChevronDown,
  Database,
  FolderKanban,
  FileChartColumn,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Workflow,
} from "lucide-react";

import type { Thread, ViewName, Workspace } from "@/lib/types";
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
  onNewThread: () => void;
  onNewWorkspace: () => void;
  view: ViewName;
  onView: (view: ViewName) => void;
}

const NAV_ITEMS = [
  { id: "assistant" as const, label: "智能工作台", icon: Bot },
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
  onNewThread,
  onNewWorkspace,
  view,
  onView,
}: SidebarProps) {
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
            <span>最近任务</span>
            <Search size={14} />
          </div>
          <div className="thread-list">
            {threads.length === 0 && (
              <button className="thread-empty" onClick={onNewThread}>
                还没有任务，开始第一次调查
              </button>
            )}
            {threads.map((thread) => (
              <button
                key={thread.id}
                className={selectedThreadId === thread.id ? "thread active" : "thread"}
                onClick={() => onThread(thread)}
              >
                <span className="thread-icon">
                  <FileChartColumn size={15} />
                </span>
                <span className="thread-copy">
                  <strong>{thread.title}</strong>
                  <small>{formatRelative(thread.updated_at)}</small>
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      <div className="sidebar-footer">
        <button className="user-menu" title={collapsed ? "Local Administrator" : undefined}>
          <span className="avatar">LA</span>
          {!collapsed && (
            <span>
              <strong>Local Administrator</strong>
              <small>开发环境</small>
            </span>
          )}
        </button>
        {!collapsed && (
          <button className="workspace-add" onClick={onNewWorkspace} aria-label="新建工作空间">
            <Plus size={15} />
          </button>
        )}
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
