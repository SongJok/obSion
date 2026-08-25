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
  KeyRound,
  LockKeyhole,
  MessageSquareCode,
  RefreshCw,
  ShieldCheck,
  UserRoundCog,
  UsersRound,
  Workflow,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { api } from "@/lib/api";

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
}

const EMPTY: AdminData = {
  users: [], roles: [], departments: [], connectors: [], capabilities: [], profiles: [],
  agents: [], skills: [], dataSources: [], dataCatalog: {}, policies: [], approvals: [],
  evaluations: [], costs: [], prompts: [], knowledge: [], secrets: [], audit: [],
};

async function loadControlPlane(): Promise<AdminData> {
  const [
    users, roles, departments, connectors, capabilities, profiles, agents, skills,
    dataSources, dataCatalog, policies, approvals, evaluations, costs, prompts,
    knowledge, secrets, audit,
  ] = await Promise.all([
    api.admin.users(), api.admin.roles(), api.admin.departments(), api.admin.connectors(),
    api.admin.capabilities(), api.admin.modelProfiles(), api.admin.agents(), api.admin.skills(),
    api.admin.dataSources(), api.admin.dataCatalog(), api.admin.policies(), api.admin.approvals(),
    api.admin.evaluations(), api.admin.costs(), api.admin.prompts(), api.admin.knowledge(),
    api.admin.secrets(), api.admin.audit(),
  ]);
  return {
    users, roles, departments, connectors, capabilities, profiles, agents, skills,
    dataSources, dataCatalog, policies, approvals, evaluations, costs, prompts,
    knowledge, secrets, audit,
  };
}

export function AdminView() {
  const [data, setData] = useState<AdminData>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

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
      </div>

      <section className="governance-catalog" aria-label="治理目录">
        <Catalog icon={<UserRoundCog />} title="身份与角色" count={data.users.length + data.roles.length} detail={`${data.departments.length} 个部门`} />
        <Catalog icon={<Cpu />} title="模型档位" count={data.profiles.length} detail="供应商解耦路由" />
        <Catalog icon={<Bot />} title="Agents" count={data.agents.length} detail={`${data.skills.length} 个版本化 Skills`} />
        <Catalog icon={<Workflow />} title="能力与策略" count={data.capabilities.length} detail={`${data.policies.length} 条 deny 优先策略`} />
        <Catalog icon={<Cable />} title="连接器" count={data.connectors.length} detail="凭据仅在网关内解析" />
        <Catalog icon={<Database />} title="数据源" count={data.dataSources.length} detail={`${data.dataCatalog.metrics ?? 0} 个指标`} />
        <Catalog icon={<ShieldCheck />} title="审批" count={data.approvals.length} detail={`${pendingApprovals} 项等待处理`} />
        <Catalog icon={<FlaskConical />} title="评测" count={data.evaluations.length} detail="版本锁定发布门禁" />
        <Catalog icon={<MessageSquareCode />} title="Prompts" count={data.prompts.length} detail="不可变版本与校验和" />
        <Catalog icon={<FileSearch />} title="Knowledge" count={data.knowledge.length} detail="文档版本与 ACL 索引" />
        <Catalog icon={<KeyRound />} title="Secrets" count={data.secrets.length} detail="仅显示元数据" />
        <Catalog icon={<FileClock />} title="Audits" count={data.audit.length} detail="不可变操作轨迹" />
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
          <div className="control-list">
            {data.connectors.map((connector) => (
              <div key={String(connector.id)}><span className={`health-dot ${connector.status === "ACTIVE" ? "healthy" : ""}`} /><div><strong>{String(connector.name)}</strong><small>{String(connector.type)} · {String(connector.environment)}</small></div><em>{String(connector.status)}</em></div>
            ))}
            {!data.connectors.length && <p className="list-empty">暂无连接器</p>}
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

function Stat({ icon, label, value, sub }: { icon: React.ReactNode; label: string; value: number | string; sub: string }) {
  return <article><span className="stat-icon">{icon}</span><div><small>{label}</small><strong>{value}</strong><p>{sub}</p></div></article>;
}

function Catalog({ icon, title, count, detail }: { icon: React.ReactNode; title: string; count: number; detail: string }) {
  return <article><span>{icon}</span><div><strong>{title}</strong><small>{detail}</small></div><b>{count}</b></article>;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
