import * as vscode from "vscode";

import { IdeError } from "./config.js";
import { IdeSession } from "./commands.js";
import type { IdeHost } from "./host.js";

class VsCodeHost implements IdeHost {
  constructor(
    private readonly context: vscode.ExtensionContext,
    private readonly output: vscode.OutputChannel,
  ) {}

  configuration(): Record<string, unknown> {
    const section = vscode.workspace.getConfiguration("obsion");
    return {
      baseUrl: section.get<string>("baseUrl") ?? "http://127.0.0.1:8080",
      protocol: section.get<string>("protocol") ?? "app-server",
    };
  }

  environment(): Record<string, string | undefined> {
    return { ...process.env };
  }

  async getSecret(key: string): Promise<string | undefined> {
    return this.context.secrets.get(key);
  }

  async storeSecret(key: string, value: string): Promise<void> {
    await this.context.secrets.store(key, value);
  }

  async deleteSecret(key: string): Promise<void> {
    await this.context.secrets.delete(key);
  }

  async showInput(options: {
    prompt: string;
    placeholder?: string;
    password?: boolean;
  }): Promise<string | undefined> {
    const box: vscode.InputBoxOptions = {
      prompt: options.prompt,
      password: options.password ?? false,
      ignoreFocusOut: true,
    };
    if (options.placeholder) box.placeHolder = options.placeholder;
    return vscode.window.showInputBox(box);
  }

  appendOutput(text: string): void {
    this.output.appendLine(text);
    this.output.show(true);
  }

  showError(message: string): void {
    void vscode.window.showErrorMessage(message);
  }
}

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel("Obsion");
  const session = new IdeSession(new VsCodeHost(context, output));
  const wrap = (work: () => Promise<unknown>) => async () => {
    try {
      await work();
    } catch (error) {
      const message = error instanceof IdeError ? error.message : "Obsion request failed";
      void vscode.window.showErrorMessage(message);
      output.appendLine(message);
    }
  };
  context.subscriptions.push(
    output,
    vscode.commands.registerCommand("obsion.ask", wrap(() => session.ask())),
    vscode.commands.registerCommand("obsion.setToken", wrap(() => session.setToken())),
    vscode.commands.registerCommand("obsion.clearToken", wrap(() => session.clearToken())),
    vscode.commands.registerCommand("obsion.cancelRun", wrap(() => session.cancelRun())),
    vscode.commands.registerCommand("obsion.replayRun", wrap(() => session.replayRun())),
    vscode.commands.registerCommand("obsion.approve", wrap(() => session.decide(true))),
    vscode.commands.registerCommand("obsion.reject", wrap(() => session.decide(false))),
  );
}

export function deactivate(): void {
  return;
}
