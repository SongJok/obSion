from fastapi import APIRouter, Request
from sqlalchemy import text

router = APIRouter(tags=["system"])


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request) -> dict[str, str]:
    async with request.app.state.database.sessions() as session:
        await session.execute(text("SELECT 1"))
    return {"status": "ready"}
