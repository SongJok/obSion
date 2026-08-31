#!/usr/bin/env node
import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { DesktopError, loadSettings, rejectCredentialKeys } from "./config.js";
import { FileSecretStore, defaultSecretPath } from "./secrets.js";
import { DesktopSession } from "./session.js";
import { DesktopWindowServer, WindowHost } from "./window-server.js";

interface ParsedArgs {
  command: "ask" | "serve";
  question?: string;
  url?: string;
  protocol?: string;
  config?: string;
  secret?: string;
  port?: number;
  json?: boolean;
}

export function parseArgs(argv: string[]): ParsedArgs {
  const args = [...argv];
  const parsed: ParsedArgs = { command: "serve" };
  while (args.length) {
    const item = args.shift();
    if (item === "ask") {
      parsed.command = "ask";
      const question = args.shift();
      if (question !== undefined) parsed.question = question;
      continue;
    }
    if (item === "serve") {
      parsed.command = "serve";
      continue;
    }
    if (item === "--url") {
      const url = args.shift();
      if (url !== undefined) parsed.url = url;
      continue;
    }
    if (item === "--protocol") {
      const protocol = args.shift();
      if (protocol !== undefined) parsed.protocol = protocol;
      continue;
    }
    if (item === "--config") {
      const config = args.shift();
      if (config !== undefined) parsed.config = config;
      continue;
    }
    if (item === "--secret-file") {
      const secret = args.shift();
      if (secret !== undefined) parsed.secret = secret;
      continue;
    }
    if (item === "--port") {
      const port = Number(args.shift());
      if (Number.isFinite(port)) parsed.port = port;
      continue;
    }
    if (item === "--json") {
      parsed.json = true;
      continue;
    }
    throw new DesktopError(`Unknown argument: ${item}`);
  }
  if (parsed.command === "ask" && !parsed.question?.trim()) {
    throw new DesktopError("ask requires a question");
  }
  return parsed;
}

export async function main(
  argv: string[] = process.argv.slice(2),
  env: Record<string, string | undefined> = process.env,
): Promise<number> {
  try {
    const args = parseArgs(argv);
    const fileConfig = readConfigFile(args.config, env);
    const host = new WindowHost(
      {
        ...(args.url ? { baseUrl: args.url } : {}),
        ...(args.protocol ? { protocol: args.protocol } : {}),
        ...fileConfig,
      },
      env,
      new FileSecretStore(args.secret ?? defaultSecretPath(env.HOME ?? homedir())),
    );
    const session = new DesktopSession(host);
    if (args.command === "ask") {
      const result = await session.ask(args.question ?? "");
      const output = args.json
        ? JSON.stringify({ answer: result.answer, runId: result.run.id })
        : host.lastOutput;
      process.stdout.write(`${output}\n`);
      return 0;
    }
    const server = new DesktopWindowServer({
      host,
      session,
      ...(args.port !== undefined ? { port: args.port } : {}),
    });
    const url = await server.listen();
    process.stdout.write(`Obsion Desktop UI on ${url}\n`);
    const electronExit = spawnElectron(url);
    if (electronExit) {
      return await electronExit.finally(() => server.close());
    }
    process.stdout.write(
      "Electron is not installed; serving the loopback desktop shell until interrupt.\n",
    );
    await waitForInterrupt();
    await server.close();
    return 0;
  } catch (error) {
    const message = error instanceof DesktopError ? error.message : "obsion-desktop failed";
    process.stderr.write(`${message}\n`);
    return 1;
  }
}

function readConfigFile(
  configPath: string | undefined,
  env: Record<string, string | undefined>,
): Record<string, unknown> {
  const path =
    configPath ??
    env.OBSION_DESKTOP_CONFIG ??
    join(env.HOME ?? homedir(), ".config/obsion/desktop.json");
  if (!existsSync(path)) return {};
  const document: unknown = JSON.parse(readFileSync(path, "utf8"));
  if (!document || typeof document !== "object" || Array.isArray(document)) {
    throw new DesktopError("Desktop config must be a JSON object");
  }
  const raw = document as Record<string, unknown>;
  rejectCredentialKeys(raw);
  loadSettings(raw, env);
  return raw;
}

function spawnElectron(url: string): Promise<number> | undefined {
  const bin = electronBinary();
  if (!bin) return undefined;
  const main = join(dirname(fileURLToPath(import.meta.url)), "electron-main.js");
  return new Promise((resolve) => {
    const child = spawn(bin, [main, url], { stdio: "inherit" });
    child.on("exit", (code) => resolve(code ?? 1));
  });
}

function waitForInterrupt(): Promise<void> {
  return new Promise((resolve) => {
    const stop = () => resolve();
    process.once("SIGINT", stop);
    process.once("SIGTERM", stop);
  });
}

function electronBinary(): string | undefined {
  const here = dirname(fileURLToPath(import.meta.url));
  const candidates = [
    join(here, "..", "node_modules", ".bin", "electron"),
    join(here, "..", "..", "..", "node_modules", ".bin", "electron"),
  ];
  return candidates.find((path) => existsSync(path));
}

const entry = process.argv[1];
if (entry && import.meta.url === pathToFileURL(entry).href) {
  void main().then((code) => process.exit(code));
}
