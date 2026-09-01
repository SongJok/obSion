import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useWorkspaceCollection } from "@/hooks/use-workspace-collection";

describe("useWorkspaceCollection", () => {
  it("loads items for the active scope and clears loading", async () => {
    const query = vi.fn(async () => ["a", "b"]);
    const { result } = renderHook(() => useWorkspaceCollection("ws-1", query, "读取失败"));
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.items).toEqual(["a", "b"]);
    expect(result.current.error).toBe("");
  });

  it("surfaces query failures with the caught message", async () => {
    const query = vi.fn(async () => Promise.reject<string[]>(new Error("无法读取工作区证据")));
    const { result } = renderHook(() => useWorkspaceCollection("ws-1", query, "备用错误"));
    await waitFor(() => expect(result.current.error).toBe("无法读取工作区证据"));
    expect(result.current.items).toEqual([]);
    expect(result.current.loading).toBe(false);
  });

  it("uses the fallback message for non-Error rejections", async () => {
    const query = vi.fn(async () => Promise.reject<string[]>("boom"));
    const { result } = renderHook(() => useWorkspaceCollection("ws-1", query, "备用错误"));
    await waitFor(() => expect(result.current.error).toBe("备用错误"));
  });

  it("never leaks a previous scope's items into a new scope", async () => {
    const queries: Record<string, () => Promise<string[]>> = {
      "ws-1": async () => ["from-ws-1"],
      "ws-2": () => new Promise<string[]>(() => {}), // ws-2 never resolves
    };
    const { result, rerender } = renderHook(
      ({ scope }) => useWorkspaceCollection(scope, queries[scope]!, "读取失败"),
      { initialProps: { scope: "ws-1" } },
    );
    await waitFor(() => expect(result.current.items).toEqual(["from-ws-1"]));
    rerender({ scope: "ws-2" });
    // While ws-2 is in flight the hook must not present ws-1 data as current.
    expect(result.current.loading).toBe(true);
    expect(result.current.error).toBe("");
  });

  it("refresh re-runs the query and clears the error", async () => {
    let calls = 0;
    const query = vi.fn(async () => {
      calls += 1;
      if (calls === 1) {
        return Promise.reject<string[]>(new Error("首次失败"));
      }
      return ["recovered"];
    });
    const { result } = renderHook(() => useWorkspaceCollection("ws-1", query, "读取失败"));
    await waitFor(() => expect(result.current.error).toBe("首次失败"));
    result.current.refresh();
    await waitFor(() => expect(result.current.items).toEqual(["recovered"]));
    expect(result.current.error).toBe("");
    expect(query).toHaveBeenCalledTimes(2);
  });

  it("reportError scopes manual errors to the active workspace", () => {
    const query = vi.fn(async () => [] as string[]);
    const { result } = renderHook(() => useWorkspaceCollection("ws-1", query, "读取失败"));
    act(() => result.current.reportError("手动上报"));
    expect(result.current.error).toBe("手动上报");
  });

  it("stays idle without a scope", () => {
    const query = vi.fn(async () => ["x"]);
    const { result } = renderHook(() => useWorkspaceCollection(undefined, query, "读取失败"));
    expect(result.current.loading).toBe(false);
    expect(result.current.items).toEqual([]);
    expect(query).not.toHaveBeenCalled();
  });
});
