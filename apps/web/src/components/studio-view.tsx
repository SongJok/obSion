"use client";

import { AlertTriangle, CheckCircle2, PencilRuler, ShieldCheck } from "lucide-react";
import { KeyboardEvent as ReactKeyboardEvent, useEffect, useState } from "react";

import { api, ApiError } from "@/lib/api";
import type { StudioCompare, StudioValidateResult, StudioVersion } from "@/lib/types";

type StudioKind = "Agent" | "Skill" | "Workflow";

const STUDIO_KINDS: readonly StudioKind[] = ["Agent", "Skill", "Workflow"];

const TEMPLATES: Record<StudioKind, string> = {
  Agent: `apiVersion: obsion.dev/v1
kind: Agent
metadata:
  name: example-agent
spec:
  description: Example governed agent
  modelPolicy: {profile: reasoning-high}
  maxSteps: 12
  timeout: 180
  skills: []
  capabilities: [knowledge.search]
  riskPolicy: {maxLevel: L1}
  memory: {session: true}
  sandbox:
    enabled: true
    network: gateway-only
    mounts: [/workspace, /repo, /artifacts, /tmp]
`,
  Skill: `apiVersion: obsion.dev/v1
kind: Skill
metadata:
  name: example-skill
spec:
  instructions: [answer only from authorized DOCUMENT evidence]
  capabilities: [knowledge.search]
  requiredEvidence: [DOCUMENT]
  verification: [citation coverage]
`,
  Workflow: `apiVersion: obsion.dev/v1
kind: Workflow
metadata:
  name: example-workflow
spec:
  steps:
    - id: analyze
      name: Analyze
      type: ANALYSIS
      prompt: Summarize authorized evidence
`,
};

function toManifest(item: StudioVersion): string {
  return JSON.stringify(
    {
      apiVersion: "obsion.dev/v1",
      kind: item.kind,
      metadata: { name: item.name },
      spec: item.spec,
    },
    null,
    2,
  );
}

function versionsOf(items: StudioVersion[], name: string): StudioVersion[] {
  return items.filter((item) => item.name === name).sort((left, right) => right.version - left.version);
}

export function StudioView() {
  const [kind, setKind] = useState<StudioKind>("Agent");
  const [agents, setAgents] = useState<StudioVersion[]>([]);
  const [skills, setSkills] = useState<StudioVersion[]>([]);
  const [selected, setSelected] = useState<StudioVersion>();
  const [document, setDocument] = useState(TEMPLATES.Agent);
  const [preview, setPreview] = useState<StudioValidateResult>();
  const [compare, setCompare] = useState<StudioCompare>();
  const [compareVersion, setCompareVersion] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  const catalog = kind === "Skill" ? skills : agents;
  const items = catalog;

  const applyKind = (nextKind: StudioKind, nextAgents: StudioVersion[], nextSkills: StudioVersion[]) => {
    setCompareVersion("");
    setCompare(undefined);
    if (nextKind === "Workflow") {
      setSelected(undefined);
      setDocument(TEMPLATES.Workflow);
      return;
    }
    const list = nextKind === "Skill" ? nextSkills : nextAgents;
    if (!list.length) {
      setSelected(undefined);
      setDocument(TEMPLATES[nextKind]);
      return;
    }
    const current = list.find((item) => item.promoted) ?? list[0];
    setSelected(current);
    setDocument(toManifest(current));
  };

  const selectVersion = (
    versionId: string,
    nextKind: StudioKind,
    nextAgents: StudioVersion[],
    nextSkills: StudioVersion[],
  ) => {
    const list = nextKind === "Skill" ? nextSkills : nextAgents;
    const item = list.find((entry) => entry.version_id === versionId) ?? list[0];
    if (!item) return;
    setSelected(item);
    setDocument(toManifest(item));
    setCompareVersion("");
    setCompare(undefined);
  };

  const load = async () => {
    try {
      const next = await api.studio.catalog();
      setAgents(next.agents);
      setSkills(next.skills);
      setError("");
      return next;
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : "无法读取 Studio 目录");
      return undefined;
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void (async () => {
      const next = await load();
      if (next) applyKind("Agent", next.agents, next.skills);
    })();
  }, []);

  const run = async (action: "validate" | "publish" | "promote" | "rollback") => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (action === "validate") {
        setPreview(await api.studio.validate(document));
        setNotice("校验通过。密钥、DSN 和供应商模型 ID 会被拒绝。");
        return;
      }
      if (action === "publish") {
        const published =
          kind === "Skill" ? await api.studio.publishSkill(document) : await api.studio.publishAgent(document);
        setNotice(
          published.promoted
            ? `已发布 ${published.name} v${published.version}`
            : `已发布 ${published.name} v${published.version}（未提升，不会绑定新的对话）`,
        );
        const next = await load();
        if (next) selectVersion(published.version_id, kind, next.agents, next.skills);
        return;
      }
      if (!selected) return;
      if (action === "rollback") {
        const rolled = await api.studio.rollback({
          kind: selected.kind,
          name: selected.name,
          version: selected.version,
        });
        setNotice(`已回滚 ${rolled.name} 到 v${rolled.version}。未改写旧版本正文。`);
        const next = await load();
        if (next) selectVersion(rolled.version_id, kind, next.agents, next.skills);
        return;
      }
      const promoted = await api.studio.promote({
        kind: selected.kind,
        name: selected.name,
        version: selected.version,
      });
      setNotice(`已将 ${promoted.name} v${promoted.version} 设为当前运行版本`);
      const next = await load();
      if (next) selectVersion(promoted.version_id, kind, next.agents, next.skills);
    } catch (caught: unknown) {
      const message =
        caught instanceof ApiError ? caught.message : caught instanceof Error ? caught.message : "操作失败";
      setError(message);
      setPreview(undefined);
    } finally {
      setBusy(false);
    }
  };

  const runCompare = async () => {
    if (!selected || !compareVersion) return;
    setBusy(true);
    setError("");
    try {
      setCompare(
        await api.studio.compare({
          kind: selected.kind,
          name: selected.name,
          baseline_version: Number(compareVersion),
          candidate_version: selected.version,
        }),
      );
      setNotice("对比完成。运行时不会分流；评测请钉住两个版本的独立 Evaluation Run。");
    } catch (caught: unknown) {
      const message =
        caught instanceof ApiError ? caught.message : caught instanceof Error ? caught.message : "对比失败";
      setError(message);
      setCompare(undefined);
    } finally {
      setBusy(false);
    }
  };

  const changeKind = (nextKind: StudioKind) => {
    setKind(nextKind);
    setPreview(undefined);
    setError("");
    setNotice("");
    applyKind(nextKind, agents, skills);
  };

  const handleKindKeyDown = (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    const currentIndex = STUDIO_KINDS.indexOf(kind);
    let nextIndex: number | undefined;
    if (event.key === "ArrowRight") nextIndex = (currentIndex + 1) % STUDIO_KINDS.length;
    if (event.key === "ArrowLeft") {
      nextIndex = (currentIndex - 1 + STUDIO_KINDS.length) % STUDIO_KINDS.length;
    }
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = STUDIO_KINDS.length - 1;
    if (nextIndex === undefined) return;
    event.preventDefault();
    const nextKind = STUDIO_KINDS[nextIndex];
    changeKind(nextKind);
    event.currentTarget.parentElement
      ?.querySelector<HTMLButtonElement>(`[data-studio-kind="${nextKind}"]`)
      ?.focus();
  };

  return (
    <main className="feature-page studio-page">
      <header className="feature-header">
        <div>
          <span className="eyebrow">OBSION STUDIO</span>
          <h1>Agent / Skill 开发台</h1>
          <p>
            校验 YAML、发布不可变版本，再显式提升或回滚。对比两个版本不会分流线上流量。
            对话里仍然只有一个助手，这里不是 Agent 选择器。Workflow 可在此校验 DAG；版本发布仍走自动化。
          </p>
        </div>
        <span className="catalog-count">
          <PencilRuler size={17} /> {agents.length + skills.length} 个版本
        </span>
      </header>

      <div className="studio-layout">
        <aside className="studio-catalog">
          <div className="studio-kind-tabs" role="tablist" aria-label="Studio 清单类型">
            {STUDIO_KINDS.map((item) => (
              <button
                key={item}
                type="button"
                role="tab"
                id={`studio-kind-${item}`}
                aria-controls="studio-kind-panel"
                aria-selected={kind === item}
                tabIndex={kind === item ? 0 : -1}
                data-studio-kind={item}
                className={kind === item ? "active" : ""}
                onKeyDown={handleKindKeyDown}
                onClick={() => changeKind(item)}
              >
                {item}
              </button>
            ))}
          </div>
          {loading ? (
            <p className="studio-hint">正在加载目录…</p>
          ) : kind === "Workflow" ? (
            <p className="studio-hint">Workflow 在此只做 DAG 校验。发布与调度在「自动化」。</p>
          ) : items.length === 0 ? (
            <p className="studio-hint">还没有 {kind} 版本。</p>
          ) : (
            <div className="studio-item-list">
              {items.map((item) => (
                <button
                  key={item.version_id}
                  className={selected?.version_id === item.version_id ? "active" : ""}
                  onClick={() => {
                    setSelected(item);
                    setDocument(toManifest(item));
                    setPreview(undefined);
                    setCompareVersion("");
                    setCompare(undefined);
                    setError("");
                    setNotice("");
                  }}
                >
                  <strong>{item.name}</strong>
                  <small>
                    v{item.version} · {item.status}
                    {item.promoted ? " · 运行中" : " · 未提升"}
                  </small>
                </button>
              ))}
            </div>
          )}
        </aside>

        <section
          className="studio-editor"
          role="tabpanel"
          id="studio-kind-panel"
          aria-labelledby={`studio-kind-${kind}`}
        >
          <label>
            清单（YAML 或 JSON）
            <textarea
              value={document}
              onChange={(event) => setDocument(event.target.value)}
              spellCheck={false}
              aria-label="Agent、Skill 或 Workflow 清单"
            />
          </label>
          <div className="studio-actions">
            <button onClick={() => void run("validate")} disabled={busy || !document.trim()}>
              校验
            </button>
            {kind !== "Workflow" && (
              <button onClick={() => void run("publish")} disabled={busy || !document.trim()}>
                发布新版本
              </button>
            )}
            {kind !== "Workflow" && selected && (
              <button className="secondary-button" onClick={() => void run("promote")} disabled={busy}>
                设为运行版本
              </button>
            )}
            {kind !== "Workflow" && selected && !selected.promoted && (
              <button className="secondary-button" onClick={() => void run("rollback")} disabled={busy}>
                回滚到此版本
              </button>
            )}
          </div>
          {kind !== "Workflow" && selected && versionsOf(catalog, selected.name).length > 1 && (
            <div className="studio-actions">
              <label>
                对比基线
                <select
                  value={compareVersion}
                  onChange={(event) => setCompareVersion(event.target.value)}
                  disabled={busy}
                >
                  <option value="">选择另一版本</option>
                  {versionsOf(catalog, selected.name)
                    .filter((item) => item.version !== selected.version)
                    .map((item) => (
                      <option key={item.version_id} value={String(item.version)}>
                        v{item.version}
                        {item.promoted ? " · 运行中" : ""}
                      </option>
                    ))}
                </select>
              </label>
              <button className="secondary-button" onClick={() => void runCompare()} disabled={busy || !compareVersion}>
                对比版本
              </button>
            </div>
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
          {preview && (
            <article className="studio-preview">
              <header>
                <ShieldCheck size={16} />
                <strong>{preview.kind} · {preview.name}</strong>
                <code>{preview.checksum_sha256.slice(0, 12)}</code>
              </header>
              <pre>{JSON.stringify(preview.preview, null, 2)}</pre>
            </article>
          )}
          {compare && (
            <article className="studio-preview">
              <header>
                <ShieldCheck size={16} />
                <strong>
                  {compare.kind} · {compare.name} · v{compare.baseline.version} → v{compare.candidate.version}
                </strong>
                <code>{compare.identical ? "identical" : `${compare.changes.length} changes`}</code>
                <code>{compare.traffic_split ? "traffic split" : "no traffic split"}</code>
              </header>
              <p className="studio-hint">{compare.evaluation}</p>
              <pre>{JSON.stringify(compare.changes, null, 2)}</pre>
            </article>
          )}
        </section>
      </div>
    </main>
  );
}
