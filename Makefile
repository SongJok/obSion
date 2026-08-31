SHELL := /bin/sh

.PHONY: bootstrap compose-up stack-up compose-down dev-api dev-web dev-cli dev-ide dev-im dev-desktop migrate migration-check lint format format-check test test-java check validate-contracts validate-evaluations validate-eval-gates validate-release-notes validate-release-candidate-contract validate-release-candidate validate-feishu-live validate-feishu-browse-live validate-feishu-send-live record-feishu-live-evidence evaluate-datasets scan-secrets sbom release-artifacts validate-release-artifacts

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

validate-release-candidate-contract:
	uv run obsion validate-release-candidate --contract-only

validate-release-candidate:
	uv run obsion validate-release-candidate --write-report

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

record-feishu-live-evidence:
	@case "$${OBSION_FEISHU_LIVE:-}" in 1) ;; *) echo "OBSION_FEISHU_LIVE=1 is required"; exit 2;; esac
	@test -n "$${OBSION_FEISHU_APP_ID:-}" || (echo "OBSION_FEISHU_APP_ID is required"; exit 2)
	@test -n "$${OBSION_FEISHU_APP_SECRET:-}" || (echo "OBSION_FEISHU_APP_SECRET is required"; exit 2)
	@test -n "$${OBSION_LIVE_PROFILE:-}" || (echo "OBSION_LIVE_PROFILE is required"; exit 2)
	@if [ "$${OBSION_FEISHU_SEND_LIVE:-}" = "1" ]; then \
		test -n "$${OBSION_FEISHU_LIVE_CHAT_ID:-}" || (echo "OBSION_FEISHU_LIVE_CHAT_ID is required"; exit 2); \
		uv run obsion record-live-evidence --profile-label "$${OBSION_LIVE_PROFILE}" --include-send-probe; \
	else \
		uv run obsion record-live-evidence --profile-label "$${OBSION_LIVE_PROFILE}"; \
	fi

evaluate-datasets:
	uv run obsion evaluate-datasets

scan-secrets:
	uv run obsion scan-secrets

sbom:
	uv run obsion sbom --output docs/release/sbom.cdx.json

release-artifacts:
	uv run --no-sync python scripts/release_artifacts.py build

validate-release-artifacts:
	uv run --no-sync python scripts/release_artifacts.py validate --require-clean

lint:
	uv run ruff check .
	uv run mypy services/control-plane/src packages/sdk-python/src apps/cli/src apps/im-adapter/src
	uv run obsion validate-contracts
	uv run obsion validate-evaluations
	uv run obsion validate-eval-gates
	uv run obsion validate-release-notes
	uv run obsion validate-release-candidate --contract-only
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
	docker run --rm -v "$(CURDIR):/workspace" -w /workspace/packages/sdk-java eclipse-temurin:21-jdk ./mvnw -B clean test

check: format-check lint test migration-check
