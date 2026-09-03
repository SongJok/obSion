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
  Turn,
  ViewName,
  Workspace,
} from "@/lib/types";
import { AdminView } from "./admin-view";
import { ActionsView } from "./actions-view";
import { AutomationView } from "./automation-view";
import { ArtifactsView } from "./artifacts-view";
import { FilesView } from "./files-view";
import { ReportsView } from "./reports-view";
import { DashboardsView } from "./dashboards-view";
import { SqlView } from "./sql-view";
import { EvidenceView } from "./evidence-view";
import { TimelineView } from "./timeline-view";
import { Composer } from "./composer";
import { Conversation } from "./conversation";
import { CollaborationView } from "./collaboration-view";
import { CodeView } from "./code-view";
import { DataView } from "./data-view";
import { EvalView } from "./eval-view";
import { StudioView } from "./studio-view";
import { EmptyState } from "./empty-state";
import { KnowledgeView } from "./knowledge-view";
import { RuntimeInspector } from "./runtime-inspector";
import { Sidebar } from "./sidebar";
import { ThreadLifecycleModal } from "./thread-lifecycle-modal";

const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

type StreamState = "idle" | "live" | "polling" | "interrupted";

interface WorkbenchProps {
  principal: SessionPrincipal;
  onSignOut: () => Promise<void>;
}

interface InspectionSnapshot {
  run: Run;
  events: RunEvent[];
  steps: RunStep[];
  evidence: Evidence[];
  memories: MemorySnapshot[];
  conversation: ConversationSnapshot[];
  claims: Claim[];
  artifacts: Artifact[];
}

class InspectionOwnershipError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "InspectionOwnershipError";
  }
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
  const [streamState, setStreamState] = useState<StreamState>("idle");
  const [submitting, setSubmitting] = useState(false);
  const submitInFlight = useRef(false);
  const pollRunRef = useRef<(
    initial: Run,
    generation: number,
    expectedWorkspaceId: string,
  ) => Promise<void>>(undefined);
  const selectionGeneration = useRef(0);
  const contextGeneration = useRef(0);
  const uploadGeneration = useRef(0);
  const feedbackGeneration = useRef(0);
  const threadLifecycleGeneration = useRef(0);

  useEffect(() => () => {
    ++selectionGeneration.current;
    ++contextGeneration.current;
    ++uploadGeneration.current;
    ++feedbackGeneration.current;
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
    setStreamState("idle");
  }, []);

  const closeContextPicker = useCallback(() => {
    ++contextGeneration.current;
    setContextPickerOpen(false);
    setContextArtifacts([]);
    setContextLoading(false);
  }, []);

  const resetThread = useCallback(() => {
    ++selectionGeneration.current;
    ++uploadGeneration.current;
    ++feedbackGeneration.current;
    ++threadLifecycleGeneration.current;
    setThread(undefined);
    setMessages([]);
    setAttachments([]);
    setUploading(false);
    closeContextPicker();
    setThreadListLoading(false);
    setManagedThread(undefined);
    setThreadEvents([]);
    setThreadLifecycleLoading(false);
    setThreadLifecycleAction(undefined);
    setFeedbackPendingRunId(undefined);
    setReplayingRunId(undefined);
    submitInFlight.current = false;
    setSubmitting(false);
    setValue("");
    clearInspection();
  }, [clearInspection, closeContextPicker]);

  const loadInspection = useCallback(async (
    target: Run,
    expectedWorkspaceId: string,
  ): Promise<InspectionSnapshot> => {
    assertRunWorkspace(target, expectedWorkspaceId);
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
    const snapshot = {
      run: target,
      events: nextEvents,
      steps: nextSteps,
      evidence: nextEvidence,
      memories: nextMemories,
      conversation: nextConversation,
      claims: nextClaims,
      artifacts: nextArtifacts,
    };
    assertInspectionOwnership(snapshot);
    return snapshot;
  }, []);

  const applyInspection = useCallback((snapshot: InspectionSnapshot) => {
    setRun(snapshot.run);
    setEvents(snapshot.events);
    setSteps(snapshot.steps);
    setEvidence(snapshot.evidence);
    setMemories(snapshot.memories);
    setConversationContext(snapshot.conversation);
    setClaims(snapshot.claims);
    setArtifacts(snapshot.artifacts);
  }, []);

  const openRunInspection = useCallback(
    async (runId: string) => {
      const generation = ++selectionGeneration.current;
      const workspaceId = workspace?.id;
      setLoading(true);
      setError("");
      try {
        const target = await api.getRun(runId);
        if (generation !== selectionGeneration.current) return;
        if (target.id !== runId) {
          throw new InspectionOwnershipError("来源 Run 响应与所选 Run 不一致");
        }
        if (!workspaceId) {
          throw new InspectionOwnershipError("当前未选择工作空间");
        }
        assertRunWorkspace(target, workspaceId);
        const snapshot = await loadInspection(target, workspaceId);
        if (generation !== selectionGeneration.current) return;
        applyInspection(snapshot);
        setView("assistant");
        setInspectorOpen(true);
        if (TERMINAL.has(target.status)) setStreamState("idle");
        else void pollRunRef.current?.(target, generation, workspaceId);
      } catch (caught) {
        if (generation !== selectionGeneration.current) return;
        setError(caught instanceof Error ? caught.message : "无法打开来源 Run");
      } finally {
        if (generation === selectionGeneration.current) setLoading(false);
      }
    },
    [applyInspection, loadInspection, workspace?.id],
  );

  const openThread = useCallback(async (selected: Thread, inspectRunId?: string) => {
    const workspaceId = workspace?.id;
    if (!workspaceId || selected.workspace_id !== workspaceId) {
      setError("任务不属于当前工作空间");
      return;
    }
    const generation = ++selectionGeneration.current;
    const keepsVerifiedProjection = thread?.id === selected.id;
    ++uploadGeneration.current;
    ++feedbackGeneration.current;
    if (!keepsVerifiedProjection) {
      setThread(selected);
      setMessages([]);
      setFeedbackByRun({});
      clearInspection();
    }
    setAttachments([]);
    setUploading(false);
    setFeedbackPendingRunId(undefined);
    setReplayingRunId(undefined);
    closeContextPicker();
    setView("assistant");
    setLoading(true);
    setError("");
    try {
      const [turns, runs] = await Promise.all([
        api.listTurns(selected.id),
        api.listThreadRuns(selected.id),
      ]);
      assertThreadOwnership(selected, workspaceId, turns, runs);
      const detailsByRun = await Promise.all(
        runs.map(async (item) => {
          const [runArtifacts, feedback] = await Promise.all([
            api.listArtifacts(item.id),
            api.getRunFeedback(item.id),
          ]);
          assertRunArtifacts(item.id, workspaceId, runArtifacts);
          assertRunFeedback(item.id, feedback);
          return { runArtifacts, feedback };
        }),
      );
      const inspectedRun = inspectRunId
        ? runs.find((item) => item.id === inspectRunId)
        : runs.at(-1);
      if (inspectRunId && !inspectedRun) {
        throw new InspectionOwnershipError("来源 Run 不属于所选任务");
      }
      const inspection = inspectedRun
        ? await loadInspection(inspectedRun, workspaceId)
        : undefined;
      if (generation !== selectionGeneration.current) return;
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
      if (inspection) applyInspection(inspection);
      else clearInspection();
      setLoading(false);
      if (inspection && !TERMINAL.has(inspection.run.status)) {
        void pollRunRef.current?.(inspection.run, generation, workspaceId);
      }
    } catch (caught) {
      if (generation === selectionGeneration.current) {
        setError(caught instanceof Error ? caught.message : "无法打开任务");
      }
    } finally {
      if (generation === selectionGeneration.current) setLoading(false);
    }
  }, [applyInspection, clearInspection, closeContextPicker, loadInspection, thread?.id, workspace?.id]);

  const openScopedRun = useCallback(async (runId: string, threadId?: string) => {
    if (!workspace) {
      setError("当前未选择工作空间");
      return;
    }
    if (!threadId) {
      await openRunInspection(runId);
      return;
    }
    const generation = ++selectionGeneration.current;
    const workspaceId = workspace.id;
    setLoading(true);
    setError("");
    try {
      const items = await api.listThreads(workspaceId);
      assertWorkspaceThreads(workspaceId, items);
      if (generation !== selectionGeneration.current) return;
      const selected = items.find((item) => item.id === threadId);
      if (!selected) {
        throw new InspectionOwnershipError("来源 Run 不属于当前工作空间中的任务");
      }
      await openThread(selected, runId);
    } catch (caught) {
      if (generation === selectionGeneration.current) {
        setError(caught instanceof Error ? caught.message : "无法打开来源 Run");
      }
    } finally {
      if (generation === selectionGeneration.current) setLoading(false);
    }
  }, [openRunInspection, openThread, workspace]);

  const selectWorkspace = useCallback(async (selected: Workspace) => {
    resetThread();
    const generation = selectionGeneration.current;
    setWorkspace(selected);
    setThreads([]);
    setShowArchivedThreads(false);
    setManagedThread(undefined);
    setLoading(true);
    setError("");
    try {
      const items = await api.listThreads(selected.id);
      assertWorkspaceThreads(selected.id, items);
      if (generation !== selectionGeneration.current) return;
      setThreads(items.filter((item) => item.status === "ACTIVE"));
    } catch (caught) {
      if (generation === selectionGeneration.current) {
        setError(caught instanceof Error ? caught.message : "无法读取工作空间");
      }
    } finally {
      if (generation === selectionGeneration.current) setLoading(false);
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
    const generation = selectionGeneration.current;
    const workspaceId = workspace.id;
    setManagedThread(undefined);
    setShowArchivedThreads(archived);
    setThreads([]);
    setThreadListLoading(true);
    setError("");
    try {
      const items = await api.listThreads(workspaceId, archived);
      assertWorkspaceThreads(workspaceId, items);
      if (generation !== selectionGeneration.current) return;
      setThreads(items.filter((item) => item.status === (archived ? "ARCHIVED" : "ACTIVE")));
    } catch (caught) {
      if (generation === selectionGeneration.current) {
        setError(caught instanceof Error ? caught.message : "无法读取任务列表");
      }
    } finally {
      if (generation === selectionGeneration.current) setThreadListLoading(false);
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

  const archiveManagedThread = useCallback(async () => {
    if (!managedThread) return;
    if (thread?.id === managedThread.id && run && !TERMINAL.has(run.status)) return;
    const generation = ++threadLifecycleGeneration.current;
    const workspaceId = workspace?.id;
    const targetThreadId = managedThread.id;
    setThreadLifecycleAction("archive");
    setError("");
    try {
      if (!workspaceId || managedThread.workspace_id !== workspaceId) {
        throw new InspectionOwnershipError("待归档任务不属于当前工作空间");
      }
      const archived = await api.archiveThread(targetThreadId);
      if (generation !== threadLifecycleGeneration.current) return;
      assertThreadWorkspace(archived, workspaceId);
      if (archived.id !== targetThreadId || archived.status !== "ARCHIVED") {
        throw new InspectionOwnershipError("归档响应与所选任务不一致");
      }
      const [items, nextEvents] = await Promise.all([
        api.listThreads(workspaceId, true),
        api.listThreadEvents(archived.id),
      ]);
      assertWorkspaceThreads(workspaceId, items);
      if (generation !== threadLifecycleGeneration.current) return;
      setManagedThread(archived);
      if (thread?.id === archived.id) setThread(archived);
      setShowArchivedThreads(true);
      setThreads(items.filter((item) => item.status === "ARCHIVED"));
      setThreadEvents(nextEvents);
    } catch (caught) {
      if (generation === threadLifecycleGeneration.current) {
        setError(caught instanceof Error ? caught.message : "无法归档任务");
      }
    } finally {
      if (generation === threadLifecycleGeneration.current) {
        setThreadLifecycleAction(undefined);
      }
    }
  }, [managedThread, run, thread, workspace?.id]);

  const resumeManagedThread = useCallback(async () => {
    if (!managedThread) return;
    const generation = ++threadLifecycleGeneration.current;
    const workspaceId = workspace?.id;
    const targetThreadId = managedThread.id;
    setThreadLifecycleAction("resume");
    setError("");
    try {
      if (!workspaceId || managedThread.workspace_id !== workspaceId) {
        throw new InspectionOwnershipError("待恢复任务不属于当前工作空间");
      }
      const resumed = await api.resumeThread(targetThreadId);
      if (generation !== threadLifecycleGeneration.current) return;
      assertThreadWorkspace(resumed, workspaceId);
      if (resumed.id !== targetThreadId || resumed.status !== "ACTIVE") {
        throw new InspectionOwnershipError("恢复响应与所选任务不一致");
      }
      const items = await api.listThreads(workspaceId);
      assertWorkspaceThreads(workspaceId, items);
      if (generation !== threadLifecycleGeneration.current) return;
      setShowArchivedThreads(false);
      setThreads(items.filter((item) => item.status === "ACTIVE"));
      setManagedThread(undefined);
      await openThread(resumed);
    } catch (caught) {
      if (generation === threadLifecycleGeneration.current) {
        setError(caught instanceof Error ? caught.message : "无法恢复任务");
      }
    } finally {
      if (generation === threadLifecycleGeneration.current) {
        setThreadLifecycleAction(undefined);
      }
    }
  }, [managedThread, openThread, workspace?.id]);

  const forkManagedThread = useCallback(async (title: string) => {
    if (!managedThread) return;
    const generation = ++threadLifecycleGeneration.current;
    const workspaceId = workspace?.id;
    const targetThreadId = managedThread.id;
    setThreadLifecycleAction("fork");
    setError("");
    try {
      if (!workspaceId || managedThread.workspace_id !== workspaceId) {
        throw new InspectionOwnershipError("分支源任务不属于当前工作空间");
      }
      const turns = await api.listTurns(targetThreadId);
      if (generation !== threadLifecycleGeneration.current) return;
      if (turns.some((item) => item.thread_id !== targetThreadId)) {
        throw new InspectionOwnershipError("分支源对话不属于所选任务");
      }
      const fromTurnId = turns.at(-1)?.id;
      const forked = await api.forkThread(targetThreadId, {
        title,
        ...(fromTurnId ? { from_turn_id: fromTurnId } : {}),
      });
      if (generation !== threadLifecycleGeneration.current) return;
      assertThreadWorkspace(forked, workspaceId);
      if (forked.parent_thread_id !== targetThreadId) {
        throw new InspectionOwnershipError("任务分支响应与所选源任务不一致");
      }
      const items = await api.listThreads(workspaceId);
      assertWorkspaceThreads(workspaceId, items);
      if (generation !== threadLifecycleGeneration.current) return;
      setShowArchivedThreads(false);
      setThreads(items.filter((item) => item.status === "ACTIVE"));
      setManagedThread(undefined);
      await openThread(forked);
    } catch (caught) {
      if (generation === threadLifecycleGeneration.current) {
        setError(caught instanceof Error ? caught.message : "无法建立任务分支");
      }
    } finally {
      if (generation === threadLifecycleGeneration.current) {
        setThreadLifecycleAction(undefined);
      }
    }
  }, [managedThread, openThread, workspace?.id]);

  const pollRun = useCallback(async (
    initial: Run,
    generation: number,
    expectedWorkspaceId: string,
  ) => {
    const expectedRunId = initial.id;
    if (initial.workspace_context?.workspace_id !== expectedWorkspaceId) {
      if (generation === selectionGeneration.current) {
        setError("运行不属于当前工作空间");
      }
      return;
    }
    let current = initial;
    let cursor = 0;
    let consecutiveFailures = 0;
    let stopStream: (() => void) | undefined;
    let acceptStream = true;
    if (generation === selectionGeneration.current) setStreamState("polling");
    void streamRunEvents(current.id, cursor, (event) => {
        if (
          generation !== selectionGeneration.current
          || event.run_id !== expectedRunId
        ) return;
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
        if (acceptStream) {
          stopStream = stop;
          if (generation === selectionGeneration.current) setStreamState("live");
        } else {
          stop();
        }
      })
      .catch(() => {
        // REST cursor reconciliation below remains the compatibility path when a
        // proxy does not support WebSocket upgrade or browser OIDC initialization.
        if (generation === selectionGeneration.current) setStreamState("polling");
      });
    try {
      while (generation === selectionGeneration.current) {
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
          if (generation !== selectionGeneration.current) return;
          if (nextRun.id !== expectedRunId) {
            throw new InspectionOwnershipError("运行状态响应与当前 Run 不一致");
          }
          if (nextRun.workspace_context?.workspace_id !== expectedWorkspaceId) {
            throw new InspectionOwnershipError("运行状态不属于当前工作空间");
          }
          const snapshot = {
            run: nextRun,
            events: nextEvents,
            steps: nextSteps,
            evidence: nextEvidence,
            memories: nextMemories,
            conversation: nextConversation,
            claims: nextClaims,
            artifacts: nextArtifacts,
          };
          assertInspectionOwnership(snapshot);
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
            if (generation === selectionGeneration.current) {
              setStreamState("interrupted");
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
      if (generation === selectionGeneration.current) setStreamState("idle");
    }
  }, []);

  useEffect(() => {
    pollRunRef.current = pollRun;
  }, [pollRun]);

  const submit = useCallback(async () => {
    const input = value.trim();
    if (submitInFlight.current || !input || run && !TERMINAL.has(run.status)) return;
    if (thread?.status === "ARCHIVED") {
      setError("此任务已归档，请先从任务生命周期面板恢复后继续。");
      return;
    }
    if (!workspace) {
      setWorkspaceModal(true);
      return;
    }
    const generation = ++selectionGeneration.current;
    const workspaceId = workspace.id;
    const selectedThreadId = thread?.id;
    const attachmentSnapshot = [...attachments];
    submitInFlight.current = true;
    setSubmitting(true);
    let createdThread: Thread | undefined;
    setError("");
    setValue("");
    setView("assistant");
    setInspectorOpen(true);
    try {
      assertWorkspaceArtifacts(workspaceId, attachmentSnapshot);
      let activeThread = thread;
      if (!activeThread) {
        activeThread = await api.createThread(workspaceId, input.slice(0, 48));
        if (generation !== selectionGeneration.current) return;
        assertThreadWorkspace(activeThread, workspaceId);
        createdThread = activeThread;
      }
      const created = await api.createTurn(
        activeThread.id,
        input,
        attachmentSnapshot.map((artifact) => ({
          type: "artifact",
          artifact_id: artifact.id,
          media_type: artifact.media_type,
          title: artifact.title,
        })),
      );
      if (generation !== selectionGeneration.current) return;
      assertTurnRunOwnership(activeThread.id, created.turn, created.run);
      assertRunWorkspace(created.run, workspaceId);
      if (selectedThreadId && activeThread.id !== selectedThreadId) {
        throw new InspectionOwnershipError("新运行不属于提交时选择的任务");
      }
      if (createdThread) {
        const committedThread = createdThread;
        setThread(committedThread);
        setShowArchivedThreads(false);
        setThreads((previous) => previous.some((item) => item.id === committedThread.id)
          ? previous
          : [committedThread, ...previous]);
      }
      setMessages((previous) => [...previous, { turn: created.turn, run: created.run }]);
      setRun(created.run);
      setEvents([]);
      setSteps([]);
      setEvidence([]);
      setMemories([]);
      setConversationContext([]);
      setClaims([]);
      setArtifacts([]);
      setAttachments([]);
      closeContextPicker();
      void pollRun(created.run, generation, workspaceId);
    } catch (caught) {
      if (generation === selectionGeneration.current) {
        setValue(input);
        setError(caught instanceof Error ? caught.message : "任务创建失败");
      }
    } finally {
      if (generation === selectionGeneration.current && submitInFlight.current) {
        submitInFlight.current = false;
        setSubmitting(false);
      }
    }
  }, [attachments, closeContextPicker, pollRun, run, thread, value, workspace]);

  const attachFiles = useCallback(async (files: File[]) => {
    if (!workspace) {
      setWorkspaceModal(true);
      return;
    }
    const generation = ++uploadGeneration.current;
    const workspaceId = workspace.id;
    setUploading(true);
    setError("");
    try {
      const uploaded: Artifact[] = [];
      for (const file of files) {
        if (generation !== uploadGeneration.current) return;
        const form = new FormData();
        form.set("file", file);
        form.set("title", file.name);
        form.set("kind", "FILE");
        form.set("classification", workspace.classification || "INTERNAL");
        const artifact = await api.uploadArtifact(workspaceId, form);
        if (generation !== uploadGeneration.current) return;
        if (artifact.workspace_id !== workspaceId) {
          throw new InspectionOwnershipError("上传产物不属于当前工作空间");
        }
        uploaded.push(artifact);
      }
      if (generation !== uploadGeneration.current) return;
      setAttachments((previous) => [...previous, ...uploaded]);
    } catch (caught) {
      if (generation === uploadGeneration.current) {
        setError(caught instanceof Error ? caught.message : "附件上传失败");
      }
    } finally {
      if (generation === uploadGeneration.current) setUploading(false);
    }
  }, [workspace]);

  const openContextPicker = useCallback(async () => {
    if (!workspace) {
      setWorkspaceModal(true);
      return;
    }
    const generation = ++contextGeneration.current;
    const workspaceId = workspace.id;
    setContextPickerOpen(true);
    setContextArtifacts([]);
    setContextLoading(true);
    setError("");
    try {
      const items = await api.listWorkspaceArtifacts(workspaceId);
      assertWorkspaceArtifacts(workspaceId, items);
      if (generation !== contextGeneration.current) return;
      setContextArtifacts(items);
    } catch (caught) {
      if (generation === contextGeneration.current) {
        setError(caught instanceof Error ? caught.message : "无法读取工作区上下文");
      }
    } finally {
      if (generation === contextGeneration.current) setContextLoading(false);
    }
  }, [workspace]);

  const cancel = useCallback(async () => {
    if (!run || TERMINAL.has(run.status)) return;
    const generation = ++selectionGeneration.current;
    const targetRunId = run.id;
    const workspaceId = workspace?.id;
    setError("");
    try {
      const cancelled = await api.cancelRun(targetRunId);
      if (generation !== selectionGeneration.current) return;
      if (cancelled.id !== targetRunId) {
        throw new InspectionOwnershipError("停止运行响应与当前 Run 不一致");
      }
      if (!workspaceId) {
        throw new InspectionOwnershipError("当前运行缺少工作空间归属");
      }
      assertRunWorkspace(cancelled, workspaceId);
      setRun(cancelled);
      setMessages((previous) => previous.map((bundle) =>
        bundle.run?.id === cancelled.id ? { ...bundle, run: cancelled } : bundle,
      ));
    } catch (caught) {
      if (generation === selectionGeneration.current) {
        setError(caught instanceof Error ? caught.message : "无法停止运行");
      }
    }
  }, [run, workspace?.id]);

  const replay = useCallback(async (target: Run) => {
    if (!TERMINAL.has(target.status) || run && !TERMINAL.has(run.status)) return;
    const workspaceId = workspace?.id;
    const targetThread = thread;
    if (
      !workspaceId
      || !targetThread
      || target.workspace_context?.workspace_id !== workspaceId
      || !messages.some((bundle) => bundle.turn.id === target.turn_id)
    ) {
      setError("待回放 Run 不属于当前任务");
      return;
    }
    const generation = ++selectionGeneration.current;
    setReplayingRunId(target.id);
    setError("");
    try {
      const replayed = await api.replayRun(target.id);
      if (generation !== selectionGeneration.current) return;
      if (replayed.replay_of_run_id !== target.id || replayed.turn_id !== target.turn_id) {
        throw new InspectionOwnershipError("回放响应与所选 Run 不一致");
      }
      assertRunWorkspace(replayed, workspaceId);
      setRun(replayed);
      setEvents([]);
      setSteps([]);
      setEvidence([]);
      setMemories([]);
      setConversationContext([]);
      setClaims([]);
      setArtifacts([]);
      setFeedbackByRun((previous) => ({ ...previous, [replayed.id]: null }));
      setMessages((previous) => previous.map((bundle) =>
        bundle.turn.id === replayed.turn_id
          ? { ...bundle, run: replayed, artifact: undefined, artifacts: [] }
          : bundle,
      ));
      void pollRun(replayed, generation, workspaceId);
    } catch (caught) {
      if (generation === selectionGeneration.current) {
        setError(caught instanceof Error ? caught.message : "无法回放运行快照");
      }
    } finally {
      if (generation === selectionGeneration.current) setReplayingRunId(undefined);
    }
  }, [messages, pollRun, run, thread, workspace?.id]);

  const recordFeedback = useCallback(async (
    target: Run,
    rating: RunFeedbackRating,
    reason: string,
  ) => {
    const generation = ++feedbackGeneration.current;
    const selection = selectionGeneration.current;
    const workspaceId = workspace?.id;
    if (!workspaceId || target.workspace_context?.workspace_id !== workspaceId) {
      setError("待评价 Run 不属于当前工作空间");
      return false;
    }
    setFeedbackPendingRunId(target.id);
    setError("");
    try {
      const current = feedbackByRun[target.id];
      const recorded = await api.recordRunFeedback(target.id, {
        rating,
        reason,
        ...(current ? { expected_version: current.version } : {}),
      });
      if (
        generation !== feedbackGeneration.current
        || selection !== selectionGeneration.current
      ) return false;
      if (recorded.run_id !== target.id) {
        throw new InspectionOwnershipError("反馈响应与所选 Run 不一致");
      }
      setFeedbackByRun((previous) => ({ ...previous, [target.id]: recorded }));
      return true;
    } catch (caught) {
      if (
        generation === feedbackGeneration.current
        && selection === selectionGeneration.current
      ) {
        setError(caught instanceof Error ? caught.message : "无法保存反馈");
      }
      return false;
    } finally {
      if (
        generation === feedbackGeneration.current
        && selection === selectionGeneration.current
      ) {
        setFeedbackPendingRunId(undefined);
      }
    }
  }, [feedbackByRun, workspace?.id]);

  const newThread = useCallback(async () => {
    const shouldRefresh = Boolean(showArchivedThreads && workspace);
    const workspaceId = workspace?.id;
    resetThread();
    const generation = selectionGeneration.current;
    setView("assistant");
    setManagedThread(undefined);
    if (!shouldRefresh || !workspaceId) return;
    setShowArchivedThreads(false);
    setThreads([]);
    setThreadListLoading(true);
    setError("");
    try {
      const items = await api.listThreads(workspaceId);
      assertWorkspaceThreads(workspaceId, items);
      if (generation !== selectionGeneration.current) return;
      setThreads(items.filter((item) => item.status === "ACTIVE"));
    } catch (caught) {
      if (generation === selectionGeneration.current) {
        setError(caught instanceof Error ? caught.message : "无法读取任务列表");
      }
    } finally {
      if (generation === selectionGeneration.current) setThreadListLoading(false);
    }
  }, [resetThread, showArchivedThreads, workspace]);

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
        onNewThread={() => { void newThread(); setMobileNavOpen(false); }}
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
                  submitting={submitting}
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
                  onCloseContext={closeContextPicker}
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
              <RuntimeInspector open={inspectorOpen} mobileVisible={mobileInspectorOpen} onClose={() => { setInspectorOpen(false); setMobileInspectorOpen(false); }} onReplay={() => { if (run) void replay(run); }} replaying={Boolean(replayingRunId)} run={run} streamState={streamState} events={events} steps={steps} evidence={evidence} memories={memories} conversation={conversationContext} claims={claims} artifacts={artifacts} onOpenCollaboration={() => { setInspectorOpen(false); setMobileInspectorOpen(false); setView("collaboration"); }} />
            </div>
          </>
        ) : view === "collaboration" ? <CollaborationView key={workspace?.id ?? "no-workspace"} workspace={workspace} onOpenRun={(runId, threadId) => void openScopedRun(runId, threadId)} /> : view === "automation" ? <AutomationView key={workspace?.id ?? "no-workspace"} workspace={workspace} onOpenRun={(runId) => void openRunInspection(runId)} /> : view === "actions" ? <ActionsView key={workspace?.id ?? "no-workspace"} workspace={workspace} /> : view === "artifacts" ? <ArtifactsView key={workspace?.id ?? "no-workspace"} workspace={workspace} /> : view === "files" ? <FilesView key={workspace?.id ?? "no-workspace"} workspace={workspace} /> : view === "reports" ? <ReportsView key={workspace?.id ?? "no-workspace"} workspace={workspace} /> : view === "dashboards" ? <DashboardsView key={workspace?.id ?? "no-workspace"} workspace={workspace} /> : view === "sql" ? <SqlView key={workspace?.id ?? "no-workspace"} workspace={workspace} /> : view === "evidence" ? <EvidenceView key={workspace?.id ?? "no-workspace"} workspace={workspace} /> : view === "timeline" ? <TimelineView key={workspace?.id ?? "no-workspace"} workspace={workspace} /> : view === "knowledge" ? <KnowledgeView /> : view === "code" ? <CodeView /> : view === "data" ? <DataView /> : view === "studio" ? <StudioView /> : view === "eval" ? <EvalView /> : <AdminView />}
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

function assertThreadWorkspace(thread: Thread, workspaceId: string) {
  if (thread.workspace_id !== workspaceId) {
    throw new InspectionOwnershipError("任务不属于当前工作空间");
  }
}

function assertRunWorkspace(run: Run, workspaceId: string) {
  if (run.workspace_context?.workspace_id !== workspaceId) {
    throw new InspectionOwnershipError("Run 不属于当前工作空间");
  }
}

function assertTurnRunOwnership(threadId: string, turn: Turn, run: Run) {
  if (turn.thread_id !== threadId) {
    throw new InspectionOwnershipError("新对话不属于当前任务");
  }
  if (run.turn_id !== turn.id) {
    throw new InspectionOwnershipError("新运行不属于新建对话");
  }
}

function assertWorkspaceThreads(workspaceId: string, threads: Thread[]) {
  if (threads.some((item) => item.workspace_id !== workspaceId)) {
    throw new InspectionOwnershipError("任务列表包含其他工作空间的数据");
  }
}

function assertThreadOwnership(
  selected: Thread,
  workspaceId: string,
  turns: Turn[],
  runs: Run[],
) {
  assertThreadWorkspace(selected, workspaceId);
  if (turns.some((item) => item.thread_id !== selected.id)) {
    throw new InspectionOwnershipError("对话记录不属于当前任务");
  }
  const turnIds = new Set(turns.map((item) => item.id));
  if (runs.some((item) => !turnIds.has(item.turn_id))) {
    throw new InspectionOwnershipError("运行记录不属于当前任务");
  }
  for (const item of runs) assertRunWorkspace(item, workspaceId);
}

function assertWorkspaceArtifacts(workspaceId: string, artifacts: Artifact[]) {
  if (artifacts.some((item) => item.workspace_id !== workspaceId)) {
    throw new InspectionOwnershipError("产物列表包含其他工作空间的数据");
  }
}

function assertRunArtifacts(runId: string, workspaceId: string, artifacts: Artifact[]) {
  if (artifacts.some((item) => item.run_id !== runId || item.workspace_id !== workspaceId)) {
    throw new InspectionOwnershipError("运行产物归属与所选 Run 不一致");
  }
}

function assertRunFeedback(runId: string, feedback: RunFeedback | null) {
  if (feedback && feedback.run_id !== runId) {
    throw new InspectionOwnershipError("运行反馈归属与所选 Run 不一致");
  }
}

function assertInspectionOwnership(snapshot: InspectionSnapshot) {
  const runId = snapshot.run.id;
  const workspaceId = snapshot.run.workspace_context?.workspace_id;
  if (!workspaceId) {
    throw new InspectionOwnershipError("Run 缺少已固化的工作空间归属");
  }
  if (snapshot.events.some((item) => item.run_id !== runId)) {
    throw new InspectionOwnershipError("运行事件归属与所选 Run 不一致");
  }
  if (snapshot.steps.some((item) => item.run_id !== runId)) {
    throw new InspectionOwnershipError("运行步骤归属与所选 Run 不一致");
  }
  if (snapshot.evidence.some((item) => item.run_id !== runId)) {
    throw new InspectionOwnershipError("证据归属与所选 Run 不一致");
  }
  if (snapshot.memories.some((item) => item.run_id !== runId)) {
    throw new InspectionOwnershipError("记忆快照归属与所选 Run 不一致");
  }
  if (snapshot.conversation.some((item) => item.run_id !== runId)) {
    throw new InspectionOwnershipError("会话快照归属与所选 Run 不一致");
  }
  if (snapshot.claims.some((item) => item.run_id !== runId)) {
    throw new InspectionOwnershipError("结论归属与所选 Run 不一致");
  }
  assertRunArtifacts(runId, workspaceId, snapshot.artifacts);
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
