import { beforeEach, describe, expect, it, vi } from "vitest";

interface FakeNotification {
  params: Record<string, unknown>;
}

const clientState = vi.hoisted(() => ({
  failNextConnect: false,
  instances: [] as Array<{
    notifications: Array<(notification: FakeNotification) => void>;
    closed: boolean;
    failConnect: boolean;
    subscribed: Array<{ runId: string; after: number }>;
  }>,
}));

vi.mock("@obsion/sdk", () => {
  class FakeClient {
    notifications: Array<(notification: FakeNotification) => void> = [];
    closed = false;
    failConnect = false;
    subscribed: Array<{ runId: string; after: number }> = [];

    constructor(
      public url: string,
      public options: Record<string, unknown>,
    ) {
      this.failConnect = clientState.failNextConnect;
      clientState.instances.push(this);
    }

    onNotification(listener: (notification: FakeNotification) => void) {
      this.notifications.push(listener);
      return () => {
        this.notifications = this.notifications.filter((item) => item !== listener);
      };
    }

    async connect() {
      if (this.failConnect) {
        throw new Error("connect failed");
      }
    }

    async subscribeRun(runId: string, after: number) {
      this.subscribed.push({ runId, after });
      return { subscription_id: "sub-1" };
    }

    close() {
      this.closed = true;
    }
  }
  return {
    ObsionAppServerClient: FakeClient,
    appServerUrlFromApiUrl: (apiUrl: string) => apiUrl.replace("http", "ws"),
  };
});

import { streamRunEvents } from "@/lib/app-server";

function runEvent(overrides: Record<string, unknown> = {}) {
  return {
    id: "evt-1",
    name: "run.step.completed",
    sequence: 9,
    run_sequence: 4,
    created_at: "2026-08-21T10:00:00Z",
    run_id: "run-1",
    payload: { step: "trace.search" },
    ...overrides,
  };
}

function notify(notification: FakeNotification) {
  clientState.instances.forEach((instance) =>
    instance.notifications.forEach((listener) => listener(notification)),
  );
}

beforeEach(() => {
  clientState.instances.length = 0;
  clientState.failNextConnect = false;
});

describe("streamRunEvents", () => {
  it("forwards only events for the requested run and subscription", async () => {
    const received: unknown[] = [];
    await streamRunEvents("run-1", 3, (event) => received.push(event));
    notify({ params: { subscription_id: "sub-1", event: runEvent() } });
    notify({ params: { subscription_id: "sub-1", event: runEvent({ run_id: "run-2" }) } });
    notify({ params: { subscription_id: "sub-other", event: runEvent() } });
    expect(received).toHaveLength(1);
    expect((received[0] as { run_id: string }).run_id).toBe("run-1");
    expect(clientState.instances[0]?.subscribed).toEqual([{ runId: "run-1", after: 3 }]);
  });

  it("drops malformed notifications instead of throwing", async () => {
    const received: unknown[] = [];
    await streamRunEvents("run-1", 0, (event) => received.push(event));
    notify({ params: { subscription_id: "sub-1", event: { id: "evt-x" } } });
    notify({ params: { subscription_id: "sub-1", event: null } });
    notify({ params: {} });
    expect(received).toHaveLength(0);
  });

  it("stops forwarding and closes the client when the returned cleanup runs", async () => {
    const received: unknown[] = [];
    const stop = await streamRunEvents("run-1", 0, (event) => received.push(event));
    notify({ params: { subscription_id: "sub-1", event: runEvent() } });
    stop();
    notify({ params: { subscription_id: "sub-1", event: runEvent() } });
    expect(received).toHaveLength(1);
    expect(clientState.instances[0]?.closed).toBe(true);
  });

  it("cleans up the client when connect fails so no listener leaks", async () => {
    clientState.failNextConnect = true;
    await expect(streamRunEvents("run-1", 0, () => {})).rejects.toThrow("connect failed");
    const instance = clientState.instances[0]!;
    expect(instance.closed).toBe(true);
    expect(instance.notifications).toHaveLength(0);
  });
});
