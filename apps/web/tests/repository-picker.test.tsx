import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RepositoryPicker } from "@/components/repository-picker";
import { api } from "@/lib/api";
import type { CodeRepository } from "@/lib/types";

vi.mock("@/lib/api", () => ({ api: { listCodeRepositories: vi.fn() } }));
const list = vi.mocked(api.listCodeRepositories);
const repository: CodeRepository = {
  id: "one", name: "selected-api", default_branch: "main", classification: "INTERNAL",
  current_snapshot_id: null, created_at: "2026-09-13T00:00:00Z", updated_at: "2026-09-13T00:00:00Z",
};
beforeEach(() => { vi.clearAllMocks(); list.mockResolvedValue([repository]); });
afterEach(cleanup);

describe("task repository selection", () => {
  it("loads only on request and emits an authorized selection", async () => {
    const change = vi.fn();
    render(<RepositoryPicker onChange={change} />);
    expect(list).not.toHaveBeenCalled();
    fireEvent.click(screen.getByLabelText("选择代码仓库"));
    await screen.findByRole("option", { name: "selected-api" });
    fireEvent.change(screen.getByLabelText("本轮仓库"), { target: { value: "selected-api" } });
    expect(change).toHaveBeenLastCalledWith("selected-api");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("clears a draft selection before refreshing and retains a visible denial", async () => {
    const change = vi.fn();
    list.mockRejectedValueOnce(new Error("仓库目录访问被拒绝"));
    render(<RepositoryPicker value="selected-api" onChange={change} />);
    fireEvent.click(screen.getByLabelText("选择代码仓库"));
    await screen.findByRole("alert");
    expect(change).toHaveBeenCalledWith(undefined);
    expect((screen.getByLabelText("本轮仓库") as HTMLSelectElement).disabled).toBe(true);
    expect(screen.queryByRole("option", { name: "selected-api" })).toBeNull();
  });

  it("aborts a late catalog response on task change and cannot select from the old task", async () => {
    let resolve!: (items: CodeRepository[]) => void;
    const pending = new Promise<CodeRepository[]>((done) => { resolve = done; });
    list.mockReturnValueOnce(pending);
    const change = vi.fn();
    const { rerender } = render(<RepositoryPicker key="thread-one" onChange={change} />);
    fireEvent.click(screen.getByLabelText("选择代码仓库"));
    const signal = list.mock.calls[0][0];
    rerender(<RepositoryPicker key="thread-two" onChange={change} />);
    await act(async () => { resolve([repository]); await pending; });
    expect(signal?.aborted).toBe(true);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(change).not.toHaveBeenCalledWith("selected-api");
  });

  it("disables changes during submission and distinguishes an empty catalog", async () => {
    list.mockResolvedValueOnce([]);
    const { rerender } = render(<RepositoryPicker disabled onChange={vi.fn()} />);
    fireEvent.click(screen.getByLabelText("选择代码仓库"));
    expect(list).not.toHaveBeenCalled();
    rerender(<RepositoryPicker onChange={vi.fn()} />);
    fireEvent.click(screen.getByLabelText("选择代码仓库"));
    await screen.findByText("暂无可选的授权仓库。");
  });
});
