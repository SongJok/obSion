import assert from "node:assert/strict";
import test from "node:test";

import { ObsionApiError, ObsionClient } from "../dist/index.js";

test("GET requests omit the JSON content type", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (_url, init) => {
    assert.equal(init.headers["Content-Type"], undefined);
    return new Response(JSON.stringify([]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ObsionClient("https://obsion.example");
    assert.deepEqual(await client.listWorkspaces(), []);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("structured errors preserve the correlation ID", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({ code: "denied", message: "Denied", correlation_id: "request-1" }),
      { status: 403, headers: { "Content-Type": "application/json" } },
    );
  try {
    const client = new ObsionClient("https://obsion.example");
    await assert.rejects(
      () => client.getRun("run-1"),
      (error) =>
        error instanceof ObsionApiError &&
        error.code === "denied" &&
        error.correlationId === "request-1",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("governed data and knowledge requests preserve their contracts", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    requests.push([new URL(url).pathname, init.method, init.body ? JSON.parse(init.body) : null]);
    return new Response(JSON.stringify(new URL(url).pathname.endsWith("search") ? [] : {}), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ObsionClient("https://obsion.example/");
    await client.queryData("thread-1", "上周收入是多少？");
    await client.searchKnowledge("发布流程", 4);
    assert.deepEqual(requests, [
      ["/api/v1/data/query", "POST", { thread_id: "thread-1", question: "上周收入是多少？" }],
      ["/api/v1/knowledge/search", "POST", { query: "发布流程", limit: 4 }],
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("artifact uploads retain the runtime-generated multipart boundary", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (_url, init) => {
    assert.ok(init.body instanceof FormData);
    assert.equal(init.headers["Content-Type"], undefined);
    assert.equal(init.body.get("title"), "Release evidence");
    return new Response(JSON.stringify({ id: "artifact-1" }), {
      status: 201,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ObsionClient("https://obsion.example");
    const artifact = await client.uploadArtifact("workspace-1", {
      title: "Release evidence",
      filename: "release.txt",
      content: new Blob(["release evidence"]),
    });
    assert.equal(artifact.id, "artifact-1");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("automation requests preserve lifecycle and idempotency contracts", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    requests.push([new URL(url).pathname, init.method, init.body ? JSON.parse(init.body) : null]);
    return new Response(JSON.stringify({ id: "automation-resource" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ObsionClient("https://obsion.example");
    await client.triggerWorkflow("workflow-1", {
      inputPayload: { service: "payments" },
      idempotencyKey: "payments-2026-08-25",
    });
    await client.reviewAutomationStep("step-1", "APPROVE", "Evidence verified");
    await client.setScheduleEnabled("schedule-1", false);
    assert.deepEqual(requests, [
      [
        "/api/v1/workflows/workflow-1/trigger",
        "POST",
        {
          input_payload: { service: "payments" },
          idempotency_key: "payments-2026-08-25",
        },
      ],
      [
        "/api/v1/automation/steps/step-1/review",
        "POST",
        { decision: "APPROVE", reason: "Evidence verified" },
      ],
      ["/api/v1/automation/schedules/schedule-1", "PATCH", { enabled: false }],
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("action requests preserve approval and rollback contracts", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    requests.push([new URL(url).pathname, init.method, init.body ? JSON.parse(init.body) : null]);
    return new Response(JSON.stringify({ id: "action-resource" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ObsionClient("https://obsion.example");
    await client.createAction("workspace-1", {
      action_type: "CREATE_TICKET",
      title: "Payment incident",
      environment: "staging",
      target: { project_key: "OPS" },
      parameters: { summary: "Payment incident", description: "Investigate" },
      idempotency_key: "ticket-1",
    });
    await client.preflightAction("action-1", "Evidence and rollback verified");
    await client.decideActionApproval("approval-1", true, "Approved by operator");
    await client.requestActionRollback("action-1", "Close validation ticket");
    assert.deepEqual(requests, [
      [
        "/api/v1/workspaces/workspace-1/actions",
        "POST",
        {
          action_type: "CREATE_TICKET",
          title: "Payment incident",
          environment: "staging",
          target: { project_key: "OPS" },
          parameters: { summary: "Payment incident", description: "Investigate" },
          idempotency_key: "ticket-1",
        },
      ],
      [
        "/api/v1/actions/action-1/preflight",
        "POST",
        { reason: "Evidence and rollback verified", approval_ttl_minutes: 60 },
      ],
      [
        "/api/v1/action-approvals/approval-1/approve",
        "POST",
        { reason: "Approved by operator" },
      ],
      [
        "/api/v1/actions/action-1/rollback",
        "POST",
        { reason: "Close validation ticket", approval_ttl_minutes: 60 },
      ],
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
