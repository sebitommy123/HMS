"""Shared fixtures. Real Postgres via testcontainers; real Anthropic API when
`ANTHROPIC_API_KEY` is set (live tests are otherwise skipped); real Core when
`CORE_URL` is reachable."""

import os
from collections.abc import Iterator

import pytest
import requests
from sqlalchemy import text
from testcontainers.postgres import PostgresContainer

from datapro_ai.app import create_app
from datapro_ai.config import Config
from datapro_ai.db import Base, make_engine


def _normalize_pg_url(raw: str) -> str:
    return raw.replace("postgresql+psycopg2", "postgresql+psycopg")


@pytest.fixture(scope="session")
def postgres() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16", driver="psycopg2") as pg:
        _wait_postgres_reachable(pg)
        yield pg


def _wait_postgres_reachable(pg: PostgresContainer, timeout: float = 60.0) -> None:
    """Belt-and-braces: testcontainers reports the port as soon as Docker
    assigns it, but under colima the host-side port forward can take a moment
    to come up. Poll the URL until SQLAlchemy can actually connect, so the
    first test isn't a flaky race."""
    import time
    import sqlalchemy

    url = _normalize_pg_url(pg.get_connection_url())
    engine = sqlalchemy.create_engine(url, pool_pre_ping=True)
    deadline = time.time() + timeout
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with engine.connect() as conn:
                conn.execute(sqlalchemy.text("SELECT 1"))
            engine.dispose()
            return
        except Exception as exc:
            last_err = exc
            time.sleep(0.5)
    engine.dispose()
    raise RuntimeError(
        f"Postgres testcontainer never became reachable at {url}: {last_err}"
    )


@pytest.fixture(scope="session")
def core_url() -> str:
    return os.environ.get("CORE_URL", "http://127.0.0.1:5001")


@pytest.fixture(scope="session")
def core_available(core_url: str) -> bool:
    try:
        r = requests.get(f"{core_url}/health", timeout=2)
        return r.ok
    except requests.RequestException:
        return False


@pytest.fixture(scope="session")
def anthropic_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


@pytest.fixture(scope="session")
def config(postgres, core_url) -> Config:
    return Config(
        database_url=_normalize_pg_url(postgres.get_connection_url()),
        core_url=core_url,
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        # Tests default to Sonnet 4.6 (cheaper than Opus, identical API surface).
        # Production default stays at claude-opus-4-8 (see datapro_ai/config.py).
        # Override with AI_MODEL if you want to exercise a specific target.
        model=os.environ.get("AI_MODEL", "claude-sonnet-4-6"),
        max_tokens=2048,
        max_tool_iterations=6,
        cors_origins=(),
    )


@pytest.fixture(scope="session", autouse=True)
def _bootstrap_schema(config):
    engine = make_engine(config.database_url)
    Base.metadata.create_all(engine)
    yield
    engine.dispose()


@pytest.fixture
def ai_app(config):
    return create_app(config)


@pytest.fixture
def client(ai_app):
    return ai_app.test_client()


@pytest.fixture(autouse=True)
def _reset(ai_app):
    yield
    engine = ai_app.extensions["db_engine"]
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE messages CASCADE"))
        conn.execute(text("TRUNCATE TABLE conversations CASCADE"))


def requires_anthropic(test):
    """Mark tests that require ANTHROPIC_API_KEY. They auto-skip otherwise."""
    return pytest.mark.skipif(
        not os.environ.get("ANTHROPIC_API_KEY"),
        reason="ANTHROPIC_API_KEY not set",
    )(pytest.mark.live(test))


def requires_core(core_available, test):
    return pytest.mark.skipif(
        not core_available,
        reason="Core not reachable",
    )(test)
