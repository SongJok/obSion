"use client";

import { AlertTriangle, CheckCircle2, FlaskConical } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api, ApiError } from "@/lib/api";
import type {
  EvalAgentPin,
  EvalCatalog,
  EvalCase,
  EvalCompare,
  EvalProfilePin,
  EvalResult,
  EvalRun,
} from "@/lib/types";

const ROUTING_CASE = {
  external_id: "route-knowledge-001",
  evaluator: "ROUTING",
  input_payload: { question: "Summarize the employee handbook" },
  expected: { route: "KNOWLEDGE" },
  fixtures: {},
};

function regressionsOf(metrics: Record<string, unknown>): string[] {
  const baseline = metrics.baseline;
  if (!baseline || typeof baseline !== "object" || Array.isArray(baseline)) return [];
  const regressions = (baseline as Record<string, unknown>).regressions;
  return Array.isArray(regressions) ? regressions.map(String) : [];
}

export function EvalView() {
  const [catalog, setCatalog] = useState<EvalCatalog>();
  const [datasetId, setDatasetId] = useState("");
  const [cases, setCases] = useState<EvalCase[]>([]);
  const [results, setResults] = useState<EvalResult[]>([]);
  const [selectedRunId, setSelectedRunId] = useState("");
  const [baselineRunId, setBaselineRunId] = useState("");
  const [compare, setCompare] = useState<EvalCompare>();
  const [datasetName, setDatasetName] = useState("eval-probe");
  const [caseDocument, setCaseDocument] = useState(JSON.stringify(ROUTING_CASE, null, 2));
  const [revision, setRevision] = useState("workbench");
  const [bindings, setBindings] = useState("{}");
  const [agentVersionId, setAgentVersionId] = useState("");
  const [promptVersion, setPromptVersion] = useState("");
  const [profileId, setProfileId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  const datasets = catalog?.datasets ?? [];
  const agents = catalog?.agents ?? [];
  const prompts = catalog?.prompts ?? [];
  const profiles = catalog?.model_profiles ?? [];
  const runs = useMemo(
    () => (catalog?.runs ?? []).filter((item) => !datasetId || item.dataset_id === datasetId),
    [catalog, datasetId],
  );

  const load = async (preserveDataset?: string) => {
    try {
      const next = await api.eval.catalog();
      setCatalog(next);
      const selected = preserveDataset && next.datasets.some((item) => item.id === preserveDataset)
        ? preserveDataset
        : next.datasets[0]?.id ?? "";
      setDatasetId(selected);
      const general = next.agents.find((item) => item.name === "general-agent") ?? next.agents[0];
      const policy =
        next.prompts.find((item) => item.name === "obsion-system-policy") ?? next.prompts[0];
      const reasoning = next.model_profiles.find((item) => item.name === "reasoning-high") ?? next.model_profiles[0];
      setAgentVersionId((current) => current || general?.version_id || "");
      setPromptVersion((current) => current || (policy ? `${policy.name}:${policy.version}` : ""));
      setProfileId((current) => current || reasoning?.id || "");
      setError("");
      return { catalog: next, selected };
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : "无法读取评测目录");
      return undefined;
    } finally {
      setLoading(false);
    }
  };

  const loadCases = async (id: string) => {
    if (!id) {
      setCases([]);
      return;
    }
    setCases(await api.eval.cases(id));
  };

  useEffect(() => {
    void (async () => {
      const next = await load();
      if (next?.selected) await loadCases(next.selected);
    })();
  }, []);

  useEffect(() => {
    if (!datasetId) return;
    let cancelled = false;
    void api.eval
      .cases(datasetId)
      .then((next) => {
        if (!cancelled) setCases(next);
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "无法读取评测案例");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [datasetId]);

  const run = async (action: "dataset" | "case" | "start" | "compare") => {
    setBusy(true);
    setError("");
    setNotice("");
    setCompare(undefined);
    try {
      if (action === "dataset") {
        const created = await api.eval.createDataset({
          name: datasetName.trim(),
          domain: "foundation",
          description: "Workbench Eval dataset",
        });
        setNotice(`已创建数据集 ${created.name}`);
        await load(created.id);
        return;
      }
      if (!datasetId) throw new Error("请先选择或创建数据集");
      if (action === "case") {
        const payload = JSON.parse(caseDocument) as Record<string, unknown>;
        const created = await api.eval.addCase(datasetId, payload);
        setNotice(`已添加案例 ${created.external_id}`);
        await loadCases(datasetId);
        return;
      }
      if (action === "start") {
        const started = await api.eval.startRun(datasetId, {
          agent_version_id: agentVersionId,
          model_profile_id: profileId,
          application_revision: revision.trim() || "workbench",
          baseline_run_id: baselineRunId || undefined,
          run_bindings: JSON.parse(bindings || "{}") as Record<string, string>,
          prompt_pins: promptVersion
            ? {
                [promptVersion.split(":")[0] ?? "obsion-system-policy"]: Number(
                  promptVersion.split(":")[1],
                ),
              }
            : undefined,
        });
        setNotice(
          started.gate_passed
            ? `评测通过 ${started.metrics.passed}/${started.metrics.total}`
            : `评测未通过 ${started.metrics.passed}/${started.metrics.total}`,
        );
        setSelectedRunId(started.id);
        const next = await load(datasetId);
        if (next) await loadCases(datasetId);
        setResults(await api.eval.results(started.id));
        return;
      }
      if (!selectedRunId || !baselineRunId) throw new Error("请选择基线评测和候选评测");
      const compared = await api.eval.compare({
        baseline_run_id: baselineRunId,
        candidate_run_id: selectedRunId,
      });
      setCompare(compared);
      setNotice(
        compared.gate_passed
          ? "基线对比通过，无回归"
          : `基线对比未通过：${regressionsOf(compared.metrics).join(", ") || "门禁失败"}`,
      );
    } catch (caught: unknown) {
      const message =
        caught instanceof ApiError ? caught.message : caught instanceof Error ? caught.message : "操作失败";
      setError(message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="feature-page eval-page">
      <header className="feature-header">
        <div>
          <span className="eyebrow">OBSION EVAL</span>
          <h1>Agent 评测台</h1>
          <p>
            Golden Dataset 绑定真实 terminal Run，比较 Agent 与 Prompt 版本。不接受 fixtures.actual，也不再实现一套
            Harness。运行时回滚仍走 Studio。每个 Turn 钉住 Prompt 快照。
          </p>
        </div>
        <span className="catalog-count">
          <FlaskConical size={17} /> {datasets.length} 个数据集
        </span>
      </header>

      <div className="eval-layout">
        <aside className="eval-catalog">
          {loading ? (
            <p className="studio-hint">正在加载目录…</p>
          ) : datasets.length === 0 ? (
            <p className="studio-hint">还没有评测数据集。</p>
          ) : (
            <div className="studio-item-list">
              {datasets.map((item) => (
                <button
                  key={item.id}
                  className={datasetId === item.id ? "active" : ""}
                  onClick={() => {
                    setDatasetId(item.id);
                    setSelectedRunId("");
                    setResults([]);
                    setCompare(undefined);
                  }}
                >
                  <strong>{item.name}</strong>
                  <small>{item.domain}</small>
                </button>
              ))}
            </div>
          )}
          <label>
            新数据集名称
            <input value={datasetName} onChange={(event) => setDatasetName(event.target.value)} />
          </label>
          <button onClick={() => void run("dataset")} disabled={busy || !datasetName.trim()}>
            创建数据集
          </button>
        </aside>

        <section className="eval-workspace">
          <article>
            <h2>案例</h2>
            <p className="studio-hint">RUN_OUTPUT 必须提供 run_ref，启动评测时再绑定真实 Run ID。禁止 fixtures.actual。</p>
            {cases.length === 0 ? (
              <p className="studio-hint">该数据集还没有案例。</p>
            ) : (
              <ul className="eval-case-list">
                {cases.map((item) => (
                  <li key={item.id}>
                    <strong>{item.external_id}</strong>
                    <small>
                      {item.evaluator} · v{item.version}
                    </small>
                  </li>
                ))}
              </ul>
            )}
            <label>
              案例 JSON
              <textarea
                value={caseDocument}
                onChange={(event) => setCaseDocument(event.target.value)}
                spellCheck={false}
                aria-label="评测案例 JSON"
              />
            </label>
            <button onClick={() => void run("case")} disabled={busy || !datasetId}>
              添加案例
            </button>
          </article>

          <article>
            <h2>评测运行</h2>
            <div className="eval-run-form">
              <label>
                Agent 版本
                <select value={agentVersionId} onChange={(event) => setAgentVersionId(event.target.value)}>
                  {agents.map((item: EvalAgentPin) => (
                    <option key={item.version_id} value={item.version_id}>
                      {item.name} v{item.version}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Prompt 版本
                <select value={promptVersion} onChange={(event) => setPromptVersion(event.target.value)}>
                  {prompts.map((item: EvalAgentPin) => (
                    <option key={item.version_id} value={`${item.name}:${item.version}`}>
                      {item.name} v{item.version}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                模型配置
                <select value={profileId} onChange={(event) => setProfileId(event.target.value)}>
                  {profiles.map((item: EvalProfilePin) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                应用修订
                <input value={revision} onChange={(event) => setRevision(event.target.value)} />
              </label>
              <label>
                基线评测
                <select value={baselineRunId} onChange={(event) => setBaselineRunId(event.target.value)}>
                  <option value="">无</option>
                  {runs.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.application_revision}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                run_bindings JSON
                <textarea value={bindings} onChange={(event) => setBindings(event.target.value)} spellCheck={false} />
              </label>
            </div>
            <div className="studio-actions">
              <button onClick={() => void run("start")} disabled={busy || !datasetId || !agentVersionId || !profileId}>
                启动评测
              </button>
              <button
                className="secondary-button"
                onClick={() => void run("compare")}
                disabled={busy || !selectedRunId || !baselineRunId}
              >
                对比基线
              </button>
            </div>
            <div className="studio-item-list">
              {runs.map((item: EvalRun) => (
                <button
                  key={item.id}
                  className={selectedRunId === item.id ? "active" : ""}
                  onClick={() => {
                    setSelectedRunId(item.id);
                    void api.eval.results(item.id).then(setResults);
                  }}
                >
                  <strong>{item.application_revision}</strong>
                  <small>
                    {item.gate_passed ? "通过" : "阻断"} · {Number(item.metrics.passed ?? 0)}/{Number(item.metrics.total ?? 0)}
                  </small>
                </button>
              ))}
            </div>
          </article>

          {results.length > 0 && (
            <article className="eval-results">
              <h2>案例结果</h2>
              {results.map((item) => (
                <div key={item.id}>
                  <strong>
                    {item.external_id} · {item.status}
                  </strong>
                  <code>{item.evaluator}</code>
                </div>
              ))}
            </article>
          )}
          {compare && (
            <article className="eval-results">
              <h2>版本对比</h2>
              <p>
                {compare.agent_changed ? "Agent 版本已变化。" : "Agent 版本相同。"}
                {compare.prompt_changed ? " Prompt 版本已变化。" : " Prompt 版本相同。"}
                回归 {regressionsOf(compare.metrics).length} 项。
              </p>
            </article>
          )}
          {error && (
            <div className="notice error">
              <AlertTriangle size={17} />
              <span>{error}</span>
            </div>
          )}
          {notice && (
            <div className="notice success">
              <CheckCircle2 size={17} />
              <span>{notice}</span>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
