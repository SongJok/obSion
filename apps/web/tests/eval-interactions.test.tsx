import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EvalView } from "@/components/eval-view";
import { api } from "@/lib/api";
import type {
  EvalCase,
  EvalCatalog,
  EvalCompare,
  EvalDataset,
  EvalResult,
  EvalRun,
} from "@/lib/types";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      ...actual.api,
      eval: {
        catalog: vi.fn(),
        createDataset: vi.fn(),
        cases: vi.fn(),
        addCase: vi.fn(),
        startRun: vi.fn(),
        results: vi.fn(),
        compare: vi.fn(),
      },
    },
  };
});

const evalApi = vi.mocked(api.eval);

afterEach(() => {
  cleanup();
});

function dataset(id: string, name: string): EvalDataset {
  return {
    id,
    name,
    description: `${name} golden dataset`,
    domain: "foundation",
    created_at: "2026-09-01T08:00:00Z",
    updated_at: "2026-09-01T08:00:00Z",
  };
}

function evaluationRun(
  id: string,
  datasetId: string,
  revision: string,
  gatePassed: boolean,
): EvalRun {
  return {
    id,
    dataset_id: datasetId,
    application_revision: revision,
    status: "COMPLETED",
    gate_passed: gatePassed,
    metrics: { passed: gatePassed ? 3 : 2, total: 3 },
    snapshot_sha256: id.padEnd(64, "a").slice(0, 64),
  };
}

function evaluationCase(partial: Partial<EvalCase> = {}): EvalCase {
  return {
    id: "case-1",
    dataset_id: "dataset-1",
    external_id: "route-knowledge-001",
    version: 1,
    evaluator: "ROUTING",
    ...partial,
  };
}

function result(): EvalResult {
  return {
    id: "result-1",
    external_id: "route-knowledge-001",
    evaluator: "ROUTING",
    status: "PASSED",
  };
}

function catalog(extraDatasets: EvalDataset[] = [], extraRuns: EvalRun[] = []): EvalCatalog {
  return {
    datasets: [
      ...extraDatasets,
      dataset("dataset-1", "payments-eval"),
      dataset("dataset-2", "support-eval"),
    ],
    runs: [
      ...extraRuns,
      evaluationRun("run-baseline", "dataset-1", "baseline", true),
      evaluationRun("run-candidate", "dataset-1", "candidate", false),
      evaluationRun("run-support", "dataset-2", "support-candidate", true),
    ],
    agents: [
      { name: "general-agent", version: 2, version_id: "agent-version-2", checksum_sha256: "a".repeat(64) },
    ],
    prompts: [
      { name: "obsion-system-policy", version: 3, version_id: "prompt-version-3", checksum_sha256: "b".repeat(64) },
    ],
    model_profiles: [{ id: "profile-reasoning", name: "reasoning-high" }],
  };
}

function comparison(): EvalCompare {
  return {
    baseline: evaluationRun("run-baseline", "dataset-1", "baseline", true),
    candidate: evaluationRun("run-candidate", "dataset-1", "candidate", false),
    gate_passed: false,
    metrics: { baseline: { regressions: ["citation_precision"] } },
    agent_changed: false,
    prompt_changed: true,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  evalApi.catalog.mockResolvedValue(catalog());
  evalApi.cases.mockResolvedValue([evaluationCase()]);
  evalApi.addCase.mockResolvedValue(evaluationCase());
  evalApi.results.mockResolvedValue([result()]);
  evalApi.compare.mockResolvedValue(comparison());
});

describe("EvalView interactions", () => {
  it("loads the default dataset once and pins the preferred runtime versions", async () => {
    render(<EvalView />);
    await screen.findByText("route-knowledge-001");

    expect(evalApi.catalog).toHaveBeenCalledTimes(1);
    expect(evalApi.cases).toHaveBeenCalledTimes(1);
    expect(evalApi.cases).toHaveBeenCalledWith("dataset-1");
    expect((screen.getByLabelText("Agent 版本") as HTMLSelectElement).value).toBe("agent-version-2");
    expect((screen.getByLabelText("Prompt 版本") as HTMLSelectElement).value).toBe("obsion-system-policy:3");
    expect((screen.getByLabelText("模型配置") as HTMLSelectElement).value).toBe("profile-reasoning");
  });

  it("creates and scopes a new dataset without retaining an old baseline", async () => {
    const created = dataset("dataset-3", "release-eval");
    evalApi.createDataset.mockResolvedValue(created);
    evalApi.catalog
      .mockResolvedValueOnce(catalog())
      .mockResolvedValue(catalog([created]));
    render(<EvalView />);
    await screen.findByText("route-knowledge-001");
    fireEvent.change(screen.getByLabelText("基线评测"), { target: { value: "run-baseline" } });
    fireEvent.change(screen.getByLabelText("新数据集名称"), { target: { value: " release-eval " } });
    fireEvent.click(screen.getByRole("button", { name: "创建数据集" }));

    await waitFor(() =>
      expect(evalApi.createDataset).toHaveBeenCalledWith({
        name: "release-eval",
        domain: "foundation",
        description: "Workbench Eval dataset",
      }),
    );
    await screen.findByText("已创建数据集 release-eval");
    await waitFor(() => expect(evalApi.cases).toHaveBeenCalledWith("dataset-3"));
    expect((screen.getByLabelText("基线评测") as HTMLSelectElement).value).toBe("");
  });

  it("adds a schema-shaped case and rejects malformed JSON before transport", async () => {
    render(<EvalView />);
    await screen.findByText("route-knowledge-001");
    const documentInput = screen.getByLabelText("评测案例 JSON");
    fireEvent.change(documentInput, { target: { value: "{invalid" } });
    fireEvent.click(screen.getByRole("button", { name: "添加案例" }));
    await screen.findByText("评测案例不是合法的 JSON，请检查逗号、引号与括号。");
    expect(evalApi.addCase).not.toHaveBeenCalled();

    const payload = {
      external_id: "route-support-001",
      evaluator: "ROUTING",
      input_payload: { question: "Why did this ticket fail?" },
      expected: { route: "SUPPORT" },
      fixtures: {},
    };
    fireEvent.change(documentInput, { target: { value: JSON.stringify(payload) } });
    evalApi.addCase.mockResolvedValue(evaluationCase({ external_id: "route-support-001" }));
    fireEvent.click(screen.getByRole("button", { name: "添加案例" }));

    await waitFor(() => expect(evalApi.addCase).toHaveBeenCalledWith("dataset-1", payload));
    await screen.findByText("已添加案例 route-support-001");
  });

  it("starts an Evaluation Run with pinned Agent, Prompt, model, baseline, and Run bindings", async () => {
    const started = evaluationRun("run-new", "dataset-1", "candidate-2026-09-02", true);
    evalApi.startRun.mockResolvedValue(started);
    evalApi.catalog
      .mockResolvedValueOnce(catalog())
      .mockResolvedValue(catalog([], [started]));
    render(<EvalView />);
    await screen.findByText("route-knowledge-001");
    fireEvent.change(screen.getByLabelText("应用修订"), {
      target: { value: "candidate-2026-09-02" },
    });
    fireEvent.change(screen.getByLabelText("基线评测"), {
      target: { value: "run-baseline" },
    });
    fireEvent.change(screen.getByLabelText("run_bindings JSON"), {
      target: { value: '{"route-knowledge-001":"run-terminal-1"}' },
    });
    fireEvent.click(screen.getByRole("button", { name: "启动评测" }));

    await waitFor(() =>
      expect(evalApi.startRun).toHaveBeenCalledWith("dataset-1", {
        agent_version_id: "agent-version-2",
        model_profile_id: "profile-reasoning",
        application_revision: "candidate-2026-09-02",
        baseline_run_id: "run-baseline",
        run_bindings: { "route-knowledge-001": "run-terminal-1" },
        prompt_pins: { "obsion-system-policy": 3 },
      }),
    );
    await screen.findByText("评测通过 3/3");
    await screen.findByText("route-knowledge-001 · PASSED");
    expect(evalApi.results).toHaveBeenCalledWith("run-new");
  });

  it("rejects non-object or non-string run bindings before starting a Run", async () => {
    render(<EvalView />);
    await screen.findByText("route-knowledge-001");
    const bindings = screen.getByLabelText("run_bindings JSON");

    fireEvent.change(bindings, { target: { value: "[]" } });
    fireEvent.click(screen.getByRole("button", { name: "启动评测" }));
    await screen.findByText("run_bindings必须是 JSON 对象。");
    expect(evalApi.startRun).not.toHaveBeenCalled();

    fireEvent.change(bindings, { target: { value: '{"case-1":42}' } });
    fireEvent.click(screen.getByRole("button", { name: "启动评测" }));
    await screen.findByText("run_bindings 的每个值都必须是 Run ID 字符串。");
    expect(evalApi.startRun).not.toHaveBeenCalled();
  });

  it("compares distinct same-dataset Runs and clears the baseline on dataset switch", async () => {
    render(<EvalView />);
    await screen.findByText("route-knowledge-001");
    fireEvent.click(screen.getByRole("button", { name: /candidate.*阻断/ }));
    expect(screen.queryByRole("option", { name: "candidate" })).toBeNull();
    fireEvent.change(screen.getByLabelText("基线评测"), { target: { value: "run-baseline" } });
    fireEvent.click(screen.getByRole("button", { name: "对比基线" }));

    await waitFor(() =>
      expect(evalApi.compare).toHaveBeenCalledWith({
        baseline_run_id: "run-baseline",
        candidate_run_id: "run-candidate",
      }),
    );
    await screen.findByText("基线对比未通过：citation_precision");

    fireEvent.click(screen.getByRole("button", { name: /support-eval/ }));
    expect((screen.getByLabelText("基线评测") as HTMLSelectElement).value).toBe("");
    expect((screen.getByRole("button", { name: "对比基线" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
