"""使用隔离 SQLite 执行 Registry SQL；不替代 PostgreSQL 外键与迁移测试。"""

import asyncio
from uuid import uuid4

from obsion.config import Settings
from obsion.data.semantic.registry import MetricRegistry, SemanticRegistry
from obsion.db.models import Dimension, Metric, SemanticEntity
from obsion.db.session import Database
from obsion.security.identity import Principal


def test_metric_registry_tenant_versions_and_ambiguity(app_settings: Settings) -> None:
    async def scenario() -> None:
        database = Database(app_settings)
        organization_id = uuid4()
        principal = Principal(uuid4(), organization_id, "registry-test", "目录测试")

        def metric(
            name: str, version: int, *, validated: bool = True, foreign: bool = False
        ) -> Metric:
            return Metric(
                id=uuid4(),
                organization_id=uuid4() if foreign else organization_id,
                name=name,
                display_name=name,
                version=version,
                expression="COUNT(user_id)",
                filters={},
                time_column="created_at",
                source_table_id=uuid4(),
                owner="test",
                synonyms=["付费"],
                validated=validated,
            )

        old = metric("paid_count", 1)
        latest = metric("paid_count", 2)
        unvalidated = metric("paid_count", 3, validated=False)
        other_tenant = metric("paid_count", 9, foreign=True)
        ambiguous = metric("paid_total", 1)
        try:
            async with database.sessions() as session:
                session.add_all([old, latest, unvalidated, other_tenant, ambiguous])
                await session.commit()
                registry = MetricRegistry(session, principal)
                assert await registry.get_by_name("paid_count") is latest
                assert await registry.get(other_tenant.id) is None
                assert await registry.get(unvalidated.id) is None
                assert await registry.get(old.id) is old
                assert await registry.list_all() == [latest, ambiguous]
                assert await registry.list_all(limit=1) == [latest]
                assert await registry.list_all(limit=0) == []
                assert await registry.search(" PAID_COUNT ") == [latest]
                assert await registry.search("   ") == []
                assert await registry.search("付费", limit=0) == []
                semantic = SemanticRegistry(session, principal)
                assert await semantic.resolve_metric("paid_count") is latest
                assert await semantic.resolve_metric("付费") is None
                assert await semantic.resolve_metric("missing") is None

                # 将已有候选移到第二页，不能因首批截断而遗漏同义词。
                filler = [metric(f"a_{index:04d}", 1) for index in range(1000)]
                for row in filler:
                    row.synonyms = []
                filler[0].synonyms = ["跨页歧义"]
                latest.synonyms = ["付费", "跨页歧义", "仅末页命中"]
                session.add_all(filler)
                await session.commit()
                assert await registry.search("仅末页命中") == [latest]
                assert await registry.search("付费") == [latest, ambiguous]
                assert await semantic.resolve_metric("跨页歧义") is None
                assert await registry.search("完全不存在") == []

                dimensions = []
                entities = []
                for version in (1, 2, 9):
                    tenant = organization_id if version < 9 else uuid4()
                    common = {
                        "organization_id": tenant,
                        "name": "customer",
                        "display_name": "客户",
                        "version": version,
                        "source_table_id": uuid4(),
                        "owner": "test",
                    }
                    dimensions.append(Dimension(**common, expression="customer_id"))
                    entities.append(SemanticEntity(**common, primary_key_expression="customer_id"))
                session.add_all([*dimensions, *entities])
                await session.commit()
                assert await semantic.resolve_dimension("customer") is dimensions[1]
                assert await semantic.resolve_entity("customer") is entities[1]
                assert await semantic.resolve_dimension("missing") is None
                assert await semantic.resolve_entity("missing") is None
        finally:
            await database.dispose()

    asyncio.run(scenario())
