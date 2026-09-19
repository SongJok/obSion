from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.catalog_schemas import CatalogDiscoveryView
from obsion.domain.enums import CatalogResourceKind
from obsion.registry.resource_catalog import ResourceCatalogService
from obsion.security.auth import get_principal, get_session
from obsion.security.identity import Principal

router = APIRouter(prefix="/catalog", tags=["catalog"])
_catalog = ResourceCatalogService()


@router.get("/discovery", response_model=CatalogDiscoveryView)
async def discover_catalog(
    q: str = Query(default="", max_length=200),
    kinds: list[CatalogResourceKind] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> CatalogDiscoveryView:
    # Discovery writes durable PolicyDecision receipts even though the catalog
    # operation itself is read-only.
    async with session.begin():
        return await _catalog.discover(
            session,
            principal,
            query=q,
            kinds=frozenset(kinds) if kinds else None,
            limit=limit,
        )
