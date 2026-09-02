import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ArtifactsView } from "@/components/artifacts-view";
import { FilesView } from "@/components/files-view";
import { api } from "@/lib/api";
import type { Artifact, Workspace } from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      listWorkspaceFiles: vi.fn(),
      listWorkspaceArtifacts: vi.fn(),
      uploadArtifact: vi.fn(),
      downloadArtifact: vi.fn(),
    },
  };
});

const listWorkspaceFiles = vi.mocked(api.listWorkspaceFiles);
const listWorkspaceArtifacts = vi.mocked(api.listWorkspaceArtifacts);
const uploadArtifact = vi.mocked(api.uploadArtifact);
const downloadArtifact = vi.mocked(api.downloadArtifact);

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function workspace(): Workspace {
  return {
    id: "ws-1",
    name: "支付平台",
    description: "",
    classification: "CONFIDENTIAL",
    visibility: "PRIVATE",
    updated_at: "2026-09-01T08:00:00Z",
  };
}

function artifact(partial: Partial<Artifact> = {}): Artifact {
  return {
    id: "artifact-file-1",
    workspace_id: "ws-1",
    run_id: null,
    kind: "FILE",
    title: "incident.md",
    media_type: "text/markdown",
    inline_content: { markdown: "# Incident" },
    storage_key: "ws-1/artifacts/file-1",
    classification: "CONFIDENTIAL",
    lineage: { source: "workspace-files", filename: "incident.md" },
    path: "/reports/incident.md",
    file_version: 2,
    superseded_at: null,
    created_at: "2026-09-01T08:00:00Z",
    ...partial,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  listWorkspaceFiles.mockResolvedValue([artifact()]);
  listWorkspaceArtifacts.mockResolvedValue([
    artifact(),
    artifact({
      id: "artifact-report-1",
      run_id: "run-1",
      kind: "REPORT",
      title: "支付事故报告",
      storage_key: null,
      path: null,
      file_version: null,
      inline_content: {
        markdown: "# 支付事故报告",
        verification: {
          verified: true,
          confidence: 0.94,
          coverage: 1,
          missing_evidence: [],
          checks: { citations: true },
        },
      },
    }),
    artifact({
      id: "artifact-code-1",
      run_id: "run-1",
      kind: "CODE",
      title: "PaymentService",
      media_type: "application/json",
      storage_key: null,
      path: null,
      file_version: null,
      inline_content: { symbols: [{ name: "createPayment" }] },
    }),
  ]);
  uploadArtifact.mockResolvedValue(artifact());
  downloadArtifact.mockResolvedValue(new Blob(["content"], { type: "text/plain" }));
});

describe("FilesView interactions", () => {
  it("switches between the current file projection and immutable history", async () => {
    const historical = artifact({
      id: "artifact-file-old",
      path: "/reports/incident.md",
      file_version: 1,
      superseded_at: "2026-09-01T07:00:00Z",
    });
    listWorkspaceFiles
      .mockResolvedValueOnce([artifact()])
      .mockResolvedValue([artifact(), historical]);
    render(<FilesView workspace={workspace()} />);
    await screen.findByRole("button", { name: /\/reports\/incident\.md.*v2.*当前/ });
    fireEvent.click(screen.getByRole("button", { name: "含历史版本" }));

    await waitFor(() => expect(listWorkspaceFiles).toHaveBeenCalledWith("ws-1", true));
    await screen.findByRole("button", { name: /\/reports\/incident\.md.*v1.*已取代/ });
  });

  it("derives a safe default path from the selected filename", async () => {
    const created = artifact({
      id: "artifact-uploaded",
      title: "incident report.md",
      path: "/uploads/incident-report.md",
      file_version: 1,
    });
    listWorkspaceFiles.mockResolvedValueOnce([]).mockResolvedValue([created]);
    uploadArtifact.mockResolvedValue(created);
    render(<FilesView workspace={workspace()} />);
    await screen.findByText("工作区还没有路径化文件");
    const file = new File(["# Incident"], "incident report.md", { type: "text/markdown" });
    fireEvent.change(screen.getByLabelText("上传工作区文件"), { target: { files: [file] } });

    await waitFor(() => expect(uploadArtifact).toHaveBeenCalledTimes(1));
    const [workspaceId, form] = uploadArtifact.mock.calls[0];
    expect(workspaceId).toBe("ws-1");
    expect(form.get("path")).toBe("/uploads/incident-report.md");
    expect(form.get("classification")).toBe("CONFIDENTIAL");
    expect(form.get("lineage")).toBe(
      '{"source":"workspace-files","filename":"incident report.md"}',
    );
    expect((screen.getByLabelText("路径") as HTMLInputElement).value).toBe(
      "/uploads/incident-report.md",
    );
    await screen.findByRole("button", { name: /\/uploads\/incident-report\.md/ });
  });

  it("opens file detail and downloads through the artifact API", async () => {
    render(<FilesView workspace={workspace()} />);
    const card = await screen.findByRole("button", { name: /\/reports\/incident\.md/ });
    fireEvent.click(card);
    expect(screen.getByLabelText("文件详情")).toBeDefined();

    const createObjectURL = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:file");
    const revokeObjectURL = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => undefined);
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    fireEvent.click(screen.getByRole("button", { name: "下载" }));

    await waitFor(() => expect(downloadArtifact).toHaveBeenCalledWith("artifact-file-1"));
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(click).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:file");
  });
});

describe("ArtifactsView interactions", () => {
  it("filters artifacts by governed kind and query while hiding stale detail", async () => {
    render(<ArtifactsView workspace={workspace()} />);
    const report = await screen.findByRole("button", { name: /支付事故报告/ });
    fireEvent.click(report);
    expect(screen.getByLabelText("产物详情")).toBeDefined();

    fireEvent.click(screen.getByRole("button", { name: "代码与 SQL" }));
    expect(screen.getByText("PaymentService")).toBeDefined();
    expect(screen.queryByText("支付事故报告")).toBeNull();
    expect(screen.queryByLabelText("产物详情")).toBeNull();

    fireEvent.change(screen.getByPlaceholderText("搜索标题、类型或密级"), {
      target: { value: "missing" },
    });
    expect(screen.getByText("没有匹配的产物")).toBeDefined();
  });

  it("uploads an ACL-classified artifact and opens the created preview", async () => {
    const created = artifact({ id: "artifact-new", title: "runbook.md" });
    uploadArtifact.mockResolvedValue(created);
    render(<ArtifactsView workspace={workspace()} />);
    await screen.findByText("支付事故报告");
    const file = new File(["# Runbook"], "runbook.md", { type: "text/markdown" });
    fireEvent.change(screen.getByLabelText("上传工作区产物"), { target: { files: [file] } });

    await waitFor(() => expect(uploadArtifact).toHaveBeenCalledTimes(1));
    const [workspaceId, form] = uploadArtifact.mock.calls[0];
    expect(workspaceId).toBe("ws-1");
    expect(form.get("kind")).toBe("FILE");
    expect(form.get("classification")).toBe("CONFIDENTIAL");
    expect(form.get("lineage")).toBe(
      '{"source":"workspace-artifact-center","filename":"runbook.md"}',
    );
    expect(screen.getByLabelText("产物详情")).toBeDefined();
    expect(screen.getAllByText("runbook.md").length).toBeGreaterThan(0);
  });

  it("refreshes from the workspace projection and preserves a selected artifact by id", async () => {
    const current = artifact();
    const refreshed = artifact({ title: "incident-v2.md" });
    listWorkspaceArtifacts
      .mockResolvedValueOnce([current])
      .mockResolvedValue([refreshed]);
    render(<ArtifactsView workspace={workspace()} />);
    fireEvent.click(await screen.findByRole("button", { name: /incident\.md/ }));
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));

    await screen.findByRole("button", { name: /incident-v2\.md/ });
    expect(listWorkspaceArtifacts).toHaveBeenCalledTimes(2);
    expect(within(screen.getByLabelText("产物详情")).getByText("incident-v2.md")).toBeDefined();
  });
});
