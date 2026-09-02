import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CodeView } from "@/components/code-view";
import { api } from "@/lib/api";
import type { CodeRepository, CodeSymbolHit } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listCodeRepositories: vi.fn(),
      searchCodeSymbols: vi.fn(),
    },
  };
});

const listCodeRepositories = vi.mocked(api.listCodeRepositories);
const searchCodeSymbols = vi.mocked(api.searchCodeSymbols);

afterEach(() => {
  cleanup();
});

function repository(): CodeRepository {
  return {
    id: "repository-1",
    name: "obsion/payments",
    default_branch: "main",
    classification: "INTERNAL",
    current_snapshot_id: "snapshot-1",
    created_at: "2026-09-01T08:00:00Z",
    updated_at: "2026-09-01T08:00:00Z",
  };
}

function symbol(partial: Partial<CodeSymbolHit> = {}): CodeSymbolHit {
  return {
    repository_id: "repository-1",
    repository: "obsion/payments",
    commit_id: "abc1234def",
    snapshot_id: "snapshot-1",
    symbol_id: "symbol-1",
    path: "src/payment/service.py",
    language: "python",
    kind: "FUNCTION",
    name: "create_payment",
    qualified_name: "payment.service.create_payment",
    start_line: 42,
    end_line: 68,
    relations: [],
    ...partial,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

beforeEach(() => {
  vi.clearAllMocks();
  listCodeRepositories.mockResolvedValue([repository()]);
  searchCodeSymbols.mockResolvedValue([symbol()]);
});

describe("CodeView interactions", () => {
  it("loads the authorized repository count and searches a trimmed symbol query", async () => {
    render(<CodeView />);
    await screen.findByText("1 个授权仓库");
    fireEvent.change(screen.getByPlaceholderText("搜索已授权的符号、API、类或 SQL 表"), {
      target: { value: "  create_payment  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));

    await waitFor(() => expect(searchCodeSymbols).toHaveBeenCalledWith("create_payment"));
    await screen.findByText("payment.service.create_payment");
    expect(screen.getByText("src/payment/service.py:42 · abc1234def")).toBeDefined();
    expect(screen.getByText("python")).toBeDefined();
  });

  it("distinguishes an authorized empty result from a transport failure", async () => {
    searchCodeSymbols.mockResolvedValueOnce([]).mockRejectedValueOnce(new Error("代码索引暂时不可用"));
    render(<CodeView />);
    await screen.findByText("1 个授权仓库");
    const input = screen.getByPlaceholderText("搜索已授权的符号、API、类或 SQL 表");

    fireEvent.change(input, { target: { value: "missing" } });
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));
    await screen.findByText("没有匹配的授权符号");

    fireEvent.change(input, { target: { value: "unavailable" } });
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));
    await screen.findByText("代码索引暂时不可用");
    expect(screen.queryByText("没有匹配的授权符号")).toBeNull();
  });

  it("clears stale symbols before a later search failure", async () => {
    searchCodeSymbols
      .mockResolvedValueOnce([symbol({ qualified_name: "payment.old_result" })])
      .mockRejectedValueOnce(new Error("检索失败"));
    render(<CodeView />);
    await screen.findByText("1 个授权仓库");
    const input = screen.getByPlaceholderText("搜索已授权的符号、API、类或 SQL 表");
    fireEvent.change(input, { target: { value: "old" } });
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));
    await screen.findByText("payment.old_result");

    fireEvent.change(input, { target: { value: "new" } });
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));
    await screen.findByText("检索失败");
    expect(screen.queryByText("payment.old_result")).toBeNull();
  });

  it("ignores a slower stale search response", async () => {
    const first = deferred<CodeSymbolHit[]>();
    const second = deferred<CodeSymbolHit[]>();
    searchCodeSymbols.mockImplementation((query) => query === "first" ? first.promise : second.promise);
    render(<CodeView />);
    await screen.findByText("1 个授权仓库");
    const input = screen.getByPlaceholderText("搜索已授权的符号、API、类或 SQL 表");
    fireEvent.change(input, { target: { value: "first" } });
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));
    fireEvent.change(input, { target: { value: "second" } });
    fireEvent.submit(input.closest("form") as HTMLFormElement);

    await act(async () => {
      second.resolve([symbol({ symbol_id: "symbol-new", qualified_name: "payment.new_result" })]);
      await second.promise;
    });
    await screen.findByText("payment.new_result");

    await act(async () => {
      first.resolve([symbol({ symbol_id: "symbol-old", qualified_name: "payment.stale_result" })]);
      await first.promise;
    });
    expect(screen.getByText("payment.new_result")).toBeDefined();
    expect(screen.queryByText("payment.stale_result")).toBeNull();
  });
});
