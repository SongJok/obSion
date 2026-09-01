import assert from "node:assert/strict";
import test from "node:test";

import { IdeSession } from "../dist/commands.js";
import { loadSettings } from "../dist/config.js";
import { ExperienceRuntime } from "../dist/runtime.js";
import { ObsionClient } from "@obsion/sdk";

class MemoryHost {
  constructor() {
    this.secrets = new Map();
    this.output = [];
    this.inputs = [];
  }

  configuration() {
    return { baseUrl: "http://obsion.example", protocol: "rest" };
  }

  environment() {
    return {};
  }

  async getSecret(key) {
    return this.secrets.get(key);
  }

  async storeSecret(key, value) {
    this.secrets.set(key, value);
  }

  async deleteSecret(key) {
    this.secrets.delete(key);
  }

  async showInput() {
    return this.inputs.shift();
  }

  appendOutput(text) {
    this.output.push(text);
  }

  showError() {}
}

test("session stores tokens only in secret storage and asks through the runtime", async () => {
  const host = new MemoryHost();
  host.inputs.push("stored-token", "你好");
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    const path = new URL(url, "http://obsion.example").pathname;
    let body = {};
    if (path === "/api/v1/workspaces" && (init?.method ?? "GET") === "GET") {
      body = [
        {
          id: "workspace-1",
          name: "IDE",
          description: "",
          owner_id: "user-1",
          classification: "INTERNAL",
          visibility: "PRIVATE",
          created_at: "2026-08-29T00:00:00Z",
          updated_at: "2026-08-29T00:00:00Z",
          archived_at: null,
        },
      ];
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
  try {
    const session = new IdeSession(host, async (settings) => {
      assert.equal(settings.token, "stored-token");
      return new ExperienceRuntime(
        { ...settings, waitTimeoutMs: 1000, pollIntervalMs: 5 },
        new ObsionClient(settings.baseUrl, () => settings.token ?? ""),
        undefined,
        (prefix) => `${prefix}-fixed`,
        (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
      );
    });
    await session.setToken();
    assert.equal(host.secrets.get("obsion.token"), "stored-token");
    const result = await session.ask();
    assert.equal(result.answer, "ok");
    assert.equal(session.lastRunId, "run-1");
    assert.equal(host.output.some((line) => line.includes("stored-token")), false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("cancel and replay use the last Run from ask", async () => {
  const host = new MemoryHost();
  const calls = [];
  const session = new IdeSession(host, async (settings) => ({
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
  const host = new MemoryHost();
  host.inputs.push("evidence is sufficient");
  const session = new IdeSession(host, async (settings) => ({
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
  await session.decide(true);
  assert.match(host.output.at(-1), /APPROVED/);
  assert.equal(host.output.some((line) => line.includes("evidence is sufficient")), false);
});

test("dismissing the reason input cancels the decision without a fallback reason", async () => {
  const host = new MemoryHost();
  host.inputs.push(undefined);
  let decided = 0;
  const session = new IdeSession(host, async (settings) => ({
    settings,
    close() {},
    async listApprovals() {
      return [{ id: "approval-1", status: "PENDING", capability: "metrics.query" }];
    },
    async decideApproval() {
      decided += 1;
      throw new Error("decideApproval must not run when the operator cancels");
    },
  }));
  await session.decide(true);
  assert.equal(decided, 0);
  assert.match(host.output.at(-1), /cancelled/);
});

test("a blank approval reason is rejected instead of replaced by a canned string", async () => {
  const host = new MemoryHost();
  host.inputs.push("   ");
  let decided = 0;
  const session = new IdeSession(host, async (settings) => ({
    settings,
    close() {},
    async listApprovals() {
      return [{ id: "approval-1", status: "PENDING", capability: "metrics.query" }];
    },
    async decideApproval() {
      decided += 1;
      throw new Error("decideApproval must not run for a blank reason");
    },
  }));
  await assert.rejects(session.decide(false), /human-entered reason/);
  assert.equal(decided, 0);
});

test("loadSettings used by the session factory matches host configuration", () => {
  const settings = loadSettings({ baseUrl: "http://obsion.example", protocol: "rest" }, {});
  assert.equal(settings.protocol, "rest");
});
