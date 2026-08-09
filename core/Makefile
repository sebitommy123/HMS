.PHONY: help install up down migrate test test-unit test-integration test-slow test-all demo lint format

PATH := $(HOME)/.local/bin:$(PATH)
DOCKER_HOST ?= unix:///Users/sebi/.colima/default/docker.sock

export PATH
export DOCKER_HOST
export TESTCONTAINERS_RYUK_DISABLED=true

help:
	@echo "DataPro Core — common targets:"
	@echo "  make install        — uv sync (deps + dev deps)"
	@echo "  make up             — start Core's Postgres (+ optionally Core itself)"
	@echo "  make down           — stop containers"
	@echo "  make migrate        — apply alembic migrations to Core's Postgres"
	@echo "  make test           — unit + integration (skips slow)"
	@echo "  make test-unit      — unit tests only (no Docker needed)"
	@echo "  make test-integration"
	@echo "  make test-slow      — slow Trino-restart test"
	@echo "  make test-all       — all tests including slow"
	@echo "  make demo           — end-to-end curl demo against running stack"
	@echo "  make lint           — ruff check"
	@echo "  make format         — ruff format"

install:
	uv sync --all-groups

up:
	docker compose up -d postgres

down:
	docker compose down

migrate:
	uv run alembic upgrade head

test: test-unit test-integration

test-unit:
	uv run pytest tests/unit -v

test-integration:
	uv run pytest tests/integration -v -m "not slow"

test-slow:
	uv run pytest tests/integration -v -m slow

test-all:
	uv run pytest tests/ -v -m "slow or not slow"

demo:
	./scripts/demo.sh

lint:
	uv run ruff check src tests

format:
	uv run ruff format src tests
