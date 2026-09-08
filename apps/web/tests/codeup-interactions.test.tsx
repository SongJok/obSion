import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CodeupReader } from "@/components/codeup-reader";
import { api, ApiError } from "@/lib/api";
import type { CodeRepository, CodeupReadResult } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, api: { ...actual.api, readCodeupRepository: vi.fn() } };
});
const remoteRead = vi.mocked(api.readCodeupRepository);
const SHA = "a".repeat(40);
const repositories: CodeRepository[] = ["one", "two"].map((id) => ({
  id, name: `example/${id}`, default_branch: "main", classification: "RESTRICTED",
  current_snapshot_id: null, created_at: "2026-09-06T00:00:00Z", updated_at: "2026-09-06T00:00:00Z",
}));
const metadata = (id = "one"): CodeupReadResult => ({
  operation: "codeup.repository.get", repository: `example/${id}`, repository_id: id,
  items: [{ id: "123", name: `remote-${id}`, default_branch: "main", visibility: "private" }],
  count: 1, next_page: null, complete: true, policy_decision_id: "decision-1",
});

function selectRepository(id = "one") {
  fireEvent.change(screen.getByLabelText("授权仓库"), { target: { value: id } });
}
function query() { fireEvent.click(screen.getByRole("button", { name: "查询云效" })); }
beforeEach(() => { vi.clearAllMocks(); remoteRead.mockResolvedValue(metadata()); });
afterEach(cleanup);

describe("Codeup remote reads", () => {
  it("requires a selected authorized repository and reports configuration guidance", async () => {
    remoteRead.mockRejectedValueOnce(new ApiError("codeup_configuration_invalid", "该项目尚未连接云效", "request-1"));
    render(<CodeupReader repositories={repositories} />);
    expect(remoteRead).not.toHaveBeenCalled();
    selectRepository();
    query();
    await screen.findByRole("alert");
    expect(screen.getByText(/请管理员在连接器中填写云效组织/)).toBeDefined();
    expect(screen.getByText("查询编号：request-1")).toBeDefined();
    expect(remoteRead).toHaveBeenCalledWith("one", { operation: "codeup.repository.get" }, expect.any(AbortSignal));
  });

  it("clears old content before a later access denial", async () => {
    remoteRead.mockResolvedValueOnce(metadata()).mockRejectedValueOnce(new ApiError("codeup_repository_denied", "无访问权限"));
    render(<CodeupReader repositories={repositories} />);
    selectRepository(); query();
    await screen.findByText("remote-one");
    query();
    expect(screen.queryByText("remote-one")).toBeNull();
    await screen.findByText("无访问权限");
    expect(screen.getByText(/请项目管理员核对你的仓库访问权限/)).toBeDefined();
  });

  it("ignores a late response after switching repositories and aborts its request", async () => {
    let resolve!: (result: CodeupReadResult) => void;
    const pending = new Promise<CodeupReadResult>((done) => { resolve = done; });
    remoteRead.mockReturnValueOnce(pending).mockResolvedValueOnce(metadata("two"));
    render(<CodeupReader repositories={repositories} />);
    selectRepository(); query();
    const signal = remoteRead.mock.calls[0][2];
    selectRepository("two");
    expect(signal?.aborted).toBe(true);
    query();
    await screen.findByText("remote-two");
    await act(async () => { resolve(metadata()); await pending; });
    expect(screen.queryByText("remote-one")).toBeNull();
    expect(screen.getByText("remote-two")).toBeDefined();
  });

  it("requires a fixed commit for file reads and renders content as text", async () => {
    remoteRead.mockResolvedValueOnce({
      operation: "codeup.file.read", repository: "example/one", repository_id: "one",
      items: [{ path: "README.md", commit_id: SHA, blob_id: "b".repeat(40),
        content: "<script>secret()</script> [REDACTED]", redacted: true, source_size_bytes: 39,
        content_sha256: "c".repeat(64) }], count: 1, complete: true, next_page: null,
      policy_decision_id: "decision-1",
    });
    const { container } = render(<CodeupReader repositories={repositories} />);
    selectRepository();
    fireEvent.change(screen.getByLabelText("查询内容"), { target: { value: "codeup.file.read" } });
    fireEvent.change(screen.getByLabelText("文件路径"), { target: { value: "README.md" } });
    query(); expect(remoteRead).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("完整提交编号"), { target: { value: SHA } });
    query();
    await screen.findByText("<script>secret()</script> [REDACTED]");
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText(/检出的敏感内容已隐藏/)).toBeDefined();
    expect(remoteRead).toHaveBeenCalledWith("one", { operation: "codeup.file.read", commit_id: SHA, path: "README.md" }, expect.any(AbortSignal));
  });

  it("requests the next page explicitly and distinguishes budget exhaustion from completeness", async () => {
    const result: CodeupReadResult = {
      operation: "codeup.commits.list", repository: "example/one", repository_id: "one", items: [],
      count: 0, complete: false, next_page: 2, policy_decision_id: "decision-1",
    };
    remoteRead.mockResolvedValueOnce(result).mockResolvedValueOnce({ ...result, next_page: null });
    render(<CodeupReader repositories={repositories} />);
    selectRepository();
    fireEvent.change(screen.getByLabelText("查询内容"), { target: { value: "codeup.commits.list" } });
    query();
    fireEvent.click(await screen.findByRole("button", { name: "下一页" }));
    await screen.findByText(/已达到本轮分页上限/);
    expect(screen.queryByRole("button", { name: "下一页" })).toBeNull();
    expect(remoteRead).toHaveBeenLastCalledWith("one", { operation: "codeup.commits.list", ref: "main", page: 2, limit: 20 }, expect.any(AbortSignal));
  });

  it("rejects a result attributed to a different repository", async () => {
    remoteRead.mockResolvedValueOnce(metadata("two"));
    render(<CodeupReader repositories={repositories} />);
    selectRepository(); query();
    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("查询结果与当前项目不一致"));
    expect(screen.queryByText("remote-two")).toBeNull();
  });
});
