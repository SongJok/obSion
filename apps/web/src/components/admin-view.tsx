"use client";

import {
  Activity,
  Bot,
  Boxes,
  Cable,
  CircleDollarSign,
  Cpu,
  Database,
  FileClock,
  FileSearch,
  FlaskConical,
  Gauge,
  KeyRound,
  LockKeyhole,
  MessageSquareCode,
  MessagesSquare,
  RefreshCw,
  Repeat2,
  ShieldCheck,
  ThumbsUp,
  Timer,
  UserRoundCog,
  UsersRound,
  Workflow,
} from "lucide-react";
import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { api } from "@/lib/api";
import type { FeedbackSummary, ImBinding, RuntimeSlo } from "@/lib/types";

type Items = Array<Record<string, unknown>>;

interface AdminData {
  users: Items;
  roles: Items;
  departments: Items;
  connectors: Items;
  capabilities: Items;
  profiles: Items;
  agents: Items;
  skills: Items;
  dataSources: Items;
  dataCatalog: Record<string, number>;
  policies: Items;
  approvals: Items;
  evaluations: Items;
  costs: Items;
  prompts: Items;
  knowledge: Items;
  secrets: Items;
  audit: Items;
  operatorInvocations: Items;
  imBindings: ImBinding[];
  feedback: FeedbackSummary;
  slo: RuntimeSlo;
}

const EMPTY: AdminData = {
  users: [], roles: [], departments: [], connectors: [], capabilities: [], profiles: [],
  agents: [], skills: [], dataSources: [], dataCatalog: {}, policies: [], approvals: [],
  evaluations: [], costs: [], prompts: [], knowledge: [],   secrets: [], audit: [],
  operatorInvocations: [], imBindings: [],
  feedback: { total: 0, helpful: 0, needs_improvement: 0, helpful_rate: null },
  slo: {
    source: "postgresql",
    runs: { terminal: 0, completed: 0, failed: 0, cancelled: 0, success_rate: null },
    latency: {
      average_ms: null,
      count: 0,
      ttft: { available: false, metric: "obsion.run.ttft", reason: "histogram-only" },
      model: { average_ms: null, count: 0 },
      tool: { average_ms: null, count: 0, source: "capability-steps" },
    },
    steps: { average: null, count: 0 },
    tokens: { input: 0, output: 0 },
    cost: { amount: "0" },
    replans: { events: 0, rate: null },
    approvals: { requested: 0, approved: 0, rejected: 0, pending: 0, approval_rate: null },
    satisfaction: { total: 0, helpful: 0, needs_improvement: 0, helpful_rate: null },
    evidence_coverage: { average: null, count: 0 },
  },
};

async function loadControlPlane(): Promise<AdminData> {
  const [
    users, roles, departments, connectors, capabilities, profiles, agents, skills,
    dataSources, dataCatalog, policies, approvals, evaluations, costs, prompts,
    knowledge, secrets, audit, operatorInvocations, imBindings, feedback, slo,
  ] = await Promise.all([
    api.admin.users(), api.admin.roles(), api.admin.departments(), api.admin.connectors(),
    api.admin.capabilities(), api.admin.modelProfiles(), api.admin.agents(), api.admin.skills(),
    api.admin.dataSources(), api.admin.dataCatalog(), api.admin.policies(), api.admin.approvals(),
    api.admin.evaluations(), api.admin.costs(), api.admin.prompts(), api.admin.knowledge(),
    api.admin.secrets(), api.admin.audit(), api.admin.operatorInvocations(),
    api.admin.imBindings(), api.admin.feedbackSummary(), api.admin.runtimeSlo(),
  ]);
  return {
    users, roles, departments, connectors, capabilities, profiles, agents, skills,
    dataSources, dataCatalog, policies, approvals, evaluations, costs, prompts,
    knowledge, secrets, audit, operatorInvocations, imBindings, feedback, slo,
  };
}

export function AdminView() {
  const [data, setData] = useState<AdminData>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [channel, setChannel] = useState("development");
  const [senderId, setSenderId] = useState("");
  const [userId, setUserId] = useState("");
  const [discoveries, setDiscoveries] = useState<Record<string, Items>>({});

  const usersById = useMemo(
    () => Object.fromEntries(data.users.map((user) => [String(user.id), user])),
    [data.users],
  );
  const activeBindings = data.imBindings.filter((item) => item.active);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setData(await loadControlPlane());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法读取治理状态");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    loadControlPlane()
      .then((next) => { if (active) setData(next); })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof Error ? caught.message : "无法读取治理状态");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const totalCost = data.costs.reduce((sum, item) => sum + Number(item.cost_amount ?? 0), 0);
  const pendingApprovals = data.approvals.filter((item) => item.status === "PENDING").length;
  const semanticObjects = Object.values(data.dataCatalog).reduce((sum, count) => sum + count, 0);
  const helpfulRate = formatRate(data.feedback.helpful_rate);
  const successRate = formatRate(data.slo.runs.success_rate);
  const replanRate = formatRate(data.slo.replans.rate);
  const approvalRate = formatRate(data.slo.approvals.approval_rate);
  const coverage = data.slo.evidence_coverage.average === null
    ? "—"
    : data.slo.evidence_coverage.average.toFixed(2);
  const averageLatency = data.slo.latency.average_ms === null
    ? "—"
    : `${Math.round(data.slo.latency.average_ms)}ms`;

  async function bindSender(event: FormEvent) {
    event.preventDefault();
    if (!senderId.trim() || !userId) return;
    setSaving(true);
    setError("");
    try {
      await api.admin.createImBinding({
        channel,
        sender_id: senderId.trim(),
        user_id: userId,
      });
      setSenderId("");
      setData(await loadControlPlane());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法创建 IM 绑定");
    } finally {
      setSaving(false);
    }
  }

  async function revokeBinding(bindingId: string) {
    setSaving(true);
    setError("");
    try {
      await api.admin.revokeImBinding(bindingId);
      setData(await loadControlPlane());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法撤销 IM 绑定");
    } finally {
      setSaving(false);
    }
  }

  async function probeConnector(connectorId: string) {
    setSaving(true);
    setError("");
    try {
      await api.admin.probeConnectorHealth(connectorId);
      setData(await loadControlPlane());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法探测连接器健康");
    } finally {
      setSaving(false);
    }
  }

  async function discoverConnector(connectorId: string) {
    setSaving(true);
    setError("");
    try {
      const result = await api.admin.discoverConnector(connectorId);
      const discovery = asRecord(result.discovery);
      const operations = Array.isArray(discovery.operations) ? discovery.operations : [];
      setDiscoveries((current) => ({
        ...current,
        [connectorId]: operations.filter((item): item is Record<string, unknown> => (
          typeof item === "object" && item !== null
        )),
      }));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法发现连接器操作");
    } finally {
      setSaving(false);
    }
  }

  async function scanConnector(connectorId: string) {
    setSaving(true);
    setError("");
    try {
      await api.admin.scanConnectorPlugin(connectorId);
      setData(await loadControlPlane());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法扫描连接器插件");
    } finally {
      setSaving(false);
    }
  }

  async function promoteConnector(connectorId: string) {
    setSaving(true);
    setError("");
    try {
      await api.admin.promoteConnectorPlugin(connectorId);
      setData(await loadControlPlane());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法晋升连接器插件");
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="feature-page admin-page">
      <header className="feature-header">
        <div><span className="eyebrow">OBSION CONTROL</span><h1>治理控制台</h1><p>身份、模型、能力、数据、策略、审批、评测与审计统一进入企业控制面。</p></div>
        <button className="secondary-button" onClick={() => void load()} disabled={loading}><RefreshCw size={16} className={loading ? "spin" : ""} /> 刷新</button>
      </header>
      {error && <div className="notice error"><LockKeyhole size={17} />{error}</div>}

      <div className="admin-stats">
        <Stat icon={<UsersRound />} label="活跃用户" value={data.users.filter((item) => item.active).length} sub={`${data.roles.length} 个角色 · ${data.departments.length} 个部门`} />
        <Stat icon={<Boxes />} label="能力版本" value={data.capabilities.length} sub={`${data.connectors.filter((item) => item.status === "ACTIVE").length} 个连接器已启用`} />
        <Stat icon={<Database />} label="语义对象" value={semanticObjects} sub={`${data.dataSources.length} 个只读数据源`} />
        <Stat icon={<CircleDollarSign />} label="模型成本" value={totalCost.toFixed(4)} sub={`${pendingApprovals} 项审批待处理`} />
        <Stat icon={<ThumbsUp />} label="用户满意度" value={helpfulRate} sub={`${data.feedback.total} 份反馈 · ${data.feedback.needs_improvement} 份待改进`} />
      </div>

      <div className="admin-stats" aria-label="运行 SLO 投影">
        <Stat icon={<Gauge />} label="运行成功率" value={successRate} sub={`${data.slo.runs.completed} 完成 · ${data.slo.runs.failed} 失败 · PostgreSQL`} />
        <Stat icon={<Repeat2 />} label="再规划率" value={replanRate} sub={`${data.slo.replans.events} 次 plan.updated`} />
        <Stat icon={<ShieldCheck />} label="审批通过率" value={approvalRate} sub={`${data.slo.approvals.approved} 通过 · ${data.slo.approvals.pending} 待处理`} />
        <Stat icon={<FileSearch />} label="证据覆盖" value={coverage} sub={`${data.slo.evidence_coverage.count} 份评估`} />
        <Stat icon={<Timer />} label="平均总延迟" value={averageLatency} sub="TTFT 仅 OTel histogram，不是 p95 SLA" />
      </div>
      <p className="slo-note">核心指标来自当前租户 PostgreSQL 投影，不是 OTel histogram 的 p95，也不是签署 SLA。</p>

      <section className="governance-catalog" aria-label="治理目录">
        <Catalog icon={<UserRoundCog />} title="身份与角色" count={data.users.length + data.roles.length} detail={`${data.departments.length} 个部门`} />
        <Catalog icon={<Cpu />} title="模型档位" count={data.profiles.length} detail="供应商解耦路由" />
        <Catalog icon={<Bot />} title="Agents" count={data.agents.length} detail={`${data.skills.length} 个版本化 Skills · 沙箱网络仅经 Gateway`} />
        <Catalog icon={<Workflow />} title="能力与策略" count={data.capabilities.length} detail={`${data.policies.length} 条 deny 优先策略`} />
        <Catalog icon={<Cable />} title="连接器" count={data.connectors.length} detail="凭据仅在网关内解析；MCP/SDK/gRPC/Workflow/Agent 为进程内适配器，Connector SDK 插件需扫描/签名后晋升" />
        <Catalog icon={<Database />} title="数据源" count={data.dataSources.length} detail={`${data.dataCatalog.metrics ?? 0} 个指标`} />
        <Catalog icon={<ShieldCheck />} title="审批" count={data.approvals.length} detail={`${pendingApprovals} 项等待处理`} />
        <Catalog icon={<FlaskConical />} title="评测" count={data.evaluations.length} detail="版本锁定发布门禁" />
        <Catalog icon={<MessageSquareCode />} title="Prompts" count={data.prompts.length} detail="不可变版本与校验和" />
        <Catalog icon={<FileSearch />} title="Knowledge" count={data.knowledge.length} detail="文档版本与 ACL 索引 · 飞书/钉钉/企微/Confluence 经 feishu-docs / dingtalk-docs / wecom-docs / confluence 连接器" />
        <Catalog icon={<KeyRound />} title="Secrets" count={data.secrets.length} detail="仅显示元数据" />
        <Catalog icon={<FileClock />} title="Audits" count={data.audit.length} detail="不可变操作轨迹" />
        <Catalog icon={<MessagesSquare />} title="IM 绑定" count={activeBindings.length} detail="昵称不能授权" />
      </section>

      <section className="control-card im-bindings-card" aria-label="IM 主体绑定">
        <header>
          <div><MessagesSquare size={17} /><strong>IM 主体绑定</strong></div>
          <span>{activeBindings.length}</span>
        </header>
        <p className="im-binding-note">
          只绑定厂商稳定 sender_id（飞书 open_id、钉钉 senderStaffId、企微 FromUserName）。昵称、display_name 与 sender_display 不能授权。未映射发送者 fail-closed。默认出站仍是本地 outbox。飞书可用 `--deliver feishu-http`（`open.feishu.cn`），钉钉可用 `--deliver dingtalk-http`（`oapi.dingtalk.com`），企微可用 `--deliver wecom-http`（`qyapi.weixin.qq.com`）。凭据只来自对应 `OBSION_FEISHU_*` / `OBSION_DINGTALK_*` / `OBSION_WECOM_*` 环境变量，并经控制面 `im.reply.deliver` 授权后才 POST。入站官方校验使用 `X-Lark-Signature` 与 `OBSION_FEISHU_ENCRYPT_KEY`；密文事件按飞书 AES-256-CBC 解密。企微 `Encrypt` 在配置 `OBSION_WECOM_ENCODING_AES_KEY` 后按 EncodingAESKey 解密；未配置则 fail-closed。`--deliver http` 仍 fail-closed。本地回调用 `obsion-im serve --listen 127.0.0.1:8787`。公网 `--public` 需 TLS、Host allowlist，以及飞书 Encrypt Key / 企微 EncodingAESKey+Token / 钉钉签名密钥。
        </p>
        <form className="im-binding-form" onSubmit={(event) => void bindSender(event)}>
          <label>
            通道
            <select value={channel} onChange={(event) => setChannel(event.target.value)} disabled={saving}>
              <option value="development">development</option>
              <option value="feishu">feishu</option>
              <option value="dingtalk">dingtalk</option>
              <option value="wecom">wecom</option>
            </select>
          </label>
          <label>
            sender_id
            <input
              value={senderId}
              onChange={(event) => setSenderId(event.target.value)}
              placeholder="ou_xxx / staffId / FromUserName"
              disabled={saving}
              required
            />
          </label>
          <label>
            用户
            <select value={userId} onChange={(event) => setUserId(event.target.value)} disabled={saving} required>
              <option value="">选择用户</option>
              {data.users.filter((item) => item.active).map((user) => (
                <option key={String(user.id)} value={String(user.id)}>
                  {String(user.display_name)} · {String(user.email)}
                </option>
              ))}
            </select>
          </label>
          <button className="primary-button" type="submit" disabled={saving || !senderId.trim() || !userId}>
            绑定
          </button>
        </form>
        <div className="control-list im-binding-list">
          {activeBindings.map((binding) => {
            const bound = usersById[binding.user_id];
            return (
              <div key={binding.id}>
                <span className="health-dot healthy" />
                <div>
                  <strong>{binding.channel}:{binding.sender_id}</strong>
                  <small>
                    {bound ? String(bound.display_name) : binding.user_id}
                    {" · "}
                    昵称不能授权
                  </small>
                </div>
                <button
                  className="ghost-button"
                  type="button"
                  disabled={saving}
                  onClick={() => void revokeBinding(binding.id)}
                >
                  撤销
                </button>
              </div>
            );
          })}
          {!activeBindings.length && <p className="list-empty">尚未绑定 IM 发送者</p>}
        </div>
      </section>

      <section className="control-card evaluation-gates" aria-label="评测发布门禁">
        <header><div><FlaskConical size={17} /><strong>评测发布门禁</strong></div><span>{data.evaluations.length}</span></header>
        <div className="evaluation-gate-list">
          {data.evaluations.slice(0, 8).map((evaluation) => {
            const metrics = asRecord(evaluation.metrics);
            const baseline = asRecord(metrics.baseline);
            const regressions = Array.isArray(baseline.regressions) ? baseline.regressions.length : 0;
            const passed = evaluation.gate_passed === true;
            return (
              <article key={String(evaluation.id)}>
                <span className={`evaluation-gate-status ${passed ? "passed" : "failed"}`}>{passed ? "通过" : "阻断"}</span>
                <div><strong>{String(evaluation.application_revision)}</strong><small>{Number(metrics.passed ?? 0)}/{Number(metrics.total ?? 0)} 案例通过 · 回归 {regressions}</small></div>
                <code title={String(evaluation.snapshot_sha256)}>{String(evaluation.snapshot_sha256).slice(0, 10)}</code>
              </article>
            );
          })}
          {!data.evaluations.length && <p className="list-empty">尚未运行版本固定评测</p>}
        </div>
      </section>

      <div className="admin-columns">
        <section className="control-card">
          <header><div><Activity size={17} /><strong>连接器健康</strong></div><span>{data.connectors.length}</span></header>
          <p className="im-binding-note">
            MCP、SDK、gRPC、Workflow 与 Agent 只走 Capability Gateway 的进程内适配器。Connector SDK 是作者 SPI（health / discover / execute），不是包安装器。插件生命周期为开发 → 静态扫描 → HMAC 签名 → 注册 → 审批 → 生产；扫描不读二进制、不 pip install。L5 永远拒绝。发现结果不会自动绑定 Capability。
          </p>
          <div className="control-list connector-health-list">
            {data.connectors.map((connector) => {
              const connectorId = String(connector.id);
              const health = asRecord(connector.health);
              const healthStatus = String(health.status ?? "unknown");
              const plugin = asRecord(connector.plugin);
              const pluginStatus = String(plugin.status ?? "unknown");
              const lifecycle = String(plugin.lifecycle ?? "");
              const operations = discoveries[connectorId] ?? [];
              return (
                <div key={connectorId} className="connector-health-row">
                  <span className={`health-dot ${healthStatus === "ready" ? "healthy" : ""}`} />
                  <div>
                    <strong>{String(connector.name)}</strong>
                    <small>
                      {String(connector.type)} · {String(connector.environment)} · {healthStatus}
                      {connector.spi === true && lifecycle ? ` · 插件 ${pluginStatus}/${lifecycle}` : ""}
                    </small>
                    {operations.length > 0 && (
                      <ul className="connector-discovery">
                        {operations.map((operation) => (
                          <li key={String(operation.capability ?? operation.name)}>
                            {String(operation.capability ?? operation.name)}
                            {" · "}
                            发现不自动绑定
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                  <div className="connector-actions">
                    <em>{String(connector.status)}</em>
                    {connector.spi === true && (
                      <>
                        <button
                          className="ghost-button"
                          type="button"
                          disabled={saving}
                          onClick={() => void probeConnector(connectorId)}
                        >
                          探测
                        </button>
                        <button
                          className="ghost-button"
                          type="button"
                          disabled={saving}
                          onClick={() => void discoverConnector(connectorId)}
                        >
                          发现
                        </button>
                        <button
                          className="ghost-button"
                          type="button"
                          disabled={saving}
                          onClick={() => void scanConnector(connectorId)}
                        >
                          扫描
                        </button>
                        <button
                          className="ghost-button"
                          type="button"
                          disabled={saving}
                          onClick={() => void promoteConnector(connectorId)}
                        >
                          晋升
                        </button>
                      </>
                    )}
                  </div>
                </div>
              );
            })}
            {!data.connectors.length && <p className="list-empty">暂无连接器</p>}
          </div>
        </section>
        <section className="control-card audit-card">
          <header><div><Repeat2 size={17} /><strong>Operator Capability 账本</strong></div><span>{data.operatorInvocations.length}</span></header>
          <div className="control-list">
            {data.operatorInvocations.slice(0, 8).map((record) => (
              <div key={String(record.id)}><span className="audit-icon"><ShieldCheck size={13} /></span><div><strong>{String(record.capability_name)}</strong><small>{String(record.request_id)} · {new Date(String(record.created_at)).toLocaleTimeString("zh-CN")}</small></div><em>{record.reconciliation_required ? "需人工核对" : String(record.status)}</em></div>
            ))}
            {!data.operatorInvocations.length && <p className="list-empty">暂无 Operator Capability 调用</p>}
          </div>
        </section>
        <section className="control-card audit-card">
          <header><div><FileClock size={17} /><strong>最近审计</strong></div><span>{data.audit.length}</span></header>
          <div className="control-list">
            {data.audit.slice(0, 8).map((record) => (
              <div key={String(record.id)}><span className="audit-icon"><LockKeyhole size={13} /></span><div><strong>{String(record.action)}</strong><small>{String(record.resource_type)} · {new Date(String(record.created_at)).toLocaleTimeString("zh-CN")}</small></div><em>{String(record.outcome)}</em></div>
            ))}
            {!data.audit.length && <p className="list-empty">暂无审计记录</p>}
          </div>
        </section>
      </div>
    </main>
  );
}

function formatRate(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

function Stat({ icon, label, value, sub }: { icon: React.ReactNode; label: string; value: number | string; sub: string }) {
  return <article><span className="stat-icon">{icon}</span><div><small>{label}</small><strong>{value}</strong><p>{sub}</p></div></article>;
}

function Catalog({ icon, title, count, detail }: { icon: React.ReactNode; title: string; count: number; detail: string }) {
  return <article><span>{icon}</span><div><strong>{title}</strong><small>{detail}</small></div><b>{count}</b></article>;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
