import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";

import { DesktopError } from "./config.js";
import type { DesktopHost } from "./host.js";
import { FileSecretStore } from "./secrets.js";
import { DesktopSession } from "./session.js";
import { DESKTOP_SHELL_HTML } from "./shell.js";

export interface WindowServerOptions {
  host: DesktopHost;
  session?: DesktopSession;
  listenHost?: string;
  port?: number;
}

export class WindowHost implements DesktopHost {
  lastOutput = "";

  constructor(
    private readonly settings: Record<string, unknown>,
    private readonly env: Record<string, string | undefined>,
    private readonly secrets: FileSecretStore,
  ) {}

  configuration(): Record<string, unknown> {
    return this.settings;
  }

  environment(): Record<string, string | undefined> {
    return this.env;
  }

  getSecret(key: string): Promise<string | undefined> {
    return this.secrets.get(key);
  }

  storeSecret(key: string, value: string): Promise<void> {
    return this.secrets.store(key, value);
  }

  deleteSecret(key: string): Promise<void> {
    return this.secrets.delete(key);
  }

  async showInput(): Promise<string | undefined> {
    return undefined;
  }

  appendOutput(text: string): void {
    this.lastOutput = text;
  }

  showError(message: string): void {
    this.lastOutput = message;
  }
}

export class DesktopWindowServer {
  readonly session: DesktopSession;
  readonly windowHost: WindowHost | undefined;
  private server: Server | undefined;
  url = "";

  constructor(private readonly options: WindowServerOptions) {
    this.session = options.session ?? new DesktopSession(options.host);
    this.windowHost = options.host instanceof WindowHost ? options.host : undefined;
  }

  async listen(): Promise<string> {
    const listenHost = this.options.listenHost ?? "127.0.0.1";
    if (listenHost !== "127.0.0.1") {
      throw new DesktopError("Desktop window server may only bind 127.0.0.1");
    }
    this.server = createServer((request, response) => {
      void this.handle(request, response);
    });
    await new Promise<void>((resolve, reject) => {
      this.server?.once("error", reject);
      this.server?.listen(this.options.port ?? 0, listenHost, () => resolve());
    });
    const address = this.server.address();
    if (!address || typeof address === "string") {
      throw new DesktopError("Desktop window server failed to bind");
    }
    this.url = `http://127.0.0.1:${address.port}`;
    return this.url;
  }

  async close(): Promise<void> {
    const server = this.server;
    this.server = undefined;
    if (!server) return;
    await new Promise<void>((resolve, reject) => {
      server.close((error) => (error ? reject(error) : resolve()));
    });
  }

  private async handle(request: IncomingMessage, response: ServerResponse): Promise<void> {
    try {
      if (!isLoopbackHost(request.headers.host)) {
        sendJson(response, 403, { error: "Desktop UI is loopback-only" });
        return;
      }
      const url = new URL(request.url ?? "/", "http://127.0.0.1");
      if (request.method === "GET" && url.pathname === "/") {
        response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
        response.end(DESKTOP_SHELL_HTML);
        return;
      }
      if (request.method === "GET" && url.pathname === "/api/status") {
        sendJson(response, 200, await this.session.status());
        return;
      }
      const body = await readJson(request);
      if (request.method === "POST" && url.pathname === "/api/token") {
        await this.session.setToken(String(body.token ?? ""));
        sendJson(response, 200, await this.session.status());
        return;
      }
      if (request.method === "DELETE" && url.pathname === "/api/token") {
        await this.session.clearToken();
        sendJson(response, 200, await this.session.status());
        return;
      }
      if (request.method === "POST" && url.pathname === "/api/ask") {
        const result = await this.session.ask(String(body.text ?? ""));
        sendJson(response, 200, {
          rendered: this.lastOutput(),
          runId: result.run.id,
          claims: result.claims.length,
          evidence: result.evidence.length,
        });
        return;
      }
      if (request.method === "POST" && url.pathname === "/api/cancel") {
        await this.session.cancelRun();
        sendJson(response, 200, { rendered: this.lastOutput() });
        return;
      }
      if (request.method === "POST" && url.pathname === "/api/replay") {
        await this.session.replayRun();
        sendJson(response, 200, { rendered: this.lastOutput() });
        return;
      }
      if (request.method === "POST" && url.pathname === "/api/approve") {
        await this.session.decide(true, String(body.reason ?? "Approved from Desktop"));
        sendJson(response, 200, { rendered: this.lastOutput() });
        return;
      }
      if (request.method === "POST" && url.pathname === "/api/reject") {
        await this.session.decide(false, String(body.reason ?? "Rejected from Desktop"));
        sendJson(response, 200, { rendered: this.lastOutput() });
        return;
      }
      sendJson(response, 404, { error: "Not found" });
    } catch (error) {
      const message = error instanceof DesktopError ? error.message : "Desktop request failed";
      sendJson(response, 400, { error: message });
    }
  }

  private lastOutput(): string {
    return this.windowHost?.lastOutput ?? "";
  }
}

function isLoopbackHost(host: string | undefined): boolean {
  if (!host) return false;
  const hostname = host.split(":")[0]?.toLowerCase();
  return hostname === "127.0.0.1" || hostname === "localhost";
}

function sendJson(response: ServerResponse, status: number, body: Record<string, unknown>): void {
  response.writeHead(status, { "Content-Type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(body));
}

async function readJson(request: IncomingMessage): Promise<Record<string, unknown>> {
  if (request.method === "GET" || request.method === "HEAD") return {};
  const chunks: Buffer[] = [];
  for await (const chunk of request) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  if (!chunks.length) return {};
  const raw = Buffer.concat(chunks).toString("utf8");
  if (!raw.trim()) return {};
  const parsed: unknown = JSON.parse(raw);
  return parsed && typeof parsed === "object" && !Array.isArray(parsed)
    ? (parsed as Record<string, unknown>)
    : {};
}
