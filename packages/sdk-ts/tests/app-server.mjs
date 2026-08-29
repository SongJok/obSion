import assert from "node:assert/strict";
import test from "node:test";

import {
  ObsionAppServerClient,
  ObsionAppServerError,
  appServerUrlFromApiUrl,
} from "../dist/index.js";

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
      if (request.method === "server.initialize") {
        this.emit({
          jsonrpc: "2.0",
          id: request.id,
          result: { protocol_version: "2026-08-26", methods: [] },
        });
      } else if (request.method === "thread.create") {
        this.emit({
          jsonrpc: "2.0",
          id: request.id,
          result: { id: "thread-1", title: request.params.title },
        });
      } else {
        this.emit({
          jsonrpc: "2.0",
          method: "run.completed",
          params: { event: { run_sequence: 9 } },
        });
        this.emit({
          jsonrpc: "2.0",
          id: request.id,
          error: {
            code: -32004,
            message: "Run was not found",
            data: {
              code: "resource_not_found",
              status: 404,
              correlation_id: "correlation-1",
              details: { resource: "Run" },
            },
          },
        });
      }
    });
  }

  close(code = 1000, reason = "") {
    this.readyState = 3;
    this.onclose?.({ code, reason });
  }

  emit(body) {
    this.onmessage?.({ data: JSON.stringify(body) });
  }
}

test("App Server client initializes, correlates requests, and emits notifications", async () => {
  let socket;
  const client = new ObsionAppServerClient("wss://obsion.example/api/v1/app-server", {
    token: "token",
    webSocketFactory: (url, protocols) => {
      socket = new FakeWebSocket(url, protocols);
      return socket;
    },
  });
  const initialized = await client.connect();
  assert.equal(initialized.protocol_version, "2026-08-26");
  assert.deepEqual(socket.protocols, ["obsion.jsonrpc.v1"]);
  assert.equal(socket.sent[0].params.bearer_token, "token");

  const thread = await client.createThread("workspace-1", "Investigation", "thread-create-1");
  assert.deepEqual(thread, { id: "thread-1", title: "Investigation" });
  assert.equal(socket.sent[1].params.client_request_id, "thread-create-1");

  const notifications = [];
  const stop = client.onNotification((message) => notifications.push(message));
  await assert.rejects(
    () => client.request("run.get", { run_id: "run-404" }),
    (error) =>
      error instanceof ObsionAppServerError &&
      error.code === "resource_not_found" &&
      error.status === 404 &&
      error.correlationId === "correlation-1",
  );
  assert.equal(notifications.at(-1).method, "run.completed");
  assert.equal(notifications.at(-1).params.event.run_sequence, 9);
  stop();
  client.close();
});

test("App Server URL derives from the versioned REST base", () => {
  assert.equal(
    appServerUrlFromApiUrl("https://obsion.example/api/v1/"),
    "wss://obsion.example/api/v1/app-server",
  );
});
