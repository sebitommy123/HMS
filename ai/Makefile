.PHONY: help install up down migrate test test-unit test-live lint format

PATH := $(HOME)/.local/bin:$(PATH)
DOCKER_HOST ?= unix:///Users/sebi/.colima/default/docker.sock

export PATH
export DOCKER_HOST
export TESTCONTAINERS_RYUK_DISABLED=true

help:
	@echo "DataPro AI — common targets:"
	@echo "  make install      — uv sync (deps + dev deps)"
	@echo "  make up           — start AI's Postgres"
	@echo "  make down         — stop containers"
	@echo "  make migrate      — apply alembic migrations"
	@echo "  make test         — unit tests (no API key needed)"
	@echo "  make test-live    — live tests against real Anthropic + real Core (needs ANTHROPIC_API_KEY + Core running)"
	@echo "  make lint         — ruff check"
	@echo "  make format       — ruff format"

install:
	uv sync --all-groups

up:
	docker compose up -d postgres

down:
	docker compose down

migrate:
	uv run alembic upgrade head

test:
	uv run pytest tests/unit -v

test-live:
	uv run pytest tests/integration -v -m live

lint:
	uv run ruff check src tests

format:
	uv run ruff format src tests
