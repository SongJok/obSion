import {
  ObsionAppServerClient,
  ObsionClient,
  appServerUrlFromApiUrl,
  newClientRequestId,
  type AppServerWebSocketFactory,
  type Artifact,
  type Run,
  type RunEvent,
  type Thread,
  type Turn,
  type Workspace,
} from "@obsion/sdk";

import { DesktopError, type DesktopSettings } from "./config.js";

const TERMINAL = new Set(["COMPLETED", "FAILED", "CANCELLED"]);

export interface AskResult {
  workspace: Workspace;
  thread: Thread;
  turn: Turn;
  run: Run;
  events: RunEvent[];
  steps: Array<Record<string, unknown>>;
  evidence: Array<Record<string, unknown>>;
  claims: Array<Record<string, unknown>>;
  artifacts: Artifact[];
  answer: string;
}

export class ExperienceRuntime {
  constructor(
    readonly settings: DesktopSettings,
    private readonly rest: ObsionClient,
    private readonly appServer: ObsionAppServerClient | undefined,
    private readonly requestId: (prefix: string) => string = (prefix) =>
      newClientRequestId(prefix),
    private readonly sleep: (ms: number) => Promise<void> = (ms) =>
      new Promise((resolve) => setTimeout(resolve, ms)),
  ) {
    if (settings.protocol === "app-server" && appServer === undefined) {
      throw new DesktopError("App Server protocol requires an App Server client");
    }
  }

  static async connect(
    settings: DesktopSettings,
    options: {
      rest?: ObsionClient;
      appServer?: ObsionAppServerClient;
      webSocketFactory?: AppServerWebSocketFactory;
    } = {},
  ): Promise<ExperienceRuntime> {
    const token = settings.token;
    const rest =
      options.rest ?? new ObsionClient(settings.baseUrl, token ? () => token : undefined);
    let appServer = options.appServer;
    if (settings.protocol === "app-server" && appServer === undefined) {
      appServer = new ObsionAppServerClient(appServerUrlFromApiUrl(settings.baseUrl), {
        ...(token ? { token } : {}),
        clientName: "obsion-desktop",
        clientVersion: "0.1.0",
        ...(options.webSocketFactory
          ? { webSocketFactory: options.webSocketFactory }
          : {}),
      });
      await appServer.connect();
    }
    return new ExperienceRuntime(settings, rest, appServer);
  }

  close(): void {
    this.appServer?.close();
  }

  async listWorkspaces(includeArchived = false): Promise<Workspace[]> {
    if (this.appServer) return this.appServer.listWorkspaces(includeArchived);
    return this.rest.listWorkspaces(includeArchived);
  }

  async createWorkspace(name: string, description = ""): Promise<Workspace> {
    return this.rest.createWorkspace({ name, description });
  }

  async listThreads(workspaceId: string, includeArchived = false): Promise<Thread[]> {
    if (this.appServer) return this.appServer.listThreads(workspaceId, includeArchived);
    return this.rest.listThreads(workspaceId, includeArchived);
  }

  async createThread(workspaceId: string, title: string): Promise<Thread> {
    if (this.appServer) {
      return this.appServer.createThread(workspaceId, title, this.requestId("thread"));
    }
    return this.rest.createThread(workspaceId, title);
  }

  async createTurn(threadId: string, text: string): Promise<{ turn: Turn; run: Run }> {
    if (this.appServer) {
      return this.appServer.createTurn(threadId, text, this.requestId("turn"));
    }
    return this.rest.createTurn(threadId, text);
  }

  async getRun(runId: string): Promise<Run> {
    if (this.appServer) return this.appServer.getRun(runId);
    return this.rest.getRun(runId);
  }

  async cancelRun(runId: string): Promise<Run> {
    if (this.appServer) return this.appServer.cancelRun(runId, this.requestId("cancel"));
    return this.rest.cancelRun(runId);
  }

  async replayRun(runId: string): Promise<Run> {
    if (this.appServer) return this.appServer.replayRun(runId, this.requestId("replay"));
    return this.rest.replayRun(runId);
  }

  listRunEvents(runId: string, after = 0): Promise<RunEvent[]> {
    return this.rest.listEvents(runId, after);
  }

  listRunSteps(runId: string): Promise<Array<Record<string, unknown>>> {
    return this.rest.listRunSteps(runId);
  }

  listRunEvidence(runId: string): Promise<Array<Record<string, unknown>>> {
    return this.rest.listRunEvidence(runId);
  }

  listRunClaims(runId: string): Promise<Array<Record<string, unknown>>> {
    return this.rest.listRunClaims(runId);
  }

  listRunArtifacts(runId: string): Promise<Artifact[]> {
    return this.rest.listRunArtifacts(runId);
  }

  async listApprovals(status?: string): Promise<Array<Record<string, unknown>>> {
    if (this.appServer) return this.appServer.listApprovals(status);
    return this.rest.listApprovals(status);
  }

  async decideApproval(
    approvalId: string,
    input: { approve: boolean; reason: string },
  ): Promise<Record<string, unknown>> {
    if (this.appServer) {
      return this.appServer.decideApproval(approvalId, this.requestId("approval"), input);
    }
    return this.rest.decideApproval(approvalId, input);
  }

  async ask(
    text: string,
    options: { workspaceName?: string; threadId?: string } = {},
  ): Promise<AskResult> {
    const question = text.trim();
    if (!question) throw new DesktopError("Ask requires a non-empty question");
    const workspace = await this.resolveWorkspace(options.workspaceName ?? "Desktop");
    const thread = await this.resolveThread(
      workspace.id,
      options.threadId,
      threadTitle(question),
    );
    const created = await this.createTurn(thread.id, question);
    const runId = created.run.id;
    const { run, events } = await this.waitForRun(runId);
    const [steps, evidence, claims, artifacts] = await Promise.all([
      this.listRunSteps(runId),
      this.listRunEvidence(runId),
      this.listRunClaims(runId),
      this.listRunArtifacts(runId),
    ]);
    return {
      workspace,
      thread,
      turn: created.turn,
      run,
      events,
      steps,
      evidence,
      claims,
      artifacts,
      answer: answerFrom(events, artifacts),
    };
  }

  async waitForRun(runId: string): Promise<{ run: Run; events: RunEvent[] }> {
    const deadline = Date.now() + this.settings.waitTimeoutMs;
    let after = 0;
    const events: RunEvent[] = [];
    const seen = new Set<string>();
    while (Date.now() < deadline) {
      const run = await this.getRun(runId);
      const batch = await this.listRunEvents(runId, after);
      for (const event of batch) {
        if (seen.has(event.id)) continue;
        seen.add(event.id);
        events.push(event);
        if (typeof event.run_sequence === "number" && event.run_sequence > after) {
          after = event.run_sequence;
        }
      }
      if (TERMINAL.has(run.status)) return { run, events };
      await this.sleep(this.settings.pollIntervalMs);
    }
    throw new DesktopError(`Timed out waiting for run ${runId}`);
  }

  private async resolveWorkspace(name: string): Promise<Workspace> {
    const workspaces = await this.listWorkspaces();
    const existing = workspaces.find((item) => item.name === name);
    if (existing) return existing;
    return this.createWorkspace(name, "Obsion Experience Desktop workspace");
  }

  private async resolveThread(
    workspaceId: string,
    threadId: string | undefined,
    title: string,
  ): Promise<Thread> {
    if (threadId) {
      const threads = await this.listThreads(workspaceId, true);
      const match = threads.find((item) => item.id === threadId);
      if (!match) throw new DesktopError(`Thread ${threadId} was not found`);
      return match;
    }
    return this.createThread(workspaceId, title);
  }
}

function threadTitle(question: string): string {
  const first = question.split(/\r?\n/, 1)[0]?.trim() ?? "";
  return first.slice(0, 80) || "Desktop question";
}

export function answerFrom(events: RunEvent[], artifacts: Artifact[]): string {
  const chunks: string[] = [];
  for (const event of events) {
    if (event.name !== "answer.delta") continue;
    const delta = event.payload.delta;
    if (typeof delta === "string") chunks.push(delta);
  }
  if (chunks.length) return chunks.join("");
  for (const artifact of artifacts) {
    const content = artifact.inline_content;
    if (!content) continue;
    if (typeof content.markdown === "string") return content.markdown;
    if (typeof content.text === "string") return content.text;
  }
  return "";
}
