import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api request normalization", () => {
  it("returns parsed JSON for successful responses", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(200, [])));
    await expect(api.listEvidence("run-1")).resolves.toEqual([]);
  });

  it("sends credentials and no-store on every request", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => jsonResponse(200, []));
    vi.stubGlobal("fetch", fetchMock);
    await api.listEvidence("run-1");
    const init = fetchMock.mock.calls[0]?.[1];
    expect(init?.credentials).toBe("include");
    expect(init?.cache).toBe("no-store");
  });

  it("surfaces control-plane error codes, messages, and correlation ids", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResponse(403, { code: "capability_denied", message: "策略拒绝", correlation_id: "corr-1" }),
      ),
    );
    const error = await api.listEvidence("run-1").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).code).toBe("capability_denied");
    expect((error as ApiError).message).toBe("策略拒绝");
    expect((error as ApiError).correlationId).toBe("corr-1");
  });

  it("normalizes network failures", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("fetch failed"))));
    const error = await api.listEvidence("run-1").catch((caught: unknown) => caught);
    expect((error as ApiError).code).toBe("network_error");
  });

  it("normalizes timeouts and cancellations distinctly", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new DOMException("timed out", "TimeoutError"))),
    );
    const timeout = await api.listEvidence("run-1").catch((caught: unknown) => caught);
    expect((timeout as ApiError).code).toBe("request_timeout");

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new DOMException("aborted", "AbortError"))),
    );
    const aborted = await api.listEvidence("run-1").catch((caught: unknown) => caught);
    expect((aborted as ApiError).code).toBe("request_cancelled");
  });

  it("rejects unparseable success bodies as invalid_response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>not json</html>", { status: 200 })),
    );
    const error = await api.listEvidence("run-1").catch((caught: unknown) => caught);
    expect((error as ApiError).code).toBe("invalid_response");
  });

  it("treats 204 as an empty success", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 })));
    await expect(api.deleteSession()).resolves.toBeUndefined();
  });
});
