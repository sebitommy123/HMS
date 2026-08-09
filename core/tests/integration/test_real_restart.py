"""Slow: a real Trino container restart. Run with `pytest -m slow`.

This test exercises the actual production failure mode the reconciler exists to
solve — Trino restarts, catalog.store=memory loses everything, Core replays from
its Postgres. The cheap `out-of-band DROP CATALOG` test (invariant 3) covers the
same convergence property without paying ~30s of container restart, but this is
the real one.
"""

import time

import pytest
import requests

from datapro_core.trino_client import TrinoClient


def _wait_trino_sql_ready(trino_client: TrinoClient, timeout: float = 120.0) -> None:
    """Block until Trino accepts SQL queries. We probe with an actual catalog read."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            trino_client.list_catalogs()
            return
        except Exception:
            time.sleep(1)
    pytest.fail(f"Trino SQL endpoint not ready within {timeout}s")


def _wait_v1_info_ready(host: str, port: int, timeout: float = 120.0) -> None:
    """Wait until /v1/info reports `starting: False`."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"http://{host}:{port}/v1/info", timeout=2)
            if r.ok and not r.json().get("starting", True):
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    pytest.fail(f"Trino /v1/info never reported ready within {timeout}s")


@pytest.mark.slow
def test_real_trino_restart_recovery(client, trino, config, core_app):
    """End-to-end production failure mode: catalog.store=memory loses all catalogs on
    Trino restart; Core reconciles them back from Postgres.

    Caveat: colima reassigns host ports on container restart, so we explicitly refresh
    Core's TrinoClient to point at the new port. In a real deployment (fixed-port Trino)
    this step is unnecessary."""
    # 1. Register two catalogs.
    r = client.post("/catalogs", json={"name": "tpch_r1", "connector": "tpch"})
    assert r.status_code == 201, r.get_json()
    r = client.post("/catalogs", json={"name": "tpch_r2", "connector": "tpch"})
    assert r.status_code == 201, r.get_json()

    # 2. Restart Trino.
    container = trino.get_wrapped_container()
    container.restart()

    # 3. After restart in colima the host port may change — refresh Core's client.
    new_host = trino.get_container_host_ip()
    new_port = int(trino.get_exposed_port(8080))
    core_app.extensions["trino"] = TrinoClient(
        host=new_host, port=new_port, user="datapro-core-test"
    )

    # 4. Wait for Trino to fully come back.
    _wait_v1_info_ready(new_host, new_port)
    _wait_trino_sql_ready(core_app.extensions["trino"])

    # 5. Both catalogs should be gone post-restart (in-memory catalog store).
    names_after_restart = {row["name"] for row in client.get("/trino/state").get_json()}
    assert "tpch_r1" not in names_after_restart
    assert "tpch_r2" not in names_after_restart

    # 6. Trigger reconcile via the API. Both catalogs come back.
    r = client.post("/reconcile")
    assert r.status_code == 200, r.get_json()

    names_after_reconcile = {row["name"] for row in client.get("/trino/state").get_json()}
    assert "tpch_r1" in names_after_reconcile
    assert "tpch_r2" in names_after_reconcile
