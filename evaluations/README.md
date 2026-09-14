# Obsion Golden Datasets

Files in `datasets/` are version-controlled release contracts. Every case must declare
`ROUTING`, `SQL_POLICY`, or `RUN_OUTPUT`; case revisions are immutable after ingestion.

`RUN_OUTPUT` cases use a stable `run_ref`. A regression job executes the candidate
Agent through the ordinary governed API and binds that name to the resulting terminal
Run ID when it starts the evaluation. See `examples/run-output-case.json` and the
[evaluation architecture](../docs/architecture/evaluation-design.md).

Validate all committed datasets before opening a pull request:

```bash
uv run obsion validate-evaluations
uv run obsion validate-eval-gates
uv run obsion evaluate-datasets
```

The `v1-knowledge-qa` dataset contains 20 KnowledgeAgent cases, including explicit
user, role, and department denial cases that require zero recall and an unknown answer.
The routing and safety dataset also includes a metric-decline case that locks the
DataAgent route and root-cause classification before execution. Agent-quality
RUN_OUTPUT contracts cover Knowledge, Data, Incident, Engineering, Support,
Operation, and Analytics; CI binds `run_ref` names to real terminal Runs.

Incident RUN_OUTPUT cases may additionally assert `minimum_incident_candidates`,
`minimum_cross_type_claims`, and `incident_top1_evidence_types`; these checks keep the
Top1/Top3 candidate and two-Evidence-type Claim contract in regression tests.

## Offline completeness

`evaluate-datasets` retains the legacy `status` for its explicitly named
`OFFLINE_ROUTING_AND_SQL_POLICY` subset. It now reports every unexecuted final-answer
case as `NOT_RUN`, sets `acceptance_status=BLOCKED`, and never claims answer quality
through `quality_eligible`. For a completeness gate, run
`uv run obsion evaluate-datasets --require-complete`; required missing Run outputs
produce JSON evidence and exit code 2. The default offline command does not certify
final answers or production readiness. See [ADR0131](../docs/adr/0131-p1-context-and-honest-quality-baseline.md).
# 产品化 P1 验收增量

`obsion acceptance freeze/run` 会冻结任务、配置和资料摘要，再从正常任务入口读取最终答案。
当前 CLI 独立语义评分仍为 NOT_RUN，完整阶段状态保持 BLOCKED；不能用开发测试或内部
VERIFIED 推进阶段。配置、权限、报告和退出码见
[操作说明](../docs/operators/productization-acceptance.md)。

真实企业保留集、来源引文和租户清单是操作者私有资料，不随公开 GitHub 仓库分发；
固定试点文件已加入忽略规则，原本地文件保留。运行真实验收前须在授权环境提供经过审核的
任务集和摘要。公开源码测试使用显式合成数据，不能替代真实企业答案质量成绩。
