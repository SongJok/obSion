"""历史语义包必须复用受治理运行时，不能恢复原始 SQL 编译旁路。"""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from obsion.common.errors import ValidationError
from obsion.config import Settings
from obsion.data.semantic import DataUnderstandingEngine, SQLCompiler
from obsion.data.semantic.models import DataSource
from obsion.data.semantic.registry import SemanticRegistry
from obsion.data_intelligence.service import DataIntelligenceService
from obsion.db.models import LegacyEntityDefinition, LegacyQueryHistory, Metric
from obsion.security.identity import Principal


def test_compatibility_entrypoints_share_governed_implementation() -> None:
    assert SQLCompiler.compile is DataIntelligenceService.compile
    assert DataUnderstandingEngine.understand is DataIntelligenceService.understand
    assert not hasattr(SQLCompiler, "compile_metric_query")


def test_raw_table_plan_is_rejected_before_database_access() -> None:
    session = AsyncMock()
    principal = Principal(uuid4(), uuid4(), "test", "测试用户")
    compiler = SQLCompiler(Settings(_env_file=None))
    with pytest.raises(ValidationError, match="Logical query plan is invalid"):
        asyncio.run(
            compiler.compile(
                session,
                principal,
                {"select_columns": ["*"], "from_tables": ["private.payments"]},
            )
        )
    session.execute.assert_not_awaited()


def test_data_source_requires_connector_reference() -> None:
    assert "connection_string" not in DataSource.model_fields
    assert DataSource.model_fields["connector_id"].is_required()
    source = DataSource(
        organization_id=uuid4(), name="测试数据源", type="postgresql", connector_id=uuid4()
    )
    assert source.created_at.tzinfo is not None


def test_legacy_tables_remain_separate_from_runtime_catalog() -> None:
    assert LegacyEntityDefinition.__tablename__ == "entity_definitions"
    assert LegacyQueryHistory.__tablename__ == "query_history"
    assert "metadata" in LegacyEntityDefinition.__table__.columns
    assert "run_id" in LegacyQueryHistory.__table__.columns


@pytest.mark.parametrize("candidate_count", [0, 1, 2])
def test_ambiguous_metric_resolution_does_not_choose_first(candidate_count: int) -> None:
    principal = Principal(uuid4(), uuid4(), "test", "测试用户")
    registry = SemanticRegistry(AsyncMock(), principal)
    candidates = [Metric(id=uuid4(), name=f"metric_{i}") for i in range(candidate_count)]
    registry.metrics.get_by_name = AsyncMock(return_value=None)
    registry.metrics.search = AsyncMock(return_value=candidates)
    resolved = asyncio.run(registry.resolve_metric("付费"))
    assert resolved is (candidates[0] if candidate_count == 1 else None)
    registry.metrics.search.assert_awaited_once_with("付费", limit=2)
