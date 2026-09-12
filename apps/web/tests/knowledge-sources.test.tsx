import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { KnowledgeSources } from "@/components/knowledge-sources";
import { api } from "@/lib/api";
import type { KnowledgeSource } from "@/lib/knowledge-sources";

vi.mock("@/lib/api", () => ({ api: {
  knowledgeSourceConnections: vi.fn(), knowledgeSources: vi.fn(), registerKnowledgeSource: vi.fn(),
  controlKnowledgeSource: vi.fn(), knowledgeSourceItems: vi.fn(),
} }));

const source: KnowledgeSource = {
  id: "source-1", connector_id: "zziv-connection", name: "zziv 点仔文档", corp_id: "zziv-corp", active: true,
  state: "ATTENTION", counts: { files: 11, available: 3, pending: 0, partial: 7, failed: 1, removed: 0, containers: 3, awaiting_access_check: 0 },
  last_scan_completed_at: "2026-09-12T03:40:50Z", next_check_at: "2026-09-12T03:41:50Z", issue: null,
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.knowledgeSourceConnections).mockResolvedValue([
    { connector_id: "zziv-connection", binding_id: "zziv-binding", name: "zziv 点仔文档", corp_id: "zziv-corp" },
  ]);
  vi.mocked(api.knowledgeSources).mockResolvedValue({ items: [source], next_cursor: null });
  vi.mocked(api.registerKnowledgeSource).mockResolvedValue(source);
  vi.mocked(api.controlKnowledgeSource).mockResolvedValue({ ...source, active: false, state: "PAUSED", counts: { ...source.counts, available: 0 } });
  vi.mocked(api.knowledgeSourceItems).mockResolvedValue({ items: [
    { id: "item-1", title: "空白文档", kind: "adoc", status: "FAILED", available: false, document_id: null, revision: null, checked_at: null, issues: ["document_parse_failed"] },
  ], next_cursor: null });
});
afterEach(cleanup);
async function open() {
  render(<KnowledgeSources />);
  expect(api.knowledgeSources).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "管理同步来源" }));
  await screen.findByRole("article", { name: "zziv 点仔文档同步状态" });
}

describe("DingTalk source management", () => {
  it("shows usable counts separately from completed scans and exposes empty-content failures", async () => {
    await open();
    expect(screen.getByText("有待处理内容")).toBeInTheDocument();
    expect(screen.getByText(/已发现 11 个文件/)).toHaveTextContent("3 篇可用于问答");
    expect(screen.getByText(/内容不完整或格式不支持 7/)).toHaveTextContent("失败 1");
    fireEvent.click(screen.getByRole("button", { name: "查看文档状态" }));
    expect(await screen.findByText("文档没有可用正文。")).toBeInTheDocument();
    expect(screen.getByText("暂不可用")).toBeInTheDocument();
  });
  it("requires a visible organization choice when multiple connections exist", async () => {
    vi.mocked(api.knowledgeSourceConnections).mockResolvedValue([
      { connector_id: "other", binding_id: "other-binding", name: "另一个组织", corp_id: "other-corp" },
      { connector_id: "zziv-connection", binding_id: "zziv-binding", name: "zziv 点仔文档", corp_id: "zziv-corp" },
    ]);
    await open();
    expect(screen.getByRole("button", { name: "启用自动同步" })).toBeDisabled();
    fireEvent.change(screen.getByRole("combobox", { name: "钉钉组织连接" }), { target: { value: "zziv-connection" } });
    fireEvent.click(screen.getByRole("button", { name: "启用自动同步" }));
    await waitFor(() => expect(api.registerKnowledgeSource).toHaveBeenCalledWith({ connector_id: "zziv-connection", binding_id: "zziv-binding" }));
  });
  it("stops displaying old item access after pausing and queues resume without claiming availability", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "查看文档状态" }));
    await screen.findByText("空白文档");
    fireEvent.click(screen.getByRole("button", { name: "暂停同步" }));
    await screen.findByText("已暂停");
    expect(screen.queryByText("空白文档")).not.toBeInTheDocument();
    expect(screen.getByText(/已发现 11 个文件/)).toHaveTextContent("0 篇可用于问答");
    vi.mocked(api.controlKnowledgeSource).mockResolvedValue({ ...source, state: "QUEUED", counts: { ...source.counts, available: 0 } });
    fireEvent.click(screen.getByRole("button", { name: "恢复同步" }));
    await screen.findByText("等待同步");
    expect(api.controlKnowledgeSource).toHaveBeenLastCalledWith("source-1", "resume");
    expect(screen.getByText(/已发现 11 个文件/)).toHaveTextContent("0 篇可用于问答");
  });
  it("resumes an already registered paused source when the user explicitly enables it", async () => {
    vi.mocked(api.registerKnowledgeSource).mockResolvedValue({ ...source, active: false, state: "PAUSED" });
    vi.mocked(api.controlKnowledgeSource).mockResolvedValue({ ...source, state: "QUEUED", counts: { ...source.counts, available: 0 } });
    await open();
    fireEvent.click(screen.getByRole("button", { name: "启用自动同步" }));
    await waitFor(() => expect(api.controlKnowledgeSource).toHaveBeenCalledWith("source-1", "resume"));
    expect(await screen.findByText("等待同步")).toBeInTheDocument();
  });
  it("removes stale source and item availability when a refreshed inventory no longer includes it", async () => {
    await open();
    fireEvent.click(screen.getByRole("button", { name: "查看文档状态" }));
    await screen.findByText("空白文档");
    vi.mocked(api.knowledgeSources).mockResolvedValue({ items: [], next_cursor: null });
    fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
    await waitFor(() => expect(screen.queryByRole("article")).not.toBeInTheDocument());
    expect(screen.queryByText("空白文档")).not.toBeInTheDocument();
  });
  it("clears previously displayed metadata when source authorization is denied", async () => {
    await open();
    vi.mocked(api.knowledgeSources).mockRejectedValue(new Error("来源权限已失效"));
    fireEvent.click(screen.getByRole("button", { name: "刷新状态" }));
    await screen.findByRole("alert");
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "启用自动同步" })).toBeDisabled();
  });
});
