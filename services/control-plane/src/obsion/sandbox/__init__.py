"""独立、显式启用的 Kubernetes 沙箱后端，尚未接入 Harness。

本包不发现凭据、不为 Agent 授权，也不把 Kubernetes 规范等同于真实隔离。
启用前须阅读 ADR 0084；生产环境在本切片中保持关闭。
"""
