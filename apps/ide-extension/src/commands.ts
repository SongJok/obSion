import { IdeError, TOKEN_SECRET_KEY, loadSettings } from "./config.js";
import type { IdeHost } from "./host.js";
import { containsCredential, renderApproval, renderAsk } from "./render.js";
import { ExperienceRuntime, type AskResult } from "./runtime.js";

export interface RuntimeFactory {
  (settings: ReturnType<typeof loadSettings>): Promise<ExperienceRuntime>;
}

export class IdeSession {
  lastRunId: string | undefined;
  lastWorkspaceName = "IDE";

  constructor(
    private readonly host: IdeHost,
    private readonly connect: RuntimeFactory = (settings) => ExperienceRuntime.connect(settings),
  ) {}

  async ask(question?: string): Promise<AskResult> {
    const text =
      question ??
      (await this.host.showInput({
        prompt: "Ask Obsion",
        placeholder: "What should the Harness investigate?",
      }));
    if (!text?.trim()) throw new IdeError("Ask requires a non-empty question");
    const runtime = await this.openRuntime();
    try {
      const result = await runtime.ask(text, { workspaceName: this.lastWorkspaceName });
      this.lastRunId = result.run.id;
      const rendered = renderAsk(result);
      if (containsCredential(rendered, runtime.settings.token)) {
        throw new IdeError("Refusing to print a credential in the IDE output");
      }
      this.host.appendOutput(rendered);
      return result;
    } finally {
      runtime.close();
    }
  }

  async setToken(): Promise<void> {
    const value = await this.host.showInput({
      prompt: "Obsion bearer token",
      password: true,
    });
    if (!value?.trim()) return;
    await this.host.storeSecret(TOKEN_SECRET_KEY, value.trim());
    this.host.appendOutput("Token stored in Secret Storage.");
  }

  async clearToken(): Promise<void> {
    await this.host.deleteSecret(TOKEN_SECRET_KEY);
    this.host.appendOutput("Token removed from Secret Storage.");
  }

  async cancelRun(runId?: string): Promise<void> {
    const id = runId ?? this.lastRunId;
    if (!id) throw new IdeError("No Run is selected");
    const runtime = await this.openRuntime();
    try {
      const run = await runtime.cancelRun(id);
      this.host.appendOutput(`Cancelled ${run.id} (${run.status})`);
    } finally {
      runtime.close();
    }
  }

  async replayRun(runId?: string): Promise<void> {
    const id = runId ?? this.lastRunId;
    if (!id) throw new IdeError("No Run is selected");
    const runtime = await this.openRuntime();
    try {
      const run = await runtime.replayRun(id);
      this.lastRunId = run.id;
      this.host.appendOutput(`Replay ${run.id} (${run.status})`);
    } finally {
      runtime.close();
    }
  }

  async decide(approve: boolean): Promise<void> {
    const runtime = await this.openRuntime();
    try {
      const pending = await runtime.listApprovals("PENDING");
      const first = pending[0];
      if (!first) throw new IdeError("No waiting approvals");
      const reason =
        (await this.host.showInput({
          prompt: approve ? "Approval reason" : "Rejection reason",
        })) ?? (approve ? "Approved from IDE" : "Rejected from IDE");
      const decided = await runtime.decideApproval(String(first.id), { approve, reason });
      this.host.appendOutput(renderApproval(decided));
    } finally {
      runtime.close();
    }
  }

  private async openRuntime(): Promise<ExperienceRuntime> {
    const secret = await this.host.getSecret(TOKEN_SECRET_KEY);
    const settings = loadSettings(this.host.configuration(), this.host.environment(), secret);
    return this.connect(settings);
  }
}
