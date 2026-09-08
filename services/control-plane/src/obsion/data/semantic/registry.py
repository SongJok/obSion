"""受治理目录的只读兼容查询；写入继续使用现有 Data Catalog API。"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.db.models import Dimension, Metric, SemanticEntity
from obsion.security.identity import Principal


class MetricRegistry:
    """只返回当前租户内经过验证的指标，稳定选择最新版本。"""

    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.session = session
        self.principal = principal

    async def list_all(self, *, limit: int = 100) -> list[Metric]:
        return await self._latest_page(limit=limit)

    async def _latest_page(self, *, limit: int, after_name: str | None = None) -> list[Metric]:
        bounded_limit = max(0, min(limit, 1000))
        if bounded_limit == 0:
            return []
        ranked = (
            select(
                Metric.id,
                func.row_number()
                .over(partition_by=Metric.name, order_by=(Metric.version.desc(), Metric.id))
                .label("version_rank"),
            )
            .where(
                Metric.organization_id == self.principal.organization_id,
                Metric.validated.is_(True),
            )
            .subquery()
        )
        statement = (
            select(Metric)
            .join(ranked, Metric.id == ranked.c.id)
            .where(ranked.c.version_rank == 1)
            .order_by(Metric.name, Metric.id)
            .limit(bounded_limit)
        )
        if after_name is not None:
            statement = statement.where(Metric.name > after_name)
        rows = await self.session.scalars(statement)
        return list(rows)

    async def get(self, metric_id: UUID) -> Metric | None:
        result = await self.session.scalars(
            select(Metric).where(
                Metric.organization_id == self.principal.organization_id,
                Metric.id == metric_id,
                Metric.validated.is_(True),
            )
        )
        return result.first()

    async def get_by_name(self, name: str) -> Metric | None:
        result = await self.session.scalars(
            select(Metric)
            .where(
                Metric.organization_id == self.principal.organization_id,
                Metric.name == name,
                Metric.validated.is_(True),
            )
            .order_by(Metric.version.desc(), Metric.id)
            .limit(1)
        )
        return result.first()

    async def search(self, query: str, *, limit: int = 10) -> list[Metric]:
        normalized = query.strip().casefold()
        bounded_limit = max(0, min(limit, 1000))
        if not normalized or bounded_limit == 0:
            return []
        matches: list[Metric] = []
        after_name: str | None = None
        while True:
            page = await self._latest_page(limit=1000, after_name=after_name)
            for row in page:
                if any(
                    normalized in term.casefold()
                    for term in [row.name, row.display_name, *row.synonyms]
                    if isinstance(term, str)
                ):
                    matches.append(row)
                    if len(matches) == bounded_limit:
                        return matches
            if len(page) < 1000:
                return matches
            after_name = page[-1].name


class DimensionRegistry:
    """当前 Principal 的版本化维度查询。"""

    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.session = session
        self.principal = principal

    async def get_by_name(self, name: str) -> Dimension | None:
        result = await self.session.scalars(
            select(Dimension)
            .where(
                Dimension.organization_id == self.principal.organization_id,
                Dimension.name == name,
            )
            .order_by(Dimension.version.desc(), Dimension.id)
            .limit(1)
        )
        return result.first()


class EntityRegistry:
    """只投影既有 semantic_entities，不启用遗留重复表。"""

    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.session = session
        self.principal = principal

    async def get_by_name(self, name: str) -> SemanticEntity | None:
        result = await self.session.scalars(
            select(SemanticEntity)
            .where(
                SemanticEntity.organization_id == self.principal.organization_id,
                SemanticEntity.name == name,
            )
            .order_by(SemanticEntity.version.desc(), SemanticEntity.id)
            .limit(1)
        )
        return result.first()


class SemanticRegistry:
    """以真实 Principal 绑定组织，拒绝从模糊候选中任取第一项。"""

    def __init__(self, session: AsyncSession, principal: Principal) -> None:
        self.metrics = MetricRegistry(session, principal)
        self.dimensions = DimensionRegistry(session, principal)
        self.entities = EntityRegistry(session, principal)

    async def resolve_metric(self, query: str) -> Metric | None:
        exact = await self.metrics.get_by_name(query)
        if exact is not None:
            return exact
        matches = await self.metrics.search(query, limit=2)
        return matches[0] if len(matches) == 1 else None

    async def resolve_dimension(self, query: str) -> Dimension | None:
        return await self.dimensions.get_by_name(query)

    async def resolve_entity(self, query: str) -> SemanticEntity | None:
        return await self.entities.get_by_name(query)
