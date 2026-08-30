"""Shared fixtures. Real Postgres + real Trino, no mocks.

The Trino fixture mounts the same `catalog.management=dynamic` /
`catalog.store=memory` config as the production-style `HMS/datapro/` compose, so the
test environment matches what Core actually faces in real life.
"""

import os
import time
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
import requests
from sqlalchemy import text
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network
from testcontainers.core.waiting_utils import wait_for_logs
from testcontainers.postgres import PostgresContainer

from datapro_core.app import create_app
from datapro_core.config import Config
from datapro_core.db import Base, make_engine

REPO_ROOT = Path(__file__).resolve().parents[2]
TRINO_CONFIG_HOST_PATH = REPO_ROOT / "datapro" / "trino" / "etc" / "config.properties"
# Custom Trino image with flex baked in. Tagged per stack slot so worktrees
# building different flex code don't clobber each other's image, which is why
# this comes from the environment — scripts/test.sh exports it. The `main`
# fallback is for running pytest by hand in the main clone.
TRINO_IMAGE = os.environ.get("TRINO_IMAGE", "hms-datapro/trino-flex:main")
# Mount target inside the Trino container for flex modules under test.
# Tests that materialize through Core write to the session-scoped temp
# directory the `flex_modules_dir` fixture creates; the same path is
# bind-mounted into the container at this location.
FLEX_MODULES_MOUNT = "/var/datapro-flex"
# Bundled examples directory, copied into the session temp dir at
# fixture setup so example-module tests can reference them by name.
FLEX_EXAMPLES_SRC = REPO_ROOT / "flex" / "examples"


@pytest.fixture(scope="session")
def flex_modules_dir() -> Iterator[Path]:
    """Single host directory used both for Core's materializer writes
    and for the Trino container's flex-module mount. Pre-seeded with
    a copy of the bundled examples so tests can reference them at
    /var/datapro-flex/<example>/module.py inside the container.

    Lives under the repo (not /tmp) because Docker-on-macOS via colima
    only exposes paths inside the user's home directory / project to
    the VM — /tmp paths bind-mount as empty dirs from the container's
    perspective, which would surface as "module not found" errors.
    """
    import shutil
    root = REPO_ROOT / "core" / ".pytest-flex-modules"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    for src in FLEX_EXAMPLES_SRC.iterdir():
        if src.is_dir():
            shutil.copytree(src, root / src.name)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _normalize_pg_url(raw: str) -> str:
    """testcontainers gives us postgresql+psycopg2://...; we use psycopg v3."""
    return raw.replace("postgresql+psycopg2", "postgresql+psycopg")


@pytest.fixture(scope="session")
def docker_network() -> Iterator[Network]:
    """Shared docker network so Trino can reach the test data Postgres by alias."""
    network = Network()
    network.create()
    try:
        yield network
    finally:
        network.remove()


@pytest.fixture(scope="session")
def postgres() -> Iterator[PostgresContainer]:
    """Core's metadata DB. Talks only to the test process (host), no network alias needed."""
    with PostgresContainer("postgres:16", driver="psycopg2") as pg:
        yield pg


@pytest.fixture(scope="session")
def trino(docker_network, flex_modules_dir) -> Iterator[DockerContainer]:
    # Bind-mount the session flex-modules dir so flex tests can both
    # reference pre-seeded examples AND write new modules through Core
    # that Trino subsequently reads. RW mount because Core writes to it.
    container = (
        DockerContainer(TRINO_IMAGE)
        .with_exposed_ports(8080)
        .with_volume_mapping(
            str(TRINO_CONFIG_HOST_PATH), "/etc/trino/config.properties", "ro"
        )
        .with_volume_mapping(str(flex_modules_dir), FLEX_MODULES_MOUNT, "rw")
        .with_network(docker_network)
        .with_network_aliases("trino")
    )
    container.start()
    try:
        wait_for_logs(container, "======== SERVER STARTED ========", timeout=180)
        # Belt and braces: HTTP-ping until /v1/info responds.
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(8080))
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                r = requests.get(f"http://{host}:{port}/v1/info", timeout=2)
                if r.ok:
                    break
            except requests.RequestException:
                pass
            time.sleep(1)
        else:
            raise RuntimeError("Trino started but /v1/info never responded")
        yield container
    finally:
        container.stop()


# -- Data-source Postgres (separate from Core's metadata Postgres) -------------
#
# A second Postgres container that lives on the same docker network as Trino,
# seeded with SEC-EDGAR-shaped data, used by the postgresql-catalog test.
# Trino reaches it at jdbc:postgresql://edgar-pg:5432/edgar.

EDGAR_PG_ALIAS = "edgar-pg"
EDGAR_PG_DB = "edgar"
EDGAR_PG_USER = "edgar"
EDGAR_PG_PASSWORD = "edgar"


@pytest.fixture(scope="session")
def edgar_postgres(docker_network) -> Iterator[PostgresContainer]:
    """A standalone Postgres seeded with a tiny SEC-EDGAR-shaped schema.

    Mirrors what `hms-sec-edgar-postgres` on the host conceptually provides, but
    spun up by the test so the test is hermetic.
    """
    pg = (
        PostgresContainer(
            "postgres:16",
            driver="psycopg2",
            username=EDGAR_PG_USER,
            password=EDGAR_PG_PASSWORD,
            dbname=EDGAR_PG_DB,
        )
        .with_network(docker_network)
        .with_network_aliases(EDGAR_PG_ALIAS)
    )
    pg.start()
    try:
        _wait_pg_ready(pg)
        _seed_edgar(pg)
        yield pg
    finally:
        pg.stop()


def _wait_pg_ready(pg: PostgresContainer, timeout: float = 30.0) -> None:
    dsn = _seed_dsn(pg)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with psycopg.connect(dsn, connect_timeout=2) as conn:
                conn.execute("SELECT 1")
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError("edgar postgres did not become ready in time")


def _seed_dsn(pg: PostgresContainer) -> str:
    host = pg.get_container_host_ip()
    port = pg.get_exposed_port(5432)
    return (
        f"host={host} port={port} dbname={EDGAR_PG_DB} "
        f"user={EDGAR_PG_USER} password={EDGAR_PG_PASSWORD}"
    )


def _seed_edgar(pg: PostgresContainer) -> None:
    with psycopg.connect(_seed_dsn(pg), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS companies (
                    cik       VARCHAR PRIMARY KEY,
                    ticker    VARCHAR NOT NULL,
                    name      VARCHAR NOT NULL,
                    sic       VARCHAR
                )
                """
            )
            cur.execute("TRUNCATE companies")
            cur.executemany(
                "INSERT INTO companies (cik, ticker, name, sic) VALUES (%s, %s, %s, %s)",
                [
                    ("0000320193", "AAPL", "Apple Inc.", "3571"),
                    ("0000789019", "MSFT", "Microsoft Corp.", "7372"),
                    ("0001018724", "AMZN", "Amazon.com Inc.", "5961"),
                    ("0001318605", "TSLA", "Tesla Inc.", "3711"),
                ],
            )


@pytest.fixture(scope="session")
def trino_endpoint(trino) -> tuple[str, int]:
    return trino.get_container_host_ip(), int(trino.get_exposed_port(8080))


@pytest.fixture(scope="session")
def config(postgres, trino_endpoint, flex_modules_dir) -> Config:
    host, port = trino_endpoint
    return Config(
        database_url=_normalize_pg_url(postgres.get_connection_url()),
        trino_host=host,
        trino_port=port,
        trino_user="datapro-core-test",
        cors_origins=(),
        flex_modules_host_dir=str(flex_modules_dir),
        flex_modules_container_dir=FLEX_MODULES_MOUNT,
    )


@pytest.fixture(scope="session", autouse=True)
def _bootstrap_schema(config):
    """Create the schema via SQLAlchemy metadata (no alembic in tests — faster)."""
    engine = make_engine(config.database_url)
    Base.metadata.create_all(engine)
    yield
    engine.dispose()


@pytest.fixture
def core_app(config, trino):
    app = create_app(config, start_heartbeat=False, run_startup_reconcile=False)
    # The session-scoped Trino container's host port can change across a
    # container restart (test_real_trino_restart_recovery; colima reassigns
    # host ports). Point every freshly-built app at the container's CURRENT
    # port so tests stay order-independent — otherwise every test after the
    # restart test would hit a dead port and 500 on reconcile.
    from datapro_core.trino_client import TrinoClient

    app.extensions["trino"] = TrinoClient(
        host=trino.get_container_host_ip(),
        port=int(trino.get_exposed_port(8080)),
        user=config.trino_user,
    )
    return app


@pytest.fixture
def client(core_app):
    return core_app.test_client()


@pytest.fixture
def make_data_source(core_app):
    """Insert a DataSource row directly, bypassing sync. Data sources are
    normally discovered by the reconciler from Trino; this helper is for tests
    that need a row at a specific or synthetic (schema, table) — e.g. pointing
    at a table that doesn't exist — without depending on Trino discovery.
    Returns the new id as a string.

    NB: a subsequent reconcile of this catalog will treat a row whose table
    isn't in Trino as a disappearance (delete if unreferenced, else mark
    deleted). Use this only in tests that don't reconcile, or that assert that
    behavior on purpose.
    """
    from datapro_core.models import DataSource, DataSourceStatus

    Session = core_app.extensions["db_session"]

    def _make(
        catalog_name,
        schema_name,
        table_name,
        *,
        description="",
        status=DataSourceStatus.ACTIVE,
    ) -> str:
        with Session() as s:
            ds = DataSource(
                catalog_name=catalog_name,
                schema_name=schema_name,
                table_name=table_name,
                description=description,
                status=status,
            )
            s.add(ds)
            s.commit()
            return str(ds.id)

    return _make


@pytest.fixture(autouse=True)
def _reset(config, request, core_app):
    """After every test: wipe catalogs table AND drop any catalogs still in Trino.

    This isolates tests from one another so order doesn't matter.
    """
    yield
    # 1. Drop everything from Trino (everything except `system`).
    trino_client = core_app.extensions["trino"]
    try:
        for snap in trino_client.list_catalogs():
            try:
                trino_client.drop_catalog(snap.name)
            except Exception:
                pass
    except Exception:
        pass

    # 2. Truncate Postgres tables. Cascade through the hierarchy
    # (catalogs → data_sources → object_factories) so order doesn't matter.
    engine = core_app.extensions["db_engine"]
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE object_factories"))
        conn.execute(text("TRUNCATE TABLE object_type_traits"))
        conn.execute(text("TRUNCATE TABLE data_sources CASCADE"))
        # flex_modules cascades from catalogs but TRUNCATE doesn't honor
        # FK cascades by default — listing it explicitly is cheaper than
        # adding CASCADE here, and it documents the dependency.
        conn.execute(text("TRUNCATE TABLE flex_modules"))
        conn.execute(text("TRUNCATE TABLE catalogs CASCADE"))
        conn.execute(text("TRUNCATE TABLE object_types CASCADE"))
