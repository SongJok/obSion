import assert from "node:assert/strict";
import test from "node:test";

import { ObsionAppServerClient, ObsionClient } from "@obsion/sdk";

import { ExperienceRuntime } from "../dist/runtime.js";
import { loadSettings } from "../dist/config.js";
import { containsCredential, renderAsk } from "../dist/render.js";

class FakeWebSocket {
  readyState = 0;
  protocol = "obsion.jsonrpc.v1";
  onopen = null;
  onmessage = null;
  onerror = null;
  onclose = null;
  sent = [];

  constructor(url, protocols) {
    this.url = url;
    this.protocols = protocols;
    queueMicrotask(() => {
      this.readyState = 1;
      this.onopen?.({});
      this.emit({
        jsonrpc: "2.0",
        method: "server.ready",
        params: { protocol_version: "2026-08-26" },
      });
    });
  }

  send(raw) {
    const request = JSON.parse(raw);
    this.sent.push(request);
    queueMicrotask(() => {
      const method = request.method;
      let result = {};
      if (method === "server.initialize") {
        result = { protocol_version: "2026-08-26", methods: [] };
      } else if (method === "workspace.list") {
        result = [{ ...workspace(), name: "Desktop" }];
      } else if (method === "thread.create") {
        result = { ...thread(), title: request.params.title };
      } else if (method === "turn.create") {
        result = { turn: turn(), run: run("RUNNING") };
      } else if (method === "run.get") {
        result = run("COMPLETED");
      }
      this.emit({ jsonrpc: "2.0", id: request.id, result });
    });
  }

  close() {
    this.readyState = 3;
    this.onclose?.({ code: 1000, reason: "" });
  }

  emit(body) {
    this.onmessage?.({ data: JSON.stringify(body) });
  }
}

function workspace() {
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

function thread() {
  return {
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
}

function turn() {
  return {
    id: "turn-1",
    thread_id: "thread-1",
    ordinal: 1,
    created_by: "user-1",
    input_text: "你好",
    context_refs: [],
    attachment_refs: [],
    created_at: "2026-08-29T00:00:00Z",
  };
}

function run(status) {
  return {
    id: "run-1",
    turn_id: "turn-1",
    status,
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
}

function restHandler(url) {
  const path = new URL(url, "http://obsion.example").pathname;
  if (path === "/api/v1/workspaces" && !url.includes("include_archived")) {
    return [workspace()];
  }
  if (path === "/api/v1/runs/run-1/events") {
    return [
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
        payload: { delta: "你好。" },
        created_at: "2026-08-29T00:00:00Z",
      },
    ];
  }
  if (path.endsWith("/steps")) {
    return [{ kind: "OBSERVE" }, { kind: "VERIFY" }, { kind: "REFLECT" }, { kind: "RESPOND" }];
  }
  if (path.endsWith("/evidence") || path.endsWith("/claims") || path.endsWith("/artifacts")) {
    return [];
  }
  if (path === "/api/v1/runs/run-1") return run("COMPLETED");
  return [];
}

test("ask uses App Server for Thread and Turn mutations", async () => {
  let socket;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) =>
    new Response(JSON.stringify(restHandler(String(url))), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  try {
    const settings = {
      ...loadSettings({ protocol: "app-server" }, {}, "desktop-token"),
      waitTimeoutMs: 1000,
      pollIntervalMs: 5,
    };
    const rest = new ObsionClient(settings.baseUrl, () => "desktop-token");
    const appServer = new ObsionAppServerClient("ws://obsion.example/api/v1/app-server", {
      token: "desktop-token",
      webSocketFactory: (url, protocols) => {
        socket = new FakeWebSocket(url, protocols);
        return socket;
      },
    });
    await appServer.connect();
    const runtime = new ExperienceRuntime(
      settings,
      rest,
      appServer,
      (prefix) => `${prefix}-fixed`,
      (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    );
    const result = await runtime.ask("你好");
    runtime.close();
    const methods = socket.sent.map((item) => item.method);
    assert.ok(methods.includes("thread.create"));
    assert.ok(methods.includes("turn.create"));
    assert.equal(result.answer, "你好。");
    const rendered = renderAsk(result);
    assert.equal(containsCredential(rendered, "desktop-token"), false);
    assert.match(rendered, /REFLECT/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("REST protocol creates a Desktop workspace when none exists", async () => {
  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, init) => {
    calls.push([String(url), init?.method ?? "GET"]);
    const path = new URL(url, "http://obsion.example").pathname;
    let body = restHandler(String(url));
    if (path === "/api/v1/workspaces" && (init?.method ?? "GET") === "GET") body = [];
    if (path === "/api/v1/workspaces" && init?.method === "POST") body = workspace();
    if (path === "/api/v1/threads") body = thread();
    if (path.endsWith("/turns")) body = { turn: turn(), run: run("COMPLETED") };
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const settings = {
      ...loadSettings({ protocol: "rest" }, {}),
      waitTimeoutMs: 1000,
      pollIntervalMs: 5,
    };
    const runtime = new ExperienceRuntime(
      settings,
      new ObsionClient(settings.baseUrl),
      undefined,
      (prefix) => `${prefix}-fixed`,
      (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    );
    const result = await runtime.ask("你好");
    assert.equal(result.thread.id, "thread-1");
    assert.ok(calls.some((item) => item[0].includes("/api/v1/workspaces") && item[1] === "POST"));
  } finally {
    globalThis.fetch = originalFetch;
  }
});
