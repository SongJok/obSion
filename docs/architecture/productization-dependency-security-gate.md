# 完整依赖安全扫描架构门禁

日期：2026-09-12。状态：本地整合及运行环境晋级通过，远端CI待重验。

[ADR0130](../adr/0130-complete-dependency-security-scan.md)补全多包Python工作区的依赖扫描，
沿用已生成的完整CycloneDX清单。无法解析uv锁文件不再被等同于Python依赖检查通过。
新增SBOM步骤保留HIGH/CRITICAL和失败退出，原文件系统及容器扫描仍执行。

安全修复更新Next.js、sharp、pypdf与js-yaml，不改变模型白名单、文档授权或公共接口。
pypdf跨依赖主版本但必须通过既有解析与完整回归；旧证据不重写。Docker运行包重新构建
并检验实际版本，禁止仅改锁文件就宣称运行环境已修复。

文件系统、完整Python清单及npm审计已通过；前端277项检查、405项PostgreSQL集成、
独立迁移往返及真实备份克隆升级通过。完整Python2651通过/262跳过/7排除（839.67秒）；
日常修复镜像及新迁移已生效，6篇资料可用；远端CI仍待完成。
详见[验证报告](../phases/productization-dependency-security-validation.md)。本项不等同于
生产就绪或完整自主企业能力验收。
