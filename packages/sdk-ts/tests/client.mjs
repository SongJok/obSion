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

test("thread lifecycle requests preserve archive, resume, fork, and event contracts", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    const parsed = new URL(url);
    requests.push([
      `${parsed.pathname}${parsed.search}`,
      init.method ?? "GET",
      init.body ? JSON.parse(init.body) : null,
    ]);
    return new Response(JSON.stringify(init.method === "POST" ? { id: "thread-1" } : []), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ObsionClient("https://obsion.example");
    await client.listThreads("workspace-1", true);
    await client.archiveThread("thread-1");
    await client.resumeThread("thread-1");
    await client.forkThread("thread-1", {
      title: "Alternative investigation",
      from_turn_id: "turn-4",
    });
    await client.listThreadEvents("thread-1", 3, 25);
    assert.deepEqual(requests, [
      ["/api/v1/workspaces/workspace-1/threads?include_archived=true", "GET", null],
      ["/api/v1/threads/thread-1/archive", "POST", null],
      ["/api/v1/threads/thread-1/resume", "POST", null],
      [
        "/api/v1/threads/thread-1/fork",
        "POST",
        { title: "Alternative investigation", from_turn_id: "turn-4" },
      ],
      ["/api/v1/threads/thread-1/events?after_sequence=3&limit=25", "GET", null],
    ]);
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

test("metric catalog requests expose governed lineage", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    requests.push([new URL(url).pathname, init.method ?? "GET"]);
    return new Response(JSON.stringify([]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ObsionClient("https://obsion.example");
    await client.listMetrics();
    await client.getMetricLineage("metric-1");
    await client.validateSql("SELECT 1 LIMIT 1", "source-1");
    await client.explainSql("SELECT 1 LIMIT 1", "source-1");
    assert.deepEqual(requests, [
      ["/api/v1/data/metrics", "GET"],
      ["/api/v1/data/lineage/metric-1", "GET"],
      ["/api/v1/data/sql/validate", "POST"],
      ["/api/v1/data/sql/explain", "POST"],
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("semantic catalog requests preserve governed admin paths", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    requests.push([new URL(url).pathname, init.method ?? "GET"]);
    return new Response(JSON.stringify({ id: "semantic-1" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ObsionClient("https://obsion.example");
    await client.getDataCatalog();
    await client.createMetric({ name: "paid_user_count" });
    await client.createDimension({ name: "payer" });
    await client.createEntity({ name: "payer" });
    await client.createRelation({ source_entity_id: "entity-1" });
    await client.createBusinessRule({ name: "successful_payment" });
    await client.createTimeDefinition({ name: "business_day" });
    await client.createSemanticSynonym({ term: "付费人数" });
    assert.deepEqual(requests, [
      ["/api/v1/admin/data/catalog", "GET"],
      ["/api/v1/admin/data/metrics", "POST"],
      ["/api/v1/admin/data/dimensions", "POST"],
      ["/api/v1/admin/data/entities", "POST"],
      ["/api/v1/admin/data/relations", "POST"],
      ["/api/v1/admin/data/rules", "POST"],
      ["/api/v1/admin/data/time-definitions", "POST"],
      ["/api/v1/admin/data/synonyms", "POST"],
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("evaluation requests preserve immutable gate contracts", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    requests.push([new URL(url).pathname, init.method, init.body ? JSON.parse(init.body) : null]);
    return new Response(JSON.stringify({ id: "evaluation-resource", gate_passed: true }), {
      status: 201,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ObsionClient("https://obsion.example");
    await client.addEvaluationCase("dataset-1", {
      external_id: "route-001",
      evaluator: "ROUTING",
      input_payload: { question: "What is the policy?" },
      expected: { route: "KNOWLEDGE" },
    });
    await client.runEvaluation("dataset-1", {
      agent_version_id: "agent-version-1",
      model_profile_id: "profile-1",
      application_revision: "revision-1",
      baseline_run_id: "baseline-1",
      minimum_pass_rate: 1,
    });
    assert.deepEqual(requests, [
      [
        "/api/v1/admin/evaluations/datasets/dataset-1/cases",
        "POST",
        {
          external_id: "route-001",
          evaluator: "ROUTING",
          input_payload: { question: "What is the policy?" },
          expected: { route: "KNOWLEDGE" },
        },
      ],
      [
        "/api/v1/admin/evaluations/datasets/dataset-1/runs",
        "POST",
        {
          agent_version_id: "agent-version-1",
          model_profile_id: "profile-1",
          application_revision: "revision-1",
          baseline_run_id: "baseline-1",
          minimum_pass_rate: 1,
        },
      ],
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("memory requests expose governed context snapshots", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    const parsed = new URL(url);
    requests.push([
      `${parsed.pathname}${parsed.search}`,
      init.method ?? "GET",
      init.body ? JSON.parse(init.body) : null,
    ]);
    return new Response(JSON.stringify([]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ObsionClient("https://obsion.example");
    await client.createMemory({
      scope: "WORKSPACE",
      owner_ref: "workspace-1",
      content: { timezone: "UTC" },
    });
    await client.listMemories({
      scope: "WORKSPACE",
      ownerRef: "workspace-1",
      status: "APPROVED",
    });
    await client.listRunMemories("run-1");
    await client.listRunConversation("run-1");
    await client.decideMemory("memory-1", true, "Governed preference");
    assert.deepEqual(requests, [
      [
        "/api/v1/memories",
        "POST",
        {
          scope: "WORKSPACE",
          owner_ref: "workspace-1",
          content: { timezone: "UTC" },
        },
      ],
      [
        "/api/v1/memories?scope=WORKSPACE&owner_ref=workspace-1&status=APPROVED",
        "GET",
        null,
      ],
      ["/api/v1/runs/run-1/memories", "GET", null],
      ["/api/v1/runs/run-1/conversation", "GET", null],
      [
        "/api/v1/memories/memory-1/approve",
        "POST",
        { reason: "Governed preference" },
      ],
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("workspace collaboration requests preserve version contracts", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    const parsed = new URL(url);
    requests.push([
      `${parsed.pathname}${parsed.search}`,
      init.method ?? "GET",
      init.body ? JSON.parse(init.body) : null,
    ]);
    return new Response(JSON.stringify({ id: "collaboration-record" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ObsionClient("https://obsion.example");
    await client.createWorkspaceTask("workspace-1", {
      title: "Verify impact",
      priority: "CRITICAL",
    });
    await client.listWorkspaceTasks("workspace-1", {
      status: "OPEN",
      assigneeId: "user-1",
    });
    await client.updateWorkspaceTask("task-1", {
      expected_version: 1,
      status: "IN_PROGRESS",
    });
    await client.createWorkspaceDecision("workspace-1", {
      title: "Use immutable evidence",
      summary: "Preserve history",
      rationale: "Required for replay",
    });
    await client.decideWorkspaceDecision("decision-1", true, 2);
    await client.listWorkspaceDecisionVersions("decision-1");
    assert.deepEqual(requests, [
      [
        "/api/v1/workspaces/workspace-1/tasks",
        "POST",
        { title: "Verify impact", priority: "CRITICAL" },
      ],
      [
        "/api/v1/workspaces/workspace-1/tasks?status=OPEN&assignee_id=user-1&limit=200",
        "GET",
        null,
      ],
      [
        "/api/v1/workspace-tasks/task-1",
        "PATCH",
        { expected_version: 1, status: "IN_PROGRESS" },
      ],
      [
        "/api/v1/workspaces/workspace-1/decisions",
        "POST",
        {
          title: "Use immutable evidence",
          summary: "Preserve history",
          rationale: "Required for replay",
        },
      ],
      [
        "/api/v1/workspace-decisions/decision-1/accept",
        "POST",
        { expected_version: 2 },
      ],
      ["/api/v1/workspace-decisions/decision-1/versions", "GET", null],
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("run feedback requests preserve rating and version contracts", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    const parsed = new URL(url);
    requests.push([
      parsed.pathname,
      init.method ?? "GET",
      init.body ? JSON.parse(init.body) : null,
    ]);
    return new Response(JSON.stringify({ id: "feedback-1", version: 2 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ObsionClient("https://obsion.example");
    await client.getRunFeedback("run-1");
    await client.recordRunFeedback("run-1", {
      rating: "NEEDS_IMPROVEMENT",
      reason: "Missing evidence",
      expected_version: 1,
    });
    await client.getFeedbackSummary();
    assert.deepEqual(requests, [
      ["/api/v1/runs/run-1/feedback", "GET", null],
      [
        "/api/v1/runs/run-1/feedback",
        "PUT",
        {
          reason: "Missing evidence",
          rating: "NEEDS_IMPROVEMENT",
          expected_version: 1,
        },
      ],
      ["/api/v1/admin/feedback/summary", "GET", null],
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
