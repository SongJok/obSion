import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { KnowledgeView } from "@/components/knowledge-view";
import { api } from "@/lib/api";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      knowledgeSearch: vi.fn(),
      uploadDocument: vi.fn(),
      ingestFeishuDocument: vi.fn(),
      ingestDingTalkDocument: vi.fn(),
      ingestWeComDocument: vi.fn(),
      syncFeishuSpace: vi.fn(),
      ingestConfluencePage: vi.fn(),
    },
  };
});

const knowledgeSearch = vi.mocked(api.knowledgeSearch);
const uploadDocument = vi.mocked(api.uploadDocument);
const ingestFeishuDocument = vi.mocked(api.ingestFeishuDocument);
const ingestDingTalkDocument = vi.mocked(api.ingestDingTalkDocument);
const ingestWeComDocument = vi.mocked(api.ingestWeComDocument);
const syncFeishuSpace = vi.mocked(api.syncFeishuSpace);
const ingestConfluencePage = vi.mocked(api.ingestConfluencePage);

afterEach(() => {
  cleanup();
});

function ingestion(title: string, source: string, externalId: string) {
  return {
    document: { id: `document-${source}`, title },
    chunk_count: 3,
    source,
    external_id: externalId,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  knowledgeSearch.mockResolvedValue([
    {
      chunk_id: "chunk-1",
      document_id: "document-1",
      version: 4,
      title: "支付故障处理 SOP",
      source: "feishu",
      heading_path: ["故障处理", "支付超时"],
      content: "先确认指标，再检查日志和发布。",
      score: 0.93,
      classification: "INTERNAL",
      external_id: "doccn_sop",
      revision_id: "revision-4",
      connector_name: "feishu-docs",
      operation: "document.read",
    },
  ]);
  uploadDocument.mockResolvedValue({
    document: { id: "document-upload", title: "payment-sop.md" },
    chunk_count: 2,
  });
  ingestFeishuDocument.mockResolvedValue(ingestion("飞书支付 SOP", "feishu", "doccn_1"));
  ingestDingTalkDocument.mockResolvedValue(ingestion("钉钉支付 SOP", "dingtalk", "ding-1"));
  ingestWeComDocument.mockResolvedValue(ingestion("企微支付 SOP", "wecom", "wecom-1"));
  ingestConfluencePage.mockResolvedValue(
    ingestion("Confluence 支付 SOP", "confluence", "page-1"),
  );
  syncFeishuSpace.mockResolvedValue({
    space_id: "space-1",
    ingested_count: 2,
    skipped_count: 1,
    failed_count: 0,
  });
});

describe("KnowledgeView interactions", () => {
  it("searches the trimmed authorized query and renders recorded provenance", async () => {
    render(<KnowledgeView />);
    fireEvent.change(screen.getByPlaceholderText("搜索已授权的制度、PRD、SOP 与技术文档"), {
      target: { value: "  支付超时  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));

    await waitFor(() => expect(knowledgeSearch).toHaveBeenCalledWith("支付超时"));
    await screen.findByText("支付故障处理 SOP");
    expect(screen.getByText("doccn_sop")).toBeDefined();
    expect(screen.getByText("revision-4")).toBeDefined();
    expect(screen.getByText("feishu-docs")).toBeDefined();
    expect(screen.getByText("document.read")).toBeDefined();
  });

  it("clears stale Evidence when a later search fails", async () => {
    knowledgeSearch
      .mockResolvedValueOnce([
        {
          chunk_id: "chunk-old",
          document_id: "document-old",
          version: 1,
          title: "旧查询结果",
          source: "knowledge",
          heading_path: [],
          content: "旧 Evidence",
          score: 0.8,
          classification: "INTERNAL",
        },
      ])
      .mockRejectedValueOnce(new Error("检索服务暂时不可用"));
    render(<KnowledgeView />);
    const input = screen.getByPlaceholderText("搜索已授权的制度、PRD、SOP 与技术文档");
    fireEvent.change(input, { target: { value: "第一次" } });
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));
    await screen.findByText("旧查询结果");

    fireEvent.change(input, { target: { value: "第二次" } });
    fireEvent.click(screen.getByRole("button", { name: "搜索" }));
    await screen.findByText("检索服务暂时不可用");
    expect(screen.queryByText("旧查询结果")).toBeNull();
    expect(screen.queryByText("旧 Evidence")).toBeNull();
  });

  it("uploads a document with classification and ACL metadata", async () => {
    render(<KnowledgeView />);
    const file = new File(["# Payment SOP"], "payment-sop.md", {
      type: "text/markdown",
      lastModified: 1788256800000,
    });
    fireEvent.change(screen.getByLabelText("上传知识文档"), {
      target: { files: [file] },
    });

    await waitFor(() => expect(uploadDocument).toHaveBeenCalledTimes(1));
    const form = uploadDocument.mock.calls[0][0];
    expect(form.get("file")).toBe(file);
    expect(form.get("source")).toBe("workbench-upload");
    expect(form.get("external_id")).toBe("payment-sop.md:1788256800000");
    expect(form.get("classification")).toBe("INTERNAL");
    expect(form.get("acl")).toBe('{"organization":true}');
    await screen.findByText("已摄取 payment-sop.md，生成 2 个结构化片段");
  });

  it("routes each vendor document ingestion through its dedicated API", async () => {
    render(<KnowledgeView />);
    const cases = [
      ["飞书文档或知识库节点 token", "  doccn_1  ", "摄取飞书文档", ingestFeishuDocument, { document_id: "doccn_1" }],
      ["钉钉文档 document id", " ding-1 ", "摄取钉钉文档", ingestDingTalkDocument, { document_id: "ding-1" }],
      ["企微文档 docid", " wecom-1 ", "摄取企微文档", ingestWeComDocument, { document_id: "wecom-1" }],
      ["Confluence Cloud 页面 ID", " page-1 ", "摄取 Confluence", ingestConfluencePage, { page_id: "page-1" }],
    ] as const;

    for (const [placeholder, value, buttonName, method, payload] of cases) {
      fireEvent.change(screen.getByPlaceholderText(placeholder), { target: { value } });
      fireEvent.click(screen.getByRole("button", { name: buttonName }));
      await waitFor(() => expect(method).toHaveBeenCalledWith(payload));
    }
  });

  it("synchronizes a Feishu knowledge space with bounded summary counts", async () => {
    render(<KnowledgeView />);
    fireEvent.change(screen.getByPlaceholderText("飞书知识库空间 ID"), {
      target: { value: "  space-1  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "同步飞书知识库" }));

    await waitFor(() => expect(syncFeishuSpace).toHaveBeenCalledWith("space-1"));
    await screen.findByText("已同步飞书知识库 space-1：摄取 2，跳过 1，失败 0");
  });
});
