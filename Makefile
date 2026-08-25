SHELL := /bin/sh

.PHONY: bootstrap compose-up stack-up compose-down dev-api dev-web migrate migration-check lint format test check validate-evaluations

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

migrate:
	uv run --package obsion-control-plane alembic -c services/control-plane/alembic.ini upgrade head

migration-check:
	uv run --package obsion-control-plane alembic -c services/control-plane/alembic.ini check

validate-evaluations:
	uv run obsion validate-evaluations

lint:
	uv run ruff check .
	uv run mypy services/control-plane/src packages/sdk-python/src
	uv run obsion validate-evaluations
	npm run lint
	npm run typecheck

format:
	uv run ruff format .
	uv run ruff check --fix .

test:
	uv run pytest
	npm test

check: lint test migration-check
