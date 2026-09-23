"""Deterministic tests for the ``env`` axis — real Postgres + real Trino, no
Claude. Hand-built env operations exercise overlay isolation, env-scoped
querying, atomic promote, drift/collision, and tombstones."""

import uuid


EDGAR_PROPS = {
    "connection-url": "jdbc:postgresql://edgar-pg:5432/edgar",
    "connection-user": "edgar",
    "connection-password": "edgar",
}


def _make_env(client, label="chat-1"):
    r = client.post("/environments", json={"label": label})
    assert r.status_code == 201, r.get_json()
    return r.get_json()["id"]


def _wire_company_in_env(client, env, catalog="edgar"):
    """Create catalog → object type (+identity) → factory, all in ``env``.
    Returns (object_type_id, factory_id)."""
    r = client.post(
        f"/catalogs?env={env}",
        json={"name": catalog, "connector": "postgresql", "properties": EDGAR_PROPS},
    )
    assert r.status_code == 201, r.get_json()

    # Data source discovered by the reconcile that create ran; resolve it in env.
    ds = client.get(f"/data-sources?env={env}&catalog={catalog}").get_json()
    companies = [d for d in ds if d["table_name"] == "companies"]
    assert companies, f"companies data source not discovered: {ds}"
    ds_id = companies[0]["id"]

    r = client.post(f"/object-types?env={env}", json={"name": "company"})
    assert r.status_code == 201, r.get_json()
    type_id = r.get_json()["id"]

    r = client.put(f"/object-types/{type_id}/traits/identity?env={env}")
    assert r.status_code == 200, r.get_json()

    r = client.post(
        f"/object-factories?env={env}",
        json={
            "data_source_id": ds_id,
            "object_type_id": type_id,
            "trait_config": {"identity": {"column": "cik"}},
        },
    )
    assert r.status_code == 201, r.get_json()
    return type_id, r.get_json()["id"]


def _query(client, env=None):
    url = "/query" if env is None else f"/query?env={env}"
    return client.post(url, json={"from": "company", "limit": 10})


def test_env_overlay_isolation_and_promote(client, edgar_postgres, core_app):
    """The headline flow: build a whole object graph inside an env, query it
    there, confirm prod can't see it, then promote and confirm prod now can."""
    env = _make_env(client)
    _wire_company_in_env(client, env)

    # Prod is blind to the env's work.
    assert client.get("/catalogs").get_json() == []
    assert _query(client, env=None).status_code == 404  # object type not in prod

    # The env sees its own catalog (by its LOGICAL name) ...
    env_catalogs = {c["name"] for c in client.get(f"/catalogs?env={env}").get_json()}
    assert env_catalogs == {"edgar"}

    # ... and querying in the env returns real rows.
    r = _query(client, env=env)
    assert r.status_code == 200, r.get_json()
    objs = r.get_json()["objects"]
    assert len(objs) == 4  # AAPL, AMZN, MSFT, TSLA

    # Promote → prod now has everything, env is retired.
    pr = client.post(f"/environments/{env}/promote")
    assert pr.status_code == 200, pr.get_json()
    assert pr.get_json()["result"]["catalogs_promoted"] == 1
    assert client.get(f"/environments/{env}").get_json()["status"] == "promoted"

    prod = _query(client, env=None)
    assert prod.status_code == 200, prod.get_json()
    assert len(prod.get_json()["objects"]) == 4
    assert {c["name"] for c in client.get("/catalogs").get_json()} == {"edgar"}


def test_two_envs_isolated(client, edgar_postgres):
    """Two envs each create a same-named catalog; they get distinct physical
    Trino catalogs and don't see each other's work."""
    a = _make_env(client, "chat-a")
    b = _make_env(client, "chat-b")
    _wire_company_in_env(client, a)
    _wire_company_in_env(client, b)

    # Each env sees exactly one "company" object type and 4 rows; prod sees none.
    assert _query(client, env=a).status_code == 200
    assert len(_query(client, env=a).get_json()["objects"]) == 4
    assert len(_query(client, env=b).get_json()["objects"]) == 4
    assert _query(client, env=None).status_code == 404

    # Distinct physical catalog names registered in Trino.
    names = {row["name"] for row in client.get("/trino/state").get_json()}
    stg = {n for n in names if n.startswith("stg_") and n.endswith("_edgar")}
    assert len(stg) == 2, names


def test_promote_collision_aborts(client, edgar_postgres):
    """If prod gains a same-named object type while an env holds an env-native
    one, promote must abort (collision) and apply nothing."""
    env = _make_env(client)
    _wire_company_in_env(client, env)

    # Prod independently creates a 'company' type — now the env's promote would
    # collide on the type name.
    assert client.post("/object-types", json={"name": "company"}).status_code == 201

    conflicts = client.get(f"/environments/{env}/conflicts").get_json()["conflicts"]
    assert any(c["kind"] == "collision" and c["entity"] == "object_type" for c in conflicts)

    pr = client.post(f"/environments/{env}/promote")
    assert pr.status_code == 409, pr.get_json()
    # Nothing applied: prod still has no factories/catalog from the env.
    assert client.get("/catalogs").get_json() == []
    assert client.get(f"/environments/{env}").get_json()["status"] == "open"


def test_additive_env_is_drift_immune(client, edgar_postgres):
    """Churn on a prod entity the env never touched does not block promote."""
    # A pre-existing prod catalog the env won't touch.
    assert client.post("/catalogs", json={"name": "tpch_x", "connector": "tpch"}).status_code == 201

    env = _make_env(client)
    _wire_company_in_env(client, env)

    # Prod mutates the untouched catalog (delete it) after the env was built.
    assert client.delete("/catalogs/tpch_x").status_code == 200

    pr = client.post(f"/environments/{env}/promote")
    assert pr.status_code == 200, pr.get_json()


def test_env_delete_of_prod_catalog_is_tombstoned(client, edgar_postgres):
    """Deleting a prod catalog from within an env tombstones it: prod keeps it
    until promote; the env doesn't see it; promote removes it from prod."""
    # Prod catalog.
    assert client.post("/catalogs", json={"name": "tpch_x", "connector": "tpch"}).status_code == 201

    env = _make_env(client)
    r = client.delete(f"/catalogs/tpch_x?env={env}")
    assert r.status_code == 200, r.get_json()

    # Prod still has it; the env doesn't.
    assert {c["name"] for c in client.get("/catalogs").get_json()} == {"tpch_x"}
    assert {c["name"] for c in client.get(f"/catalogs?env={env}").get_json()} == set()

    pr = client.post(f"/environments/{env}/promote")
    assert pr.status_code == 200, pr.get_json()
    assert pr.get_json()["result"]["catalogs_deleted"] == 1
    assert client.get("/catalogs").get_json() == []


FLEX_SRC = """\
from datapro_flex import batch_from_rows
TABLE = {"schema": "default", "name": "items",
         "columns": [{"name": "id", "type": "BIGINT"}, {"name": "label", "type": "VARCHAR"}]}
def get_tables():
    return [TABLE]
def read_table(table):
    yield batch_from_rows([{"id": 1, "label": "a"}, {"id": 2, "label": "b"}], table=TABLE)
"""


def test_flex_catalog_in_env(client, core_app):
    """A flex catalog can be created, edited, queried and promoted entirely
    within a staging env — exercising the env-scoped flex_modules endpoints and
    the flex connector at scan time."""
    env = _make_env(client, "flex-chat")

    # Create a flex catalog in the env (mangled physical name, module materialized).
    r = client.post(
        f"/catalogs?env={env}",
        json={"name": "widgets", "connector": "flex", "source": FLEX_SRC},
    )
    assert r.status_code == 201, r.get_json()

    # The env sees its flex module by logical name; prod does not.
    assert client.get(f"/flex-modules/widgets?env={env}").status_code == 200
    assert client.get("/flex-modules/widgets").status_code == 404

    # Wire an object type + factory over the flex table and query it in the env.
    ds = client.get(f"/data-sources?env={env}&catalog=widgets").get_json()
    items = [d for d in ds if d["table_name"] == "items"]
    assert items, f"flex table not discovered: {ds}"
    tid = client.post(f"/object-types?env={env}", json={"name": "widget"}).get_json()["id"]
    r = client.post(
        f"/object-factories?env={env}",
        json={"data_source_id": items[0]["id"], "object_type_id": tid},
    )
    assert r.status_code == 201, r.get_json()

    r = client.post(f"/query?env={env}", json={"from": "widget", "limit": 10})
    assert r.status_code == 200, r.get_json()
    assert len(r.get_json()["objects"]) == 2

    # Editing the module in-env is env-scoped (prod flex module untouched — there
    # is none). Hot-swap to three rows.
    new_src = FLEX_SRC.replace(
        '[{"id": 1, "label": "a"}, {"id": 2, "label": "b"}]',
        '[{"id": 1, "label": "a"}, {"id": 2, "label": "b"}, {"id": 3, "label": "c"}]',
    )
    assert client.put(f"/flex-modules/widgets?env={env}", json={"source": new_src}).status_code == 200
    r = client.post(f"/query?env={env}", json={"from": "widget", "limit": 10})
    assert len(r.get_json()["objects"]) == 3

    # Prod is still blind; promote makes it real.
    assert client.post("/query", json={"from": "widget", "limit": 10}).status_code == 404
    assert client.post(f"/environments/{env}/promote").status_code == 200
    r = client.post("/query", json={"from": "widget", "limit": 10})
    assert r.status_code == 200, r.get_json()
    assert len(r.get_json()["objects"]) == 3


def test_bad_env_id_rejected(client):
    assert client.get("/catalogs?env=not-a-uuid").status_code == 400
    assert client.get(f"/catalogs?env={uuid.uuid4()}").status_code == 400  # unknown env
