import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StudioView } from "@/components/studio-view";
import { api } from "@/lib/api";
import type {
  StudioCatalog,
  StudioCompare,
  StudioValidateResult,
  StudioVersion,
} from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      studio: {
        catalog: vi.fn(),
        validate: vi.fn(),
        publishAgent: vi.fn(),
        publishSkill: vi.fn(),
        promote: vi.fn(),
        rollback: vi.fn(),
        compare: vi.fn(),
      },
    },
  };
});

const studio = vi.mocked(api.studio);

afterEach(() => {
  cleanup();
});

function version(
  kind: StudioVersion["kind"],
  versionNumber: number,
  promoted = false,
): StudioVersion {
  const name = kind === "Agent" ? "example-agent" : "example-skill";
  return {
    kind,
    name,
    display_name: name,
    description: `${kind} version ${versionNumber}`,
    definition_id: `${kind.toLowerCase()}-definition-1`,
    version_id: `${kind.toLowerCase()}-version-${versionNumber}`,
    version: versionNumber,
    status: "ACTIVE",
    checksum_sha256: String(versionNumber).repeat(64),
    promoted,
    promoted_at: promoted ? "2026-09-01T08:00:00Z" : null,
    spec: kind === "Agent"
      ? { modelPolicy: { profile: "reasoning-high" }, capabilities: ["knowledge.search"] }
      : { instructions: ["answer from Evidence"], requiredEvidence: ["DOCUMENT"] },
  };
}

function catalog(
  promotedAgentVersion = 2,
  promotedSkillVersion = 2,
  extraAgents: StudioVersion[] = [],
  extraSkills: StudioVersion[] = [],
): StudioCatalog {
  return {
    agents: [
      ...extraAgents,
      version("Agent", 2, promotedAgentVersion === 2),
      version("Agent", 1, promotedAgentVersion === 1),
    ],
    skills: [
      ...extraSkills,
      version("Skill", 2, promotedSkillVersion === 2),
      version("Skill", 1, promotedSkillVersion === 1),
    ],
  };
}

function comparison(): StudioCompare {
  return {
    kind: "Agent",
    name: "example-agent",
    baseline: { version: 1, checksum_sha256: "1".repeat(64), promoted: false },
    candidate: { version: 2, checksum_sha256: "2".repeat(64), promoted: true },
    identical: false,
    changes: [{ path: "spec.maxSteps", baseline: 8, candidate: 12 }],
    traffic_split: false,
    evaluation: "Pin two independent Evaluation Runs before promotion.",
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  studio.catalog.mockResolvedValue(catalog());
  studio.compare.mockResolvedValue(comparison());
  studio.validate.mockResolvedValue({
    kind: "Workflow",
    name: "example-workflow",
    checksum_sha256: "a".repeat(64),
    preview: { steps: 1 },
  } satisfies StudioValidateResult);
});

describe("StudioView interactions", () => {
  it("uses accessible roving tabs and keeps Workflow validation-only", async () => {
    render(<StudioView />);
    await screen.findByText(/example-agent/);
    const agentTab = screen.getByRole("tab", { name: "Agent" });
    expect(agentTab.getAttribute("aria-selected")).toBe("true");

    agentTab.focus();
    fireEvent.keyDown(agentTab, { key: "ArrowRight" });
    const skillTab = screen.getByRole("tab", { name: "Skill" });
    expect(skillTab.getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(skillTab);

    fireEvent.keyDown(skillTab, { key: "End" });
    const workflowTab = screen.getByRole("tab", { name: "Workflow" });
    expect(workflowTab.getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(workflowTab);
    expect(screen.getByDisplayValue(/kind: Workflow/)).toBeDefined();
    expect(screen.queryByRole("button", { name: "发布新版本" })).toBeNull();
  });

  it("validates a Workflow DAG without publishing it through Studio", async () => {
    render(<StudioView />);
    await screen.findByText(/example-agent/);
    fireEvent.click(screen.getByRole("tab", { name: "Workflow" }));
    const manifest = screen.getByLabelText("Agent、Skill 或 Workflow 清单") as HTMLTextAreaElement;
    fireEvent.click(screen.getByRole("button", { name: "校验" }));

    await waitFor(() => expect(studio.validate).toHaveBeenCalledWith(manifest.value));
    await screen.findByText("Workflow · example-workflow");
    expect(studio.publishAgent).not.toHaveBeenCalled();
    expect(studio.publishSkill).not.toHaveBeenCalled();
  });

  it("publishes a new immutable Skill version without promoting it", async () => {
    const published = version("Skill", 3, false);
    studio.publishSkill.mockResolvedValue(published);
    studio.catalog
      .mockResolvedValueOnce(catalog())
      .mockResolvedValue(catalog(2, 2, [], [published]));
    render(<StudioView />);
    await screen.findByText(/example-agent/);
    fireEvent.click(screen.getByRole("tab", { name: "Skill" }));
    const documentInput = screen.getByLabelText("Agent、Skill 或 Workflow 清单");
    fireEvent.change(documentInput, { target: { value: '{"kind":"Skill","metadata":{"name":"example-skill"},"spec":{}}' } });
    fireEvent.click(screen.getByRole("button", { name: "发布新版本" }));

    await waitFor(() => expect(studio.publishSkill).toHaveBeenCalledTimes(1));
    await screen.findByText("已发布 example-skill v3（未提升，不会绑定新的对话）");
    expect(screen.getByDisplayValue(/"kind": "Skill"/)).toBeDefined();
  });

  it("compares immutable versions with no runtime traffic split and clears stale baselines", async () => {
    render(<StudioView />);
    await screen.findByText(/example-agent/);
    fireEvent.change(screen.getByLabelText("对比基线"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "对比版本" }));

    await waitFor(() =>
      expect(studio.compare).toHaveBeenCalledWith({
        kind: "Agent",
        name: "example-agent",
        baseline_version: 1,
        candidate_version: 2,
      }),
    );
    await screen.findByText("no traffic split");

    fireEvent.click(screen.getByRole("tab", { name: "Skill" }));
    const baseline = screen.getByLabelText("对比基线") as HTMLSelectElement;
    expect(baseline.value).toBe("");
    expect((screen.getByRole("button", { name: "对比版本" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("promotes only the explicitly selected immutable version", async () => {
    studio.promote.mockResolvedValue(version("Agent", 1, true));
    studio.catalog
      .mockResolvedValueOnce(catalog())
      .mockResolvedValue(catalog(1));
    render(<StudioView />);
    fireEvent.click(await screen.findByRole("button", { name: /example-agent.*v1.*未提升/ }));
    fireEvent.click(screen.getByRole("button", { name: "设为运行版本" }));

    await waitFor(() =>
      expect(studio.promote).toHaveBeenCalledWith({
        kind: "Agent",
        name: "example-agent",
        version: 1,
      }),
    );
    await screen.findByText("已将 example-agent v1 设为当前运行版本");
  });

  it("rolls back by promoting an older immutable version without rewriting it", async () => {
    studio.rollback.mockResolvedValue(version("Agent", 1, true));
    studio.catalog
      .mockResolvedValueOnce(catalog())
      .mockResolvedValue(catalog(1));
    render(<StudioView />);
    fireEvent.click(await screen.findByRole("button", { name: /example-agent.*v1.*未提升/ }));
    fireEvent.click(screen.getByRole("button", { name: "回滚到此版本" }));

    await waitFor(() =>
      expect(studio.rollback).toHaveBeenCalledWith({
        kind: "Agent",
        name: "example-agent",
        version: 1,
      }),
    );
    await screen.findByText("已回滚 example-agent 到 v1。未改写旧版本正文。");
  });
});
