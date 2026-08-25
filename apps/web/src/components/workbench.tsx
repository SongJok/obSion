"use client";

import { Menu, PanelRightOpen, Plus, ShieldCheck, X } from "lucide-react";
import { FormEvent, useCallback, useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import type {
  Artifact,
  Claim,
  Evidence,
  MessageBundle,
  Run,
  RunEvent,
  RunStep,
  Thread,
  ViewName,
  Workspace,
} from "@/lib/types";
import { AdminView } from "./admin-view";
import { ActionsView } from "./actions-view";
import { AutomationView } from "./automation-view";
import { ArtifactsView } from "./artifacts-view";
import { Composer } from "./composer";
import { Conversation } from "./conversation";
import { DataView } from "./data-view";
import { EmptyState } from "./empty-state";
import { KnowledgeView } from "./knowledge-view";
import { RuntimeInspector } from "./runtime-inspector";
import { Sidebar } from "./sidebar";

const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

export function Workbench() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [workspace, setWorkspace] = useState<Workspace>();
  const [threads, setThreads] = useState<Thread[]>([]);
  const [thread, setThread] = useState<Thread>();
  const [messages, setMessages] = useState<MessageBundle[]>([]);
  const [run, setRun] = useState<Run>();
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [steps, setSteps] = useState<RunStep[]>([]);
  const [evidence, setEvidence] = useState<Evidence[]>([]);
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
  const [replaying, setReplaying] = useState(false);
  const requestGeneration = useRef(0);

  const clearInspection = useCallback(() => {
    setRun(undefined);
    setEvents([]);
    setSteps([]);
    setEvidence([]);
    setClaims([]);
    setArtifacts([]);
  }, []);

  const loadInspection = useCallback(async (target: Run) => {
    const [nextEvents, nextSteps, nextEvidence, nextClaims, nextArtifacts] = await Promise.all([
      api.listEvents(target.id),
      api.listSteps(target.id),
      api.listEvidence(target.id),
      api.listClaims(target.id),
      api.listArtifacts(target.id),
    ]);
    setRun(target);
    setEvents(nextEvents);
    setSteps(nextSteps);
    setEvidence(nextEvidence);
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
      const artifactsByRun = await Promise.all(
        runs.map(async (item) => api.listArtifacts(item.id)),
      );
      if (generation !== requestGeneration.current) return;
      const runByTurn = new Map(runs.map((item, index) => [item.turn_id, {
        item,
        artifacts: artifactsByRun[index],
        artifact: primaryArtifact(artifactsByRun[index]),
      }]));
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
    ++requestGeneration.current;
    setWorkspace(selected);
    setThread(undefined);
    setMessages([]);
    setAttachments([]);
    clearInspection();
    setLoading(true);
    setError("");
    try {
      setThreads(await api.listThreads(selected.id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取工作空间");
    } finally {
      setLoading(false);
    }
  }, [clearInspection]);

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

  const pollRun = useCallback(async (initial: Run, generation: number) => {
    let current = initial;
    let cursor = 0;
    let consecutiveFailures = 0;
    while (generation === requestGeneration.current) {
      try {
        const [nextRun, nextEvents, nextSteps, nextEvidence, nextClaims, nextArtifacts] = await Promise.all([
          api.getRun(current.id),
          api.listEvents(current.id, cursor),
          api.listSteps(current.id),
          api.listEvidence(current.id),
          api.listClaims(current.id),
          api.listArtifacts(current.id),
        ]);
        if (generation !== requestGeneration.current) return;
        consecutiveFailures = 0;
        current = nextRun;
        if (nextEvents.length) {
          cursor = nextEvents.at(-1)?.sequence ?? cursor;
          setEvents((previous) => [...previous, ...nextEvents]);
        }
        setRun(nextRun);
        setSteps(nextSteps);
        setEvidence(nextEvidence);
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
            setError(caught instanceof Error ? `运行仍在后台继续，但状态同步暂时中断：${caught.message}` : "运行状态同步暂时中断");
          }
          return;
        }
        await new Promise((resolve) => window.setTimeout(resolve, 500 * consecutiveFailures));
      }
    }
  }, []);

  const submit = useCallback(async () => {
    const input = value.trim();
    if (!input || run && !TERMINAL.has(run.status)) return;
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
      setClaims([]);
      setAttachments([]);
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

  const replay = useCallback(async () => {
    if (!run || !TERMINAL.has(run.status)) return;
    setReplaying(true);
    setError("");
    try {
      const replayed = await api.replayRun(run.id);
      const generation = ++requestGeneration.current;
      setRun(replayed);
      setEvents([]);
      setSteps([]);
      setEvidence([]);
      setClaims([]);
      setArtifacts([]);
      setMessages((previous) => previous.map((bundle) =>
        bundle.turn.id === replayed.turn_id
          ? { ...bundle, run: replayed, artifact: undefined, artifacts: [] }
          : bundle,
      ));
      void pollRun(replayed, generation);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法回放运行快照");
    } finally {
      setReplaying(false);
    }
  }, [pollRun, run]);

  const newThread = () => {
    ++requestGeneration.current;
    setThread(undefined);
    setMessages([]);
    clearInspection();
    setView("assistant");
    setValue("");
    setAttachments([]);
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
        onNewThread={() => { newThread(); setMobileNavOpen(false); }}
        onNewWorkspace={() => { setWorkspaceModal(true); setMobileNavOpen(false); }}
        view={view}
        onView={(nextView) => { setView(nextView); setMobileNavOpen(false); }}
      />

      <section className="workspace-shell">
        {view !== "assistant" && <button className="icon-button feature-mobile-menu" onClick={() => setMobileNavOpen(true)} aria-label="打开导航"><Menu size={19} /></button>}
        {view === "assistant" ? (
          <>
            <header className="workspace-header">
              <button className="icon-button mobile-menu" onClick={() => setMobileNavOpen(true)} aria-label="打开导航"><Menu size={19} /></button>
              <div><strong>{thread?.title ?? "智能工作台"}</strong><span>{workspace?.name ?? "选择工作空间"}</span></div>
              <div className="header-actions">
                <span className="governance-pill"><ShieldCheck size={14} /> 策略已启用</span>
                {!inspectorOpen && <button className="icon-button" onClick={() => setInspectorOpen(true)} title="打开运行详情"><PanelRightOpen size={19} /></button>}
                <button className="icon-button mobile-inspector-trigger" onClick={() => { setInspectorOpen(true); setMobileInspectorOpen(true); }} title="打开运行详情" aria-label="打开运行详情"><PanelRightOpen size={19} /></button>
              </div>
            </header>

            <div className="assistant-layout">
              <main className="chat-panel">
                {error && <div className="global-error"><span>{error}</span><button onClick={() => setError("")}><X size={15} /></button></div>}
                <div className="chat-scroll">
                  {!messages.length && !loading ? <EmptyState onSuggestion={setValue} /> : <Conversation messages={messages} />}
                  {loading && <div className="loading-state"><i /><span>正在载入可回放记录…</span></div>}
                </div>
                <Composer
                  value={value}
                  onChange={setValue}
                  onSubmit={() => void submit()}
                  onCancel={() => void cancel()}
                  running={running}
                  disabled={loading}
                  attachments={attachments}
                  uploading={uploading}
                  onAttach={(files) => void attachFiles(files)}
                  onRemoveAttachment={(artifactId) =>
                    setAttachments((items) => items.filter((item) => item.id !== artifactId))
                  }
                />
              </main>
              <RuntimeInspector open={inspectorOpen} mobileVisible={mobileInspectorOpen} onClose={() => { setInspectorOpen(false); setMobileInspectorOpen(false); }} onReplay={() => void replay()} replaying={replaying} run={run} events={events} steps={steps} evidence={evidence} claims={claims} artifacts={artifacts} />
            </div>
          </>
        ) : view === "automation" ? <AutomationView key={workspace?.id ?? "no-workspace"} workspace={workspace} /> : view === "actions" ? <ActionsView key={workspace?.id ?? "no-workspace"} workspace={workspace} /> : view === "artifacts" ? <ArtifactsView key={workspace?.id ?? "no-workspace"} workspace={workspace} /> : view === "knowledge" ? <KnowledgeView /> : view === "data" ? <DataView /> : <AdminView />}
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
