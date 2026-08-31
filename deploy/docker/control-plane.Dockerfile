# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.11.16 AS uv

FROM python:3.12.10-slim-bookworm AS builder
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv
COPY pyproject.toml uv.lock .python-version ./
COPY services/control-plane/ services/control-plane/
COPY packages/sdk-python/ packages/sdk-python/
RUN uv sync --frozen --no-dev --no-editable --package obsion-control-plane

FROM python:3.12.10-slim-bookworm AS runtime
RUN groupadd --gid 10001 obsion \
    && useradd --uid 10001 --gid obsion --no-create-home --shell /usr/sbin/nologin obsion
WORKDIR /app
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OBSION_API_HOST=0.0.0.0 \
    OBSION_API_PORT=8080
COPY --from=builder --chown=obsion:obsion /app/.venv /app/.venv
COPY --chown=obsion:obsion services/control-plane/alembic.ini services/control-plane/alembic.ini
COPY --chown=obsion:obsion services/control-plane/alembic services/control-plane/alembic
COPY --chown=obsion:obsion agents agents
COPY --chown=obsion:obsion skills skills
COPY --chown=obsion:obsion connectors connectors
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=3s --start-period=15s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/live', timeout=2)"]
CMD ["uvicorn", "obsion.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips=*", "--timeout-graceful-shutdown", "45"]
