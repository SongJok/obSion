SHELL := /bin/sh

.PHONY: bootstrap compose-up stack-up compose-down dev-api dev-web dev-cli dev-ide dev-im dev-desktop migrate migration-check lint format format-check test test-java check validate-contracts validate-evaluations validate-eval-gates validate-release-notes validate-feishu-live validate-feishu-browse-live evaluate-datasets scan-secrets sbom

bootstrap:
	uv sync --all-packages --all-extras
	npm install

compose-up:
	docker compose up -d postgres redis minio minio-init

stack-up:
	docker compose up -d --build

compose-down:
	docker compose down

dev-api:
	uv run --package obsion-control-plane uvicorn obsion.main:create_app --factory --reload --host 0.0.0.0 --port 8080

dev-web:
	npm run dev:web

dev-cli:
	uv run --package obsion-cli obsion-cli --help

dev-ide:
	npm run build --workspace @obsion/ide-extension

dev-im:
	uv run --package obsion-im obsion-im --help

dev-desktop:
	npm run build --workspace @obsion/desktop
	npx obsion-desktop --help

migrate:
	uv run --package obsion-control-plane alembic -c services/control-plane/alembic.ini upgrade head

migration-check:
	uv run --package obsion-control-plane alembic -c services/control-plane/alembic.ini check

validate-contracts:
	uv run obsion validate-contracts

validate-evaluations:
	uv run obsion validate-evaluations

validate-eval-gates:
	uv run obsion validate-eval-gates

validate-release-notes:
	uv run obsion validate-release-notes

validate-feishu-live:
	@case "$${OBSION_FEISHU_LIVE:-}" in 1) ;; *) echo "OBSION_FEISHU_LIVE=1 is required"; exit 2;; esac
	@test -n "$${OBSION_FEISHU_APP_ID:-}" || (echo "OBSION_FEISHU_APP_ID is required"; exit 2)
	@test -n "$${OBSION_FEISHU_APP_SECRET:-}" || (echo "OBSION_FEISHU_APP_SECRET is required"; exit 2)
	uv run pytest --no-cov -m live

validate-feishu-browse-live:
	@case "$${OBSION_FEISHU_BROWSE_LIVE:-}" in 1) ;; *) echo "OBSION_FEISHU_BROWSE_LIVE=1 is required"; exit 2;; esac
	@test -n "$${OBSION_FEISHU_APP_ID:-}" || (echo "OBSION_FEISHU_APP_ID is required"; exit 2)
	@test -n "$${OBSION_FEISHU_APP_SECRET:-}" || (echo "OBSION_FEISHU_APP_SECRET is required"; exit 2)
	uv run pytest --no-cov -m feishu_browse_live

validate-feishu-send-live:
	@case "$${OBSION_FEISHU_SEND_LIVE:-}" in 1) ;; *) echo "OBSION_FEISHU_SEND_LIVE=1 is required"; exit 2;; esac
	@test -n "$${OBSION_FEISHU_APP_ID:-}" || (echo "OBSION_FEISHU_APP_ID is required"; exit 2)
	@test -n "$${OBSION_FEISHU_APP_SECRET:-}" || (echo "OBSION_FEISHU_APP_SECRET is required"; exit 2)
	@test -n "$${OBSION_FEISHU_LIVE_CHAT_ID:-}" || (echo "OBSION_FEISHU_LIVE_CHAT_ID is required"; exit 2)
	uv run pytest --no-cov -m feishu_send_live

evaluate-datasets:
	uv run obsion evaluate-datasets

scan-secrets:
	uv run obsion scan-secrets

sbom:
	uv run obsion sbom --output docs/release/sbom.cdx.json

lint:
	uv run ruff check .
	uv run mypy services/control-plane/src packages/sdk-python/src apps/cli/src apps/im-adapter/src
	uv run obsion validate-contracts
	uv run obsion validate-evaluations
	uv run obsion validate-eval-gates
	uv run obsion validate-release-notes
	uv run obsion evaluate-datasets
	uv run obsion scan-secrets
	npm run lint
	npm run typecheck

format:
	uv run ruff format .
	uv run ruff check --fix .

format-check:
	uv run ruff format --check .

test:
	uv run pytest
	npm test

test-java:
	cd packages/sdk-java && ./mvnw -B test

check: format-check lint test migration-check
