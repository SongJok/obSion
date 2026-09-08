# ADR 0094：通过现有能力网关接入云效 Codeup 只读协议

日期：2026-09-06。状态：Accepted for implementation；真实租户验收待完成。

## 问题

`goal.txt` 和本轮产品化目标要求真实云效连接。现有工程元数据代理契约和环境变量占位不包含原生 Codeup 协议。
M2 的来源授权、Git 校验与不可变账本也不能自行获取厂商数据。需要从可验证的授权查询入口连接现有控制面。

## 决策

1. 在单 Python 控制面新增 `codeup.read.v1`，通过既有 HTTP Executor 分派。固定 Central HTTPS origin、四个 GET 路由，禁止重定向和任意请求头/URL/操作扩展。
2. 四个版本化能力为 `codeup.repository.get`、`codeup.commit.get`、`codeup.commits.list`、`codeup.file.read`。均为 L2、`SideEffect.NONE`、`code.read`、RESTRICTED CODE，输入与输出字段封闭。没有自动 Connector、Binding 或 Agent grant。
3. 连接器声明组织 ID 和本地仓库名称 → 厂商数字 ID/本地 UUID 的封闭映射。Gateway 先记录 Policy，再校验当前用户/组织、仓库 ACL、精确 grants、输入和预算，最后解析服务端凭据。
4. 读取前后均查询当前主体、仓库 ACL 和连接配置。后置读取不 refresh 旧 ORM 对象，而是比较数据库列与发起时对象，避免变化后的映射覆盖原调用边界。配置变化、撤权或用户失效则拒绝交付。
5. 新增同源 REST POST 查询入口和工作台“云效项目查询”。无 Run 的直接查询复用 operator Gateway 事务、Policy/Audit；失败审计提交后才映射 HTTP。Run 路径可产生带 RESTRICTED 分类的 CODE Evidence。两者共享原生适配器和授权守卫。
6. 文件绑定完整 SHA、规范相对路径、大小、编码和 Git blob hash。厂商身份字段逐项检查，删除作者邮箱等未使用数据；内容与凭据反射在进入结果前脱敏。记录原 blob 与脱敏后内容 hash 的不同含义。
7. 列表有显式分页预算；满页不推断完整，最后允许页不生成不可请求的 next page。UI 取消等待、切换选择和迟到响应隔离，不将旧结果误标为新项目内容。

## 兼容、迁移与边界

无表结构或历史数据变更，无新 Alembic。能力注册在现有 seed/version 机制中新增四条未绑定契约；原接口与已有 Agent grants 不变。
增加 8 个稳定错误码，精确错误 producer 清单仅增加新来源/调整网关新增分支和转发位置，保持已有静态门禁；OpenAPI 添加一条路径和类型模型。

这不是完整项目来源获取：不产生原始 commit/tree 对象、来源账本 pin、沙箱 checkout、项目传输或源码 Artifact。单文件 hash 不证明全仓库内容或应用实际 scopes。
运行中后置检查覆盖当前身份/仓库 ACL 与连接配置；没有声称对整个 Policy/Binding 图的并发变更提供交付时线性化保证，也没有通用上下文/Artifact 撤权传播。
真实访问需要实际只读 PAT，不能把已有 APP_ID 当令牌或把配置存在当联通证据。Central 之外的 Region 与其他鉴权机制需要独立契约。

## 验证

Codeup 专项 71 项覆盖四种真实请求形状、严格 origin/路径/参数、文件篡改/体积/压缩扩张/脱敏、分页预算、本地角色/租户/仓库 ACL、
缺失配置、厂商拒绝审计、Run Evidence 和读取期间撤权/连接变化。厂商由明确 MockTransport 模拟；数据库使用现有 SQLite 测试控制面，
不声称已验证 PostgreSQL 跨连接并发或真实云效。

Web 新增 6 项交互测试和 1 项真实 API 封装测试；完整前端工作区验证及 Python/门禁结果见
[Codeup 验证记录](../phases/productization-codeup-validation.md)。M2 架构门和正式 Phase 99 晋级仍保持未通过。
