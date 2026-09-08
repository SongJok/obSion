"""历史导入路径的兼容入口；SQL 编译只使用受治理的数据智能服务。

编译需要数据库会话、Principal 及引用已注册指标 ID 的逻辑计划，不再接受
客户端提交的表名、表达式或连接字符串。执行仍由 Capability Gateway 负责。
"""

from obsion.data_intelligence.service import DataIntelligenceService


class SQLCompiler(DataIntelligenceService):
    """保留类名，复用唯一的目录解析、参数绑定和 SQL Policy 实现。"""
