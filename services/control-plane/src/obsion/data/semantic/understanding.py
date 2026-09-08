"""查询理解的兼容入口，使用同一组织目录和治理后的指标同义词。"""

from obsion.data_intelligence.service import DataIntelligenceService


class DataUnderstandingEngine(DataIntelligenceService):
    """通过 understand(session, principal, question) 解析真实目录。"""
