"use client";

import { GitFork, History, Menu, PanelRightOpen, Plus, ShieldCheck, X } from "lucide-react";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { streamRunEvents } from "@/lib/app-server";
import type {
  Artifact,
  Claim,
  ConversationSnapshot,
  Evidence,
  MemorySnapshot,
  MessageBundle,
  Run,
  RunEvent,
  RunFeedback,
  RunFeedbackRating,
  RunStep,
  SessionPrincipal,
  Thread,
  ThreadEvent,
  ViewName,
  Workspace,
} from "@/lib/types";
import { AdminView } from "./admin-view";
import { ActionsView } from "./actions-view";
import { AutomationView } from "./automation-view";
import { ArtifactsView } from "./artifacts-view";
import { Composer } from "./composer";
import { Conversation } from "./conversation";
import { CollaborationView } from "./collaboration-view";
import { DataView } from "./data-view";
import { EmptyState } from "./empty-state";
import { KnowledgeView } from "./knowledge-view";
import { RuntimeInspector } from "./runtime-inspector";
import { Sidebar } from "./sidebar";
import { ThreadLifecycleModal } from "./thread-lifecycle-modal";

const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

interface WorkbenchProps {
  principal: SessionPrincipal;
  onSignOut: () => Promise<void>;
}

export function Workbench({ principal, onSignOut }: WorkbenchProps) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspace, setWorkspace] = useState<Workspace>();
  const [threads, setThreads] = useState<Thread[]>([]);
  const [thread, setThread] = useState<Thread>();
  const [showArchivedThreads, setShowArchivedThreads] = useState(false);
  const [threadListLoading, setThreadListLoading] = useState(false);
  const [managedThread, setManagedThread] = useState<Thread>();
  const [threadEvents, setThreadEvents] = useState<ThreadEvent[]>([]);
  const [threadLifecycleLoading, setThreadLifecycleLoading] = useState(false);
  const [threadLifecycleAction, setThreadLifecycleAction] = useState<
    "archive" | "resume" | "fork"
  >();
  const [messages, setMessages] = useState<MessageBundle[]>([]);
  const [feedbackByRun, setFeedbackByRun] = useState<
    Record<string, RunFeedback | null | undefined>
  >({});
  const [run, setRun] = useState<Run>();
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [steps, setSteps] = useState<RunStep[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
  const [memories, setMemories] = useState<MemorySnapshot[]>([]);
  const [conversationContext, setConversationContext] = useState<ConversationSnapshot[]>([]);
  const [claims, setClaims] = useState<Claim[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [value, setValue] = useState("");
  const [view, setView] = useState<ViewName>("assistant");
  const [collapsed, setCollapsed] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [mobileInspectorOpen, setMobileInspectorOpen] = useState(false);
  const [workspaceModal, setWorkspaceModal] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attachments, setAttachments] = useState<Artifact[]>([]);
  const [uploading, setUploading] = useState(false);
  const [contextPickerOpen, setContextPickerOpen] = useState(false);
  const [contextArtifacts, setContextArtifacts] = useState<Artifact[]>([]);
  const [contextLoading, setContextLoading] = useState(false);
  const [replayingRunId, setReplayingRunId] = useState<string>();
  const [feedbackPendingRunId, setFeedbackPendingRunId] = useState<string>();
  const requestGeneration = useRef(0);
  const threadLifecycleGeneration = useRef(0);

  useEffect(() => () => {
    ++requestGeneration.current;
    ++threadLifecycleGeneration.current;
  }, []);

  const clearInspection = useCallback(() => {
    setRun(undefined);
    setEvents([]);
    setSteps([]);
    setEvidence([]);
    setMemories([]);
    setConversationContext([]);
    setClaims([]);
    setArtifacts([]);
    setFeedbackByRun({});
  }, []);

  const resetThread = useCallback(() => {
    ++requestGeneration.current;
    setThread(undefined);
    setMessages([]);
    setAttachments([]);
    setContextPickerOpen(false);
    setContextArtifacts([]);
    setValue("");
    clearInspection();
  }, [clearInspection]);

  const loadInspection = useCallback(async (target: Run) => {
    const [
      nextEvents,
      nextSteps,
      nextEvidence,
      nextMemories,
      nextConversation,
      nextClaims,
      nextArtifacts,
    ] = await Promise.all([
      api.listEvents(target.id),
      api.listSteps(target.id),
      api.listEvidence(target.id),
      api.listRunMemories(target.id),
      api.listRunConversation(target.id),
      api.listClaims(target.id),
      api.listArtifacts(target.id),
    ]);
    setRun(target);
    setEvents(nextEvents);
    setSteps(nextSteps);
    setEvidence(nextEvidence);
    setMemories(nextMemories);
    setConversationContext(nextConversation);
    setClaims(nextClaims);
    setArtifacts(nextArtifacts);
  }, []);

  const openThread = useCallback(async (selected: Thread) => {
    const generation = ++requestGeneration.current;
    setThread(selected);
    setView("assistant");
    setLoading(true);
    setError("");
    try {
      const [turns, runs] = await Promise.all([
        api.listTurns(selected.id),
        api.listThreadRuns(selected.id),
      ]);
      const detailsByRun = await Promise.all(
        runs.map(async (item) => {
          const [runArtifacts, feedback] = await Promise.all([
            api.listArtifacts(item.id),
            api.getRunFeedback(item.id),
          ]);
          return { runArtifacts, feedback };
        }),
      );
      if (generation !== requestGeneration.current) return;
      const runByTurn = new Map(runs.map((item, index) => [item.turn_id, {
        item,
        artifacts: detailsByRun[index].runArtifacts,
        artifact: primaryArtifact(detailsByRun[index].runArtifacts),
      }]));
      setFeedbackByRun(Object.fromEntries(
        runs.map((item, index) => [item.id, detailsByRun[index].feedback]),
      ));
      setMessages(turns.map((turn) => ({
        turn,
        run: runByTurn.get(turn.id)?.item,
        artifact: runByTurn.get(turn.id)?.artifact,
        artifacts: runByTurn.get(turn.id)?.artifacts,
      })));
      const latest = runs.at(-1);
      if (latest) await loadInspection(latest);
      else clearInspection();
    } catch (caught) {
      if (generation === requestGeneration.current) {
        setError(caught instanceof Error ? caught.message : "无法打开任务");
      }
    } finally {
      if (generation === requestGeneration.current) setLoading(false);
    }
  }, [clearInspection, loadInspection]);

  const selectWorkspace = useCallback(async (selected: Workspace) => {
    setWorkspace(selected);
    resetThread();
    setShowArchivedThreads(false);
    setManagedThread(undefined);
    setLoading(true);
    setError("");
    try {
      setThreads(await api.listThreads(selected.id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取工作空间");
    } finally {
      setLoading(false);
    }
  }, [resetThread]);

  useEffect(() => {
    let active = true;
    api.listWorkspaces()
      .then(async (items) => {
        if (!active) return;
        setWorkspaces(items);
        if (items[0]) await selectWorkspace(items[0]);
        else {
          setWorkspaceModal(true);
          setLoading(false);
        }
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(caught instanceof Error ? caught.message : "无法连接 Obsion 控制面");
          setLoading(false);
        }
      });
    return () => { active = false; };
  }, [selectWorkspace]);

  const toggleArchivedThreads = useCallback(async () => {
    if (!workspace) return;
    const archived = !showArchivedThreads;
    resetThread();
    setManagedThread(undefined);
    setShowArchivedThreads(archived);
    setThreadListLoading(true);
    setError("");
    try {
      const items = await api.listThreads(workspace.id, archived);
      setThreads(items.filter((item) => item.status === (archived ? "ARCHIVED" : "ACTIVE")));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取任务列表");
    } finally {
      setThreadListLoading(false);
    }
  }, [resetThread, showArchivedThreads, workspace]);

  const manageThread = useCallback(async (target: Thread) => {
    const generation = ++threadLifecycleGeneration.current;
    setManagedThread(target);
    setThreadEvents([]);
    setThreadLifecycleAction(undefined);
    setThreadLifecycleLoading(true);
    setMobileNavOpen(false);
    setError("");
    try {
      const items = await api.listThreadEvents(target.id);
      if (generation === threadLifecycleGeneration.current) setThreadEvents(items);
    } catch (caught) {
      if (generation === threadLifecycleGeneration.current) {
        setError(caught instanceof Error ? caught.message : "无法读取任务生命周期");
      }
    } finally {
      if (generation === threadLifecycleGeneration.current) setThreadLifecycleLoading(false);
    }
  }, []);

  const refreshManagedThreadEvents = useCallback(async (target: Thread) => {
    const generation = ++threadLifecycleGeneration.current;
    setThreadLifecycleLoading(true);
    try {
      const items = await api.listThreadEvents(target.id);
      if (generation === threadLifecycleGeneration.current) setThreadEvents(items);
    } finally {
      if (generation === threadLifecycleGeneration.current) setThreadLifecycleLoading(false);
    }
  }, []);

  const archiveManagedThread = useCallback(async () => {
    if (!managedThread) return;
    if (thread?.id === managedThread.id && run && !TERMINAL.has(run.status)) return;
    setThreadLifecycleAction("archive");
    setError("");
    try {
      const archived = await api.archiveThread(managedThread.id);
      setManagedThread(archived);
      if (thread?.id === archived.id) setThread(archived);
      setShowArchivedThreads(true);
      if (workspace) {
        const items = await api.listThreads(workspace.id, true);
        setThreads(items.filter((item) => item.status === "ARCHIVED"));
      }
      await refreshManagedThreadEvents(archived);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法归档任务");
    } finally {
      setThreadLifecycleAction(undefined);
    }
  }, [managedThread, refreshManagedThreadEvents, run, thread, workspace]);

  const resumeManagedThread = useCallback(async () => {
    if (!managedThread) return;
    setThreadLifecycleAction("resume");
    setError("");
    try {
      const resumed = await api.resumeThread(managedThread.id);
      setShowArchivedThreads(false);
      if (workspace) {
        const items = await api.listThreads(workspace.id);
        setThreads(items.filter((item) => item.status === "ACTIVE"));
      }
      setManagedThread(undefined);
      await openThread(resumed);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法恢复任务");
    } finally {
      setThreadLifecycleAction(undefined);
    }
  }, [managedThread, openThread, workspace]);

  const forkManagedThread = useCallback(async (title: string) => {
    if (!managedThread) return;
    setThreadLifecycleAction("fork");
    setError("");
    try {
      const turns = await api.listTurns(managedThread.id);
      const fromTurnId = turns.at(-1)?.id;
      const forked = await api.forkThread(managedThread.id, {
        title,
        ...(fromTurnId ? { from_turn_id: fromTurnId } : {}),
      });
      setShowArchivedThreads(false);
      if (workspace) {
        const items = await api.listThreads(workspace.id);
        setThreads(items.filter((item) => item.status === "ACTIVE"));
      }
      setManagedThread(undefined);
      await openThread(forked);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法建立任务分支");
    } finally {
      setThreadLifecycleAction(undefined);
    }
  }, [managedThread, openThread, workspace]);

  const pollRun = useCallback(async (initial: Run, generation: number) => {
    let current = initial;
    let cursor = 0;
    let consecutiveFailures = 0;
    let stopStream: (() => void) | undefined;
    let acceptStream = true;
    void streamRunEvents(current.id, cursor, (event) => {
        if (generation !== requestGeneration.current) return;
        cursor = Math.max(cursor, event.run_sequence ?? 0);
        setEvents((previous) =>
          previous.some((item) => item.id === event.id)
            ? previous
            : [...previous, event].sort(
                (left, right) =>
                  (left.run_sequence ?? 0) - (right.run_sequence ?? 0),
              ),
        );
      })
      .then((stop) => {
        if (acceptStream) stopStream = stop;
        else stop();
      })
      .catch(() => {
        // REST cursor reconciliation below remains the compatibility path when a
        // proxy does not support WebSocket upgrade or browser OIDC initialization.
      });
    try {
      while (generation === requestGeneration.current) {
        try {
          const [
            nextRun,
            nextEvents,
            nextSteps,
            nextEvidence,
            nextMemories,
            nextConversation,
            nextClaims,
            nextArtifacts,
          ] = await Promise.all([
            api.getRun(current.id),
            api.listEvents(current.id, cursor),
            api.listSteps(current.id),
            api.listEvidence(current.id),
            api.listRunMemories(current.id),
            api.listRunConversation(current.id),
            api.listClaims(current.id),
            api.listArtifacts(current.id),
          ]);
          if (generation !== requestGeneration.current) return;
          consecutiveFailures = 0;
          current = nextRun;
          if (nextEvents.length) {
            cursor = Math.max(
              cursor,
              ...nextEvents.map((event) => event.run_sequence ?? 0),
            );
            setEvents((previous) => {
              const known = new Set(previous.map((event) => event.id));
              return [
                ...previous,
                ...nextEvents.filter((event) => !known.has(event.id)),
              ].sort(
                (left, right) =>
                  (left.run_sequence ?? 0) - (right.run_sequence ?? 0),
              );
            });
          }
          setRun(nextRun);
          setSteps(nextSteps);
          setEvidence(nextEvidence);
          setMemories(nextMemories);
          setConversationContext(nextConversation);
          setClaims(nextClaims);
          setArtifacts(nextArtifacts);
          setMessages((previous) => previous.map((bundle) =>
            bundle.run?.id === nextRun.id
              ? {
                  ...bundle,
                  run: nextRun,
                  artifact: primaryArtifact(nextArtifacts) ?? bundle.artifact,
                  artifacts: nextArtifacts,
                }
              : bundle,
          ));
          if (TERMINAL.has(nextRun.status)) return;
          await new Promise((resolve) => window.setTimeout(resolve, 650));
        } catch (caught) {
          consecutiveFailures += 1;
          if (consecutiveFailures >= 4) {
            if (generation === requestGeneration.current) {
              setError(
                caught instanceof Error
                  ? `运行仍在后台继续，但状态同步暂时中断：${caught.message}`
                  : "运行状态同步暂时中断",
              );
            }
            return;
          }
          await new Promise((resolve) =>
            window.setTimeout(resolve, 500 * consecutiveFailures),
          );
        }
      }
    } finally {
      acceptStream = false;
      stopStream?.();
    }
  }, []);

  const submit = useCallback(async () => {
    const input = value.trim();
    if (!input || run && !TERMINAL.has(run.status)) return;
    if (thread?.status === "ARCHIVED") {
      setError("此任务已归档，请先从任务生命周期面板恢复后继续。");
      return;
    }
    if (!workspace) {
      setWorkspaceModal(true);
      return;
    }
    setError("");
    setValue("");
    setView("assistant");
    setInspectorOpen(true);
    try {
      let activeThread = thread;
      if (!activeThread) {
        activeThread = await api.createThread(workspace.id, input.slice(0, 48));
        setThread(activeThread);
        setShowArchivedThreads(false);
        setThreads((previous) => [activeThread!, ...previous]);
      }
      const created = await api.createTurn(
        activeThread.id,
        input,
        attachments.map((artifact) => ({
          type: "artifact",
          artifact_id: artifact.id,
          media_type: artifact.media_type,
          title: artifact.title,
        })),
      );
      const generation = ++requestGeneration.current;
      setMessages((previous) => [...previous, { turn: created.turn, run: created.run }]);
      setRun(created.run);
      setEvents([]);
      setSteps([]);
      setEvidence([]);
      setMemories([]);
      setClaims([]);
      setAttachments([]);
      setContextPickerOpen(false);
      void pollRun(created.run, generation);
    } catch (caught) {
      setValue(input);
      setError(caught instanceof Error ? caught.message : "任务创建失败");
    }
  }, [attachments, pollRun, run, thread, value, workspace]);

  const attachFiles = useCallback(async (files: File[]) => {
    if (!workspace) {
      setWorkspaceModal(true);
      return;
    }
    setUploading(true);
    setError("");
    try {
      const uploaded: Artifact[] = [];
      for (const file of files) {
        const form = new FormData();
        form.set("file", file);
        form.set("title", file.name);
        form.set("kind", "FILE");
        form.set("classification", workspace.classification || "INTERNAL");
        uploaded.push(await api.uploadArtifact(workspace.id, form));
      }
      setAttachments((previous) => [...previous, ...uploaded]);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "附件上传失败");
    } finally {
      setUploading(false);
    }
  }, [workspace]);

  const openContextPicker = useCallback(async () => {
    if (!workspace) {
      setWorkspaceModal(true);
      return;
    }
    setContextPickerOpen(true);
    setContextLoading(true);
    setError("");
    try {
      setContextArtifacts(await api.listWorkspaceArtifacts(workspace.id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取工作区上下文");
    } finally {
      setContextLoading(false);
    }
  }, [workspace]);

  const cancel = useCallback(async () => {
    if (!run || TERMINAL.has(run.status)) return;
    try {
      const cancelled = await api.cancelRun(run.id);
      ++requestGeneration.current;
      setRun(cancelled);
      setMessages((previous) => previous.map((bundle) =>
        bundle.run?.id === cancelled.id ? { ...bundle, run: cancelled } : bundle,
      ));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法停止运行");
    }
  }, [run]);

  const replay = useCallback(async (target: Run) => {
    if (!TERMINAL.has(target.status) || run && !TERMINAL.has(run.status)) return;
    setReplayingRunId(target.id);
    setError("");
    try {
      const replayed = await api.replayRun(target.id);
      const generation = ++requestGeneration.current;
      setRun(replayed);
      setEvents([]);
      setSteps([]);
      setEvidence([]);
      setMemories([]);
      setClaims([]);
      setArtifacts([]);
      setFeedbackByRun((previous) => ({ ...previous, [replayed.id]: null }));
      setMessages((previous) => previous.map((bundle) =>
        bundle.turn.id === replayed.turn_id
          ? { ...bundle, run: replayed, artifact: undefined, artifacts: [] }
          : bundle,
      ));
      void pollRun(replayed, generation);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法回放运行快照");
    } finally {
      setReplayingRunId(undefined);
    }
  }, [pollRun, run]);

  const recordFeedback = useCallback(async (
    target: Run,
    rating: RunFeedbackRating,
    reason: string,
  ) => {
    setFeedbackPendingRunId(target.id);
    setError("");
    try {
      const current = feedbackByRun[target.id];
      const recorded = await api.recordRunFeedback(target.id, {
        rating,
        reason,
        ...(current ? { expected_version: current.version } : {}),
      });
      setFeedbackByRun((previous) => ({ ...previous, [target.id]: recorded }));
      return true;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法保存反馈");
      return false;
    } finally {
      setFeedbackPendingRunId(undefined);
    }
  }, [feedbackByRun]);

  const newThread = () => {
    resetThread();
    setView("assistant");
    setManagedThread(undefined);
    if (showArchivedThreads && workspace) {
      setShowArchivedThreads(false);
      setThreadListLoading(true);
      void api.listThreads(workspace.id)
        .then((items) => setThreads(items.filter((item) => item.status === "ACTIVE")))
        .catch((caught: unknown) => {
          setError(caught instanceof Error ? caught.message : "无法读取任务列表");
        })
        .finally(() => setThreadListLoading(false));
    }
  };

  const running = Boolean(run && !TERMINAL.has(run.status));

  return (
    <div className="app-shell">
      <Sidebar
        collapsed={collapsed}
        mobileOpen={mobileNavOpen}
        onCollapse={() => {
          if (window.innerWidth <= 880) setMobileNavOpen(false);
          else setCollapsed((current) => !current);
        }}
        workspaces={workspaces}
        selectedWorkspace={workspace}
        onWorkspace={(item) => { setMobileNavOpen(false); void selectWorkspace(item); }}
        threads={threads}
        selectedThreadId={thread?.id}
        onThread={(item) => { setMobileNavOpen(false); void openThread(item); }}
        onManageThread={(item) => void manageThread(item)}
        showArchivedThreads={showArchivedThreads}
        onToggleArchivedThreads={() => void toggleArchivedThreads()}
        threadListLoading={threadListLoading}
        onNewThread={() => { newThread(); setMobileNavOpen(false); }}
        onNewWorkspace={() => { setWorkspaceModal(true); setMobileNavOpen(false); }}
        view={view}
        onView={(nextView) => { setView(nextView); setMobileNavOpen(false); }}
        principal={principal}
        onSignOut={onSignOut}
      />

      {mobileNavOpen && (
        <button
          type="button"
          className="mobile-scrim navigation-scrim"
          onClick={() => setMobileNavOpen(false)}
          aria-label="关闭导航"
        />
      )}

      <section className="workspace-shell">
        {view !== "assistant" && <button className="icon-button feature-mobile-menu" onClick={() => { setCollapsed(false); setMobileNavOpen(true); }} aria-label="打开导航"><Menu size={19} /></button>}
        {view === "assistant" ? (
          <>
            <header className="workspace-header">
              <button className="icon-button mobile-menu" onClick={() => { setCollapsed(false); setMobileNavOpen(true); }} aria-label="打开导航"><Menu size={19} /></button>
              <div>
                <strong>{thread?.title ?? "智能工作台"}</strong>
                <span>{workspace?.name ?? "选择工作空间"}</span>
                {thread?.parent_thread_id && (
                  <span className="thread-branch-pill"><GitFork size={12} /> 分支任务</span>
                )}
                {thread?.status === "ARCHIVED" && (
                  <span className="thread-archived-pill">已归档</span>
                )}
              </div>
              <div className="header-actions">
                <span className="governance-pill"><ShieldCheck size={14} /> 策略已启用</span>
                {thread && (
                  <button
                    className="icon-button"
                    onClick={() => void manageThread(thread)}
                    aria-label="管理任务生命周期"
                    title="归档、恢复、分支与事件历史"
                  >
                    <History size={18} />
                  </button>
                )}
                {!inspectorOpen && <button className="icon-button" onClick={() => setInspectorOpen(true)} title="打开运行详情"><PanelRightOpen size={19} /></button>}
                <button className="icon-button mobile-inspector-trigger" onClick={() => { setInspectorOpen(true); setMobileInspectorOpen(true); }} title="打开运行详情" aria-label="打开运行详情"><PanelRightOpen size={19} /></button>
              </div>
            </header>

            <div className="assistant-layout">
              <main className="chat-panel">
                {error && <div className="global-error"><span>{error}</span><button onClick={() => setError("")}><X size={15} /></button></div>}
                <div className="chat-scroll">
                  {!messages.length && !loading ? <EmptyState onSuggestion={setValue} /> : (
                    <Conversation
                      messages={messages}
                      feedbackByRun={feedbackByRun}
                      feedbackPendingRunId={feedbackPendingRunId}
                      replayingRunId={replayingRunId}
                      replayDisabled={running}
                      onFeedback={recordFeedback}
                      onReplay={(target) => void replay(target)}
                    />
                  )}
                  {loading && <div className="loading-state"><i /><span>正在载入可回放记录…</span></div>}
                </div>
                <Composer
                  value={value}
                  onChange={setValue}
                  onSubmit={() => void submit()}
                  onCancel={() => void cancel()}
                  running={running}
                  disabled={loading || thread?.status === "ARCHIVED"}
                  placeholder={thread?.status === "ARCHIVED" ? "此任务已归档，恢复后可以继续…" : undefined}
                  note={thread?.status === "ARCHIVED"
                    ? "归档任务保持完整历史，只读检查不会创建新的运行。"
                    : undefined}
                  attachments={attachments}
                  uploading={uploading}
                  onAttach={(files) => void attachFiles(files)}
                  onRemoveAttachment={(artifactId) =>
                    setAttachments((items) => items.filter((item) => item.id !== artifactId))
                  }
                  contextArtifacts={contextArtifacts}
                  contextOpen={contextPickerOpen}
                  contextLoading={contextLoading}
                  onOpenContext={() => void openContextPicker()}
                  onCloseContext={() => setContextPickerOpen(false)}
                  onAddContext={(artifact) =>
                    setAttachments((items) => items.some((item) => item.id === artifact.id)
                      ? items
                      : [...items, artifact])
                  }
                />
              </main>
              {mobileInspectorOpen && (
                <button
                  type="button"
                  className="mobile-scrim inspector-scrim"
                  onClick={() => setMobileInspectorOpen(false)}
                  aria-label="关闭运行详情"
                />
              )}
              <RuntimeInspector open={inspectorOpen} mobileVisible={mobileInspectorOpen} onClose={() => { setInspectorOpen(false); setMobileInspectorOpen(false); }} onReplay={() => { if (run) void replay(run); }} replaying={Boolean(replayingRunId)} run={run} events={events} steps={steps} evidence={evidence} memories={memories} conversation={conversationContext} claims={claims} artifacts={artifacts} />
            </div>
          </>
        ) : view === "collaboration" ? <CollaborationView key={workspace?.id ?? "no-workspace"} workspace={workspace} /> : view === "automation" ? <AutomationView key={workspace?.id ?? "no-workspace"} workspace={workspace} /> : view === "actions" ? <ActionsView key={workspace?.id ?? "no-workspace"} workspace={workspace} /> : view === "artifacts" ? <ArtifactsView key={workspace?.id ?? "no-workspace"} workspace={workspace} /> : view === "knowledge" ? <KnowledgeView /> : view === "data" ? <DataView /> : <AdminView />}
      </section>

      {workspaceModal && (
        <WorkspaceModal
          onClose={workspaces.length ? () => setWorkspaceModal(false) : undefined}
          onCreate={async (name, description) => {
            const created = await api.createWorkspace(name, description);
            setWorkspaces((previous) => [...previous, created]);
            await selectWorkspace(created);
            setWorkspaceModal(false);
          }}
        />
      )}
      {managedThread && (
        <ThreadLifecycleModal
          key={managedThread.id}
          thread={managedThread}
          events={threadEvents}
          loading={threadLifecycleLoading}
          pendingAction={threadLifecycleAction}
          running={Boolean(
            thread?.id === managedThread.id && run && !TERMINAL.has(run.status)
          )}
          onClose={() => {
            ++threadLifecycleGeneration.current;
            setManagedThread(undefined);
            setThreadEvents([]);
            setThreadLifecycleAction(undefined);
          }}
          onArchive={() => void archiveManagedThread()}
          onResume={() => void resumeManagedThread()}
          onFork={(title) => void forkManagedThread(title)}
        />
      )}
    </div>
  );
}

function primaryArtifact(items: Artifact[] | undefined) {
  return items?.findLast((item) => item.kind === "TEXT" && Boolean(item.inline_content?.markdown))
    ?? items?.at(-1);
}

function WorkspaceModal({ onClose, onCreate }: { onClose?: () => void; onCreate: (name: string, description: string) => Promise<void> }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    setSaving(true);
    setError("");
    try { await onCreate(name.trim(), description.trim()); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "创建失败"); setSaving(false); }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <form className="workspace-modal" onSubmit={(event) => void submit(event)}>
        <header><span className="modal-icon"><Plus size={19} /></span><div><h2>新建工作空间</h2><p>任务、权限、数据与记忆的隔离边界</p></div>{onClose && <button type="button" className="icon-button" onClick={onClose}><X size={18} /></button>}</header>
        <label><span>名称</span><input autoFocus value={name} onChange={(event) => setName(event.target.value)} maxLength={120} placeholder="例如：生产运营" /></label>
        <label><span>描述</span><textarea value={description} onChange={(event) => setDescription(event.target.value)} maxLength={1000} rows={3} placeholder="这个空间负责哪些业务与调查任务？" /></label>
        {error && <p className="form-error">{error}</p>}
        <footer>{onClose && <button type="button" className="secondary-button" onClick={onClose}>取消</button>}<button className="primary-button" disabled={saving || !name.trim()}>{saving ? "正在创建…" : "创建工作空间"}</button></footer>
      </form>
    </div>
  );
}
