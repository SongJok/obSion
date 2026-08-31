import assert from "node:assert/strict";
import test from "node:test";
import { request as httpRequest } from "node:http";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { ObsionClient } from "@obsion/sdk";

import { loadSettings } from "../dist/config.js";
import { FileSecretStore } from "../dist/secrets.js";
import { ExperienceRuntime } from "../dist/runtime.js";
import { DesktopSession } from "../dist/session.js";
import { DesktopWindowServer, WindowHost } from "../dist/window-server.js";
import { DESKTOP_SHELL_HTML } from "../dist/shell.js";

function restWorkspace() {
  return {
    id: "workspace-1",
    name: "Desktop",
    description: "",
    owner_id: "user-1",
    classification: "INTERNAL",
    visibility: "PRIVATE",
    created_at: "2026-08-29T00:00:00Z",
    updated_at: "2026-08-29T00:00:00Z",
    archived_at: null,
  };
}

function installRestAsk() {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    if (String(url).startsWith("http://127.0.0.1")) {
      return originalFetch(url, init);
    }
    const path = new URL(url, "http://obsion.example").pathname;
    let body = {};
    if (path === "/api/v1/workspaces" && (init?.method ?? "GET") === "GET") {
      body = [restWorkspace()];
    } else if (path === "/api/v1/threads") {
      body = {
        id: "thread-1",
        workspace_id: "workspace-1",
        title: "你好",
        status: "ACTIVE",
        created_by: "user-1",
        parent_thread_id: null,
        forked_from_turn_id: null,
        created_at: "2026-08-29T00:00:00Z",
        updated_at: "2026-08-29T00:00:00Z",
        archived_at: null,
      };
    } else if (path.endsWith("/turns")) {
      body = {
        turn: {
          id: "turn-1",
          thread_id: "thread-1",
          ordinal: 1,
          created_by: "user-1",
          input_text: "你好",
          context_refs: [],
          attachment_refs: [],
          created_at: "2026-08-29T00:00:00Z",
        },
        run: {
          id: "run-1",
          turn_id: "turn-1",
          status: "COMPLETED",
          agent_version_id: null,
          model_profile_id: null,
          intent: {},
          plan: {},
          max_steps: 30,
          timeout_seconds: 120,
          max_input_tokens: 1,
          max_output_tokens: 1,
          max_cost_amount: "0",
          step_count: 6,
          input_tokens: 0,
          output_tokens: 0,
          cost_amount: "0",
          started_at: null,
          completed_at: null,
          cancellation_requested_at: null,
          error_code: null,
          error_message: null,
          replay_of_run_id: null,
          created_at: "2026-08-29T00:00:00Z",
          updated_at: "2026-08-29T00:00:00Z",
        },
      };
    } else if (path.endsWith("/events")) {
      body = [
        {
          id: "event-1",
          event_id: "event-1",
          organization_id: "org-1",
          aggregate_type: "run",
          aggregate_id: "run-1",
          sequence: 1,
          name: "answer.delta",
          run_id: "run-1",
          run_sequence: 1,
          causation_id: null,
          correlation_id: "c-1",
          actor_type: "AGENT",
          actor_id: null,
          schema_version: 1,
          classification: "INTERNAL",
          payload: { delta: "ok" },
          created_at: "2026-08-29T00:00:00Z",
        },
      ];
    } else if (path === "/api/v1/runs/run-1") {
      body = {
        id: "run-1",
        turn_id: "turn-1",
        status: "COMPLETED",
        agent_version_id: null,
        model_profile_id: null,
        intent: {},
        plan: {},
        max_steps: 30,
        timeout_seconds: 120,
        max_input_tokens: 1,
        max_output_tokens: 1,
        max_cost_amount: "0",
        step_count: 6,
        input_tokens: 0,
        output_tokens: 0,
        cost_amount: "0",
        started_at: null,
        completed_at: null,
        cancellation_requested_at: null,
        error_code: null,
        error_message: null,
        replay_of_run_id: null,
        created_at: "2026-08-29T00:00:00Z",
        updated_at: "2026-08-29T00:00:00Z",
      };
    } else {
      body = [];
    }
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  return () => {
    globalThis.fetch = originalFetch;
  };
}

test("session stores tokens only in the secret file and asks through the runtime", async () => {
  const dir = await mkdtemp(join(tmpdir(), "obsion-desktop-session-"));
  const host = new WindowHost(
    { baseUrl: "http://obsion.example", protocol: "rest" },
    {},
    new FileSecretStore(join(dir, "desktop.secret")),
  );
  const restore = installRestAsk();
  try {
    const session = new DesktopSession(host, async (settings) => {
      assert.equal(settings.token, "stored-token");
      return new ExperienceRuntime(
        { ...settings, waitTimeoutMs: 1000, pollIntervalMs: 5 },
        new ObsionClient(settings.baseUrl, () => settings.token ?? ""),
        undefined,
        (prefix) => `${prefix}-fixed`,
        (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
      );
    });
    await session.setToken("stored-token");
    assert.equal(await host.getSecret("obsion.token"), "stored-token");
    const result = await session.ask("你好");
    assert.equal(result.answer, "ok");
    assert.equal(session.lastRunId, "run-1");
    assert.equal(host.lastOutput.includes("stored-token"), false);
  } finally {
    restore();
  }
});

test("loopback window server serves the desktop shell and never echoes the token", async () => {
  const dir = await mkdtemp(join(tmpdir(), "obsion-desktop-ui-"));
  const host = new WindowHost(
    { baseUrl: "http://obsion.example", protocol: "rest" },
    {},
    new FileSecretStore(join(dir, "desktop.secret")),
  );
  const restore = installRestAsk();
  const session = new DesktopSession(host, async (settings) => {
    assert.equal(settings.token, "window-token");
    return new ExperienceRuntime(
      { ...settings, waitTimeoutMs: 1000, pollIntervalMs: 5 },
      new ObsionClient(settings.baseUrl, () => settings.token ?? ""),
      undefined,
      (prefix) => `${prefix}-fixed`,
      (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    );
  });
  const server = new DesktopWindowServer({ host, session });
  const url = await server.listen();
  try {
    const page = await fetch(url);
    const html = await page.text();
    assert.equal(html, DESKTOP_SHELL_HTML);
    assert.match(html, /App Server 客户端/);
    assert.match(html, /Evidence/);
    const saved = await fetch(`${url}/api/token`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: "window-token" }),
    });
    const status = await saved.json();
    assert.equal(status.hasToken, true);
    assert.equal(JSON.stringify(status).includes("window-token"), false);
    const asked = await fetch(`${url}/api/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: "你好" }),
    });
    const body = await asked.json();
    assert.match(body.rendered, /ok/);
    assert.equal(body.runId, "run-1");
    assert.equal(JSON.stringify(body).includes("window-token"), false);
  } finally {
    restore();
    await server.close();
  }
});

test("window server rejects non-loopback hosts and refuses non-loopback binds", async () => {
  const dir = await mkdtemp(join(tmpdir(), "obsion-desktop-bind-"));
  const host = new WindowHost(
    { baseUrl: "http://obsion.example", protocol: "rest" },
    {},
    new FileSecretStore(join(dir, "desktop.secret")),
  );
  const blocked = new DesktopWindowServer({ host, listenHost: "0.0.0.0" });
  await assert.rejects(() => blocked.listen(), /127\.0\.0\.1/);
  const server = new DesktopWindowServer({ host });
  const url = await server.listen();
  try {
    const parsed = new URL(url);
    const status = await new Promise((resolve, reject) => {
      const req = httpRequest(
        {
          hostname: parsed.hostname,
          port: parsed.port,
          path: "/",
          headers: { Host: `example.com:${parsed.port}` },
        },
        (response) => {
          resolve(response.statusCode);
        },
      );
      req.on("error", reject);
      req.end();
    });
    assert.equal(status, 403);
  } finally {
    await server.close();
  }
});

test("cancel and replay use the last Run from ask", async () => {
  const dir = await mkdtemp(join(tmpdir(), "obsion-desktop-run-"));
  const host = new WindowHost(
    { baseUrl: "http://obsion.example", protocol: "rest" },
    {},
    new FileSecretStore(join(dir, "desktop.secret")),
  );
  const calls = [];
  const session = new DesktopSession(host, async (settings) => ({
    settings,
    close() {},
    async cancelRun(runId) {
      calls.push(["cancel", runId]);
      return { id: runId, status: "CANCELLED" };
    },
    async replayRun(runId) {
      calls.push(["replay", runId]);
      return { id: "run-2", status: "RUNNING" };
    },
  }));
  session.lastRunId = "run-1";
  await session.cancelRun();
  await session.replayRun();
  assert.deepEqual(calls, [
    ["cancel", "run-1"],
    ["replay", "run-1"],
  ]);
  assert.equal(session.lastRunId, "run-2");
});

test("approve decides the first pending capability approval", async () => {
  const dir = await mkdtemp(join(tmpdir(), "obsion-desktop-appr-"));
  const host = new WindowHost(
    { baseUrl: "http://obsion.example", protocol: "rest" },
    {},
    new FileSecretStore(join(dir, "desktop.secret")),
  );
  const session = new DesktopSession(host, async (settings) => ({
    settings,
    close() {},
    async listApprovals(status) {
      assert.equal(status, "PENDING");
      return [{ id: "approval-1", status, capability: "metrics.query" }];
    },
    async decideApproval(id, input) {
      return { id, status: input.approve ? "APPROVED" : "REJECTED", capability: "metrics.query" };
    },
  }));
  await session.decide(true, "evidence is sufficient");
  assert.match(host.lastOutput, /APPROVED/);
  assert.equal(host.lastOutput.includes("evidence is sufficient"), false);
});

test("loadSettings used by the session factory matches host configuration", () => {
  const settings = loadSettings({ baseUrl: "http://obsion.example", protocol: "rest" }, {});
  assert.equal(settings.protocol, "rest");
});
