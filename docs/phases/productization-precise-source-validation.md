# 正文投影与精确引用验证

日期：2026-09-11。状态：VERIFIED_REPOSITORY_LOCAL。

依据 [ADR 0112](../adr/0112-precise-knowledge-source-projection.md)。先加入复现验证，原代码
得到 **3 failed、2 passed、25 deselected，6.85 秒**：两项缺少正文投影参数，一项真实 API/Harness
在答案仅引用报销提交制度正文时仍显示“其他归档说明”。后者是实际引用错误，不只是样式问题。

修复后，投影、原文复核、发布、知识路线、工具上下文与契约组合 **80 passed，46.36 秒**。
进一步覆盖正文位置与元数据映射、空白/无效条目、缺失片段编号、拒绝及空复核不得回退、
不可信标题纯文本渲染，投影专项 **4 passed，0.06 秒**。最终同一源码快照完整 Python 回归 **2382 passed、223 skipped、7 deselected，637.31 秒**；
显式基础设施跳过与真实租户排除项不记为通过。
Ruff/984 文件格式通过，Mypy 255 源文件通过。

共享正文枚举保持原有复核位置语义；作者上下文不会取得传输和检索元数据，原 Evidence 未被修改。
验收同时检查最终 Artifact、answer.delta 与引用元数据，不能只凭模型宣称“有来源”。
真实 Kimi 作者与独立复核经 ModelGateway 在隔离 PostgreSQL 完整执行，Run
`01a09052-93af-7519-abb0-2770c657c428` 检索两份合成制度，回答“先完成审批，再提交报销”，
只引用提交制度，未引用归档说明。正文及两次实际模型请求均不含作为来源元数据的测试标记，
完整 Artifact 来源记录仍保留该标记。两次调用均为 kimi-k3-kimi / SUCCESS，累计 5648 输入、
1615 输出 token；供应商耗时 30765 ms / 25928 ms，不能宣称已达到 30 秒目标。
[完整合成账本](../release/evidence/productization/20260911-precise-sources-kimi.json) 保存结果与定位摘要。

一次性库从空库升级至 d0e2f5a7b3c4，Alembic check 无差异，未修改业务文档。
本地镜像 `obsion-api:m1j-precise-sources-20260911` 的 230 个控制面 Python 源文件与仓库逐一一致，
已替换开发 API，健康检查通过；模型 ID 限制仍为用户指定的两个 Kimi 名称。回退版本为同样执行
模型限制的 `obsion-api:m1h-kimi-provenance-20260911`，禁止使用无模型限制的旧版本回退。
重新获取 GitHub main 后 HEAD 与 origin/main 无提交差异，工作树未提交、未推送。实际 README
部署后复查已完成：Run `01a0905d-0c23-7aa9-b474-10df966e9666` 的五项 Claim 均通过原文复核，
定义与设计定位没有被说成生产验收。两处引用对应同一 README 的不同正文片段，用户显示仍存在重复标题。
两次 K3 调用耗时 122270 / 102654 ms，累计约 225 秒，尚不满足 30 秒目标。
[部署后实际账本](../release/evidence/productization/20260911-precise-readme.json) 保留答案与复核位置。

无数据库迁移、旧事件改写或外部权限扩大。旧历史答案保留原样。当前只使用已授权 Kimi 模型；
完整 M1、开放域日常表达、自主项目及生产晋级仍未完成。
