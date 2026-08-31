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
    await client.ingestFeishuDocument({ document_id: "doxcnPhase64Token" });
    await client.listCodeRepositories();
    await client.createCodeRepository({ name: "payment-service" });
    await client.indexCodeSnapshot("repo-1", {
      commit_id: "abc1234",
      files: [{ path: "src/app.py", content: "def ping():\n    return 1\n" }],
    });
    await client.searchCodeSymbols("OrderService", { limit: 8 });
    assert.deepEqual(requests, [
      ["/api/v1/data/query", "POST", { thread_id: "thread-1", question: "上周收入是多少？" }],
      ["/api/v1/knowledge/search", "POST", { query: "发布流程", limit: 4 }],
      [
        "/api/v1/knowledge/sources/feishu/documents",
        "POST",
        {
          obj_type: "auto",
          classification: "INTERNAL",
          acl: { organization: true },
          inherit_acl: false,
          document_id: "doxcnPhase64Token",
        },
      ],
      ["/api/v1/code/repositories", "GET", null],
      [
        "/api/v1/code/repositories",
        "POST",
        {
          classification: "INTERNAL",
          acl: { organization: true },
          default_branch: "main",
          name: "payment-service",
        },
      ],
      [
        "/api/v1/code/repositories/repo-1/snapshots",
        "POST",
        {
          commit_id: "abc1234",
          files: [{ path: "src/app.py", content: "def ping():\n    return 1\n" }],
        },
      ],
      ["/api/v1/code/symbols/search", "POST", { query: "OrderService", limit: 8 }],
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("feishu wiki space requests preserve their contracts", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    requests.push([new URL(url).pathname, init.method ?? "GET", init.body ? JSON.parse(init.body) : null]);
    return new Response(JSON.stringify([]), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ObsionClient("https://obsion.example/");
    await client.listFeishuSpaces();
    await client.listFeishuWikiNodes("7365887123");
    await client.syncFeishuSpace("7365887123");
    assert.deepEqual(requests, [
      ["/api/v1/knowledge/sources/feishu/spaces", "GET", null],
      ["/api/v1/knowledge/sources/feishu/spaces/7365887123/nodes", "GET", null],
      [
        "/api/v1/knowledge/sources/feishu/spaces/7365887123/sync",
        "POST",
        {
          classification: "INTERNAL",
          acl: { organization: true },
          inherit_acl: false,
        },
      ],
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("confluence knowledge requests preserve their contracts", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    requests.push([new URL(url).pathname, init.method ?? "GET", init.body ? JSON.parse(init.body) : null]);
    return new Response(JSON.stringify({}), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ObsionClient("https://obsion.example/");
    await client.ingestConfluencePage({ page_id: "4567890123" });
    await client.listConfluenceSpaces();
    await client.syncConfluenceSpace("111222333");
    assert.deepEqual(requests, [
      [
        "/api/v1/knowledge/sources/confluence/pages",
        "POST",
        {
          classification: "INTERNAL",
          acl: { organization: true },
          inherit_acl: false,
          page_id: "4567890123",
        },
      ],
      ["/api/v1/knowledge/sources/confluence/spaces", "GET", null],
      [
        "/api/v1/knowledge/sources/confluence/spaces/111222333/sync",
        "POST",
        {
          classification: "INTERNAL",
          acl: { organization: true },
          inherit_acl: false,
        },
      ],
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
    await client.getMemory("memory-1");
    await client.updateMemory("memory-1", { content: { timezone: "UTC" } });
    await client.revokeMemory("memory-1");
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
      ["/api/v1/memories/memory-1", "GET", null],
      ["/api/v1/memories/memory-1", "PATCH", { content: { timezone: "UTC" } }],
      ["/api/v1/memories/memory-1", "DELETE", null],
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
    await client.getRuntimeSlo();
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
      ["/api/v1/admin/slo", "GET", null],
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
    assert.equal(init.body.get("path"), "/releases/notes.txt");
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
      path: "/releases/notes.txt",
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

test("capability approval list and decide use the governed REST surface", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    const parsed = new URL(url);
    requests.push([
      `${parsed.pathname}${parsed.search}`,
      init.method ?? "GET",
      init.body ? JSON.parse(init.body) : null,
    ]);
    return new Response(JSON.stringify({ id: "approval-1", status: "APPROVED" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ObsionClient("https://obsion.example");
    await client.listApprovals("PENDING");
    await client.decideApproval("approval-1", { approve: true, reason: "Matches policy" });
    assert.deepEqual(requests, [
      ["/api/v1/approvals?status=PENDING", "GET", null],
      ["/api/v1/approvals/approval-1/approve", "POST", { reason: "Matches policy" }],
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("IM sender mapping uses the control-plane identity surface", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    const parsed = new URL(url);
    requests.push([
      parsed.pathname,
      init.method ?? "GET",
      init.body ? JSON.parse(init.body) : null,
    ]);
    if (parsed.pathname.endsWith("/im/messages")) {
      return new Response(JSON.stringify({ run_id: "run-1" }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response(JSON.stringify({ id: "binding-1", active: true }), {
      status: parsed.pathname.endsWith("/revoke") ? 200 : 201,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ObsionClient("https://obsion.example");
    await client.listImBindings();
    await client.createImBinding({
      channel: "development",
      sender_id: "alice-stable",
      user_id: "user-1",
    });
    await client.revokeImBinding("binding-1");
    await client.createImMessage({
      channel: "development",
      sender_id: "alice-stable",
      conversation_id: "ops-room",
      text: "你好",
      sender_display: "Alice",
    });
    await client.prepareImDelivery("run-1");
    await client.completeImDelivery("delivery-1", { vendor_message_id: "om_1" });
    await client.failImDelivery("delivery-1");
    assert.deepEqual(requests, [
      ["/api/v1/admin/im-bindings", "GET", null],
      [
        "/api/v1/admin/im-bindings",
        "POST",
        { channel: "development", sender_id: "alice-stable", user_id: "user-1" },
      ],
      ["/api/v1/admin/im-bindings/binding-1/revoke", "POST", null],
      [
        "/api/v1/experience/im/messages",
        "POST",
        {
          channel: "development",
          sender_id: "alice-stable",
          conversation_id: "ops-room",
          text: "你好",
          sender_display: "Alice",
        },
      ],
      ["/api/v1/experience/im/runs/run-1/deliveries", "POST", null],
      [
        "/api/v1/experience/im/deliveries/delivery-1/complete",
        "POST",
        { vendor_message_id: "om_1" },
      ],
      [
        "/api/v1/experience/im/deliveries/delivery-1/fail",
        "POST",
        { failure_code: "vendor_request_failed" },
      ],
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("studio registry methods call control-plane studio routes", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    const parsed = new URL(url);
    requests.push([
      parsed.pathname,
      init.method ?? "GET",
      init.body ? JSON.parse(init.body) : null,
    ]);
    return new Response(JSON.stringify({ agents: [], skills: [] }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ObsionClient("https://obsion.example");
    await client.listStudioCatalog();
    await client.validateStudioDocument("kind: Agent");
    await client.publishStudioAgent("kind: Agent");
    await client.publishStudioSkill("kind: Skill");
    await client.promoteStudioVersion({
      kind: "Agent",
      name: "studio-probe-agent",
      version: 1,
    });
    await client.rollbackStudioVersion({
      kind: "Agent",
      name: "studio-probe-agent",
      version: 1,
    });
    await client.compareStudioVersions({
      kind: "Agent",
      name: "studio-probe-agent",
      baseline_version: 1,
      candidate_version: 2,
    });
    assert.deepEqual(requests, [
      ["/api/v1/studio/catalog", "GET", null],
      ["/api/v1/studio/validate", "POST", { document: "kind: Agent" }],
      ["/api/v1/studio/agents", "POST", { document: "kind: Agent" }],
      ["/api/v1/studio/skills", "POST", { document: "kind: Skill" }],
      [
        "/api/v1/studio/promote",
        "POST",
        { kind: "Agent", name: "studio-probe-agent", version: 1 },
      ],
      [
        "/api/v1/studio/rollback",
        "POST",
        { kind: "Agent", name: "studio-probe-agent", version: 1 },
      ],
      [
        "/api/v1/studio/compare",
        "POST",
        {
          kind: "Agent",
          name: "studio-probe-agent",
          baseline_version: 1,
          candidate_version: 2,
        },
      ],
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("connector and capability admin methods call control-plane admin routes", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    const parsed = new URL(url);
    requests.push([
      parsed.pathname,
      init.method ?? "GET",
      init.body ? JSON.parse(init.body) : null,
    ]);
    return new Response(JSON.stringify({ id: "created" }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ObsionClient("https://obsion.example");
    await client.listConnectors();
    await client.createConnector({
      name: "obsion-workflow-dispatch-test",
      connector_type: "workflow-development",
      environment: "development",
      status: "ACTIVE",
      declared_grants: ["automation.trigger"],
      allowed_egress: [],
    });
    await client.listAdminCapabilities();
    await client.listOperatorInvocations({ status: "UNKNOWN", limit: 25 });
    await client.bindCapability("capability-1", {
      connector_id: "connector-1",
      environment: "development",
    });
    await client.probeConnectorHealth("connector-1");
    await client.discoverConnector("connector-1");
    await client.scanConnectorPlugin("connector-1");
    await client.promoteConnectorPlugin("connector-1");
    assert.deepEqual(requests, [
      ["/api/v1/admin/connectors", "GET", null],
      [
        "/api/v1/admin/connectors",
        "POST",
        {
          name: "obsion-workflow-dispatch-test",
          connector_type: "workflow-development",
          environment: "development",
          status: "ACTIVE",
          declared_grants: ["automation.trigger"],
          allowed_egress: [],
        },
      ],
      ["/api/v1/admin/capabilities", "GET", null],
      ["/api/v1/admin/operator-invocations", "GET", null],
      [
        "/api/v1/admin/capabilities/capability-1/bindings",
        "POST",
        {
          resource_selector: {},
          connector_id: "connector-1",
          environment: "development",
        },
      ],
      ["/api/v1/admin/connectors/connector-1/health", "POST", null],
      ["/api/v1/admin/connectors/connector-1/discover", "POST", null],
      ["/api/v1/admin/connectors/connector-1/scan", "POST", null],
      ["/api/v1/admin/connectors/connector-1/promote", "POST", null],
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("eval console requests wrap Experience Eval routes", async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (url, init) => {
    requests.push([
      new URL(url).pathname,
      init.method ?? "GET",
      init.body ? JSON.parse(init.body) : null,
    ]);
    return new Response(JSON.stringify({ datasets: [], gate_passed: true }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    const client = new ObsionClient("https://obsion.example");
    await client.listEvalCatalog();
    await client.createEvalDataset({ name: "routing", domain: "foundation" });
    await client.addEvalCase("dataset-1", {
      external_id: "route-001",
      evaluator: "ROUTING",
    });
    await client.startEvalRun("dataset-1", {
      agent_version_id: "agent-1",
      application_revision: "rev-1",
    });
    await client.compareEvalRuns({
      baselineRunId: "run-1",
      candidateRunId: "run-2",
    });
    assert.deepEqual(requests, [
      ["/api/v1/eval/catalog", "GET", null],
      [
        "/api/v1/eval/datasets",
        "POST",
        { name: "routing", domain: "foundation" },
      ],
      [
        "/api/v1/eval/datasets/dataset-1/cases",
        "POST",
        { external_id: "route-001", evaluator: "ROUTING" },
      ],
      [
        "/api/v1/eval/datasets/dataset-1/runs",
        "POST",
        { agent_version_id: "agent-1", application_revision: "rev-1" },
      ],
      [
        "/api/v1/eval/compare",
        "POST",
        { baseline_run_id: "run-1", candidate_run_id: "run-2" },
      ],
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
