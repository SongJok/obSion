# 完整依赖安全扫描与运行包验证

日期：2026-09-12。依据[ADR0130](../adr/0130-complete-dependency-security-scan.md)。

GitHub CI首次安全扫描引用解析问题已修复，但第二次运行34695599574的安全扫描仍失败。
同一已提交源码的本地Trivy扫描发现Next.js和sharp漏洞；扫描器同时报告无法解析多根uv
工作区锁文件。新增显式完整Python CycloneDX SBOM扫描后，发现此前漏检的两项pypdf高危
漏洞。npm audit另发现js-yaml开发依赖高危漏洞。没有将解析失败当成安全通过。

修复版本为Next.js/eslint-config-next 16.3.3、sharp 0.35.4、pypdf 6.14.2、js-yaml 4.3.2。
Python其余锁定版本不变。现有HIGH/CRITICAL、ignore-unfixed=false、失败退出规则保留；
CI新增SBOM扫描并检查生产步骤与消费路径一致。

已完成验证：

- 官方Trivy 0.70.0可执行文件下载摘要通过校验；官方GHCR数据库层完整SHA256校验通过。
- 修复后的干净源码文件系统扫描、完整Python SBOM扫描均为0项HIGH/CRITICAL；npm ci审计0项漏洞。
- 前端222项与其他JavaScript55项通过，lint、typecheck、build通过。
- CI契约4项通过；278源码Mypy、Ruff、格式和项目秘密扫描通过。
- 完整PostgreSQL集成405通过、10跳过（161.11秒），迁移差异检查通过，独立测试库已删除。
- IM历史迁移专项1项通过（7.53秒）；与前述集成不同，该项显式验证破坏性往返并使用独立空库。
- 日常真实数据库备份在独立克隆中恢复、升级新head并检查结构通过；94条Run、8篇Document、11个版本、23条DingTalk Outbox及来源/绑定记录数量保持。普通ImDelivery表当前0行，不能把本次日常克隆称为修复了真实旧UNKNOWN数据；该修复由独立历史夹具覆盖。
- 新API与Web镜像已构建；包版本与源码一致性结果记录在本地验收账本。日常实例已更新至两个修复镜像，新head为b4c6d8e0f2a4，6篇授权文档可用且标识保留。

最终完整Python回归2651通过、262跳过、7排除（839.67秒）；远端CI需在提交后重验，尚未计为通过。原生文档后续
权限检查、其余格式、MCP调查及完整产品化仍保持开放。
