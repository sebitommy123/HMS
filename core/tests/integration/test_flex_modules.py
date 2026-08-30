"""Phase B end-to-end: Core-owned flex module storage + hot-swap.

Covers:
  * POST /catalogs with connector=flex + source → catalog + module row
    + materialized file, queryable end-to-end.
  * GET /flex-modules/{name} returns the source as last written.
  * PUT /flex-modules/{name} overwrites the source; next query reflects
    the new schema WITHOUT any DROP+CREATE of the catalog.
  * POST /flex-modules/preview returns sample rows for a transient
    catalog without persisting anything.
  * Substring + line-range edits work; ambiguous substrings reject.
  * DELETE /catalogs/{name} removes the row, the FlexModule, and the file.
  * Syntax-invalid Python is rejected at PUT + CREATE time.
"""

import json
import time
import uuid
from pathlib import Path


SIMPLE_V1 = """\
from datapro_flex import batch_from_rows

TABLE = {
    "schema": "default",
    "name": "items",
    "columns": [
        {"name": "id", "type": "BIGINT"},
        {"name": "label", "type": "VARCHAR"},
    ],
}

def get_tables():
    return [TABLE]

def read_table(table):
    rows = [{"id": 1, "label": "first"}, {"id": 2, "label": "second"}]
    yield batch_from_rows(rows, table=TABLE)
"""

SIMPLE_V2 = """\
from datapro_flex import batch_from_rows

TABLE = {
    "schema": "default",
    "name": "items",
    "columns": [
        {"name": "id", "type": "BIGINT"},
        {"name": "label", "type": "VARCHAR"},
        {"name": "count", "type": "INTEGER"},
    ],
}

def get_tables():
    return [TABLE]

def read_table(table):
    rows = [
        {"id": 1, "label": "first",  "count": 100},
        {"id": 2, "label": "second", "count": 200},
        {"id": 3, "label": "third",  "count": 300},
    ]
    yield batch_from_rows(rows, table=TABLE)
"""


def _ucat() -> str:
    """Unique-but-Trino-safe catalog name (no hyphens — Trino's
    identifier parser is stricter than Core's)."""
    return f"flex_b_{uuid.uuid4().hex[:8]}"


def _create_flex_catalog(client, name: str, source: str):
    r = client.post(
        "/catalogs",
        json={"name": name, "connector": "flex", "source": source},
    )
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def _query_rows(client, sql: str) -> list[list]:
    r = client.post("/raw-trino-query", json={"sql": sql})
    assert r.status_code == 200, r.get_json()
    return r.get_json()["rows"]


def _query_rows_eventually(client, sql: str, expected: list, timeout: float = 15.0):
    """Poll ``sql`` until it returns ``expected`` (or times out). Flex hot-swap
    is eventually-consistent: after Core writes the new module file, the change
    must propagate across the Trino container's filesystem mount before the
    worker's next scan re-reads it. A single fixed-delay query is racy on slow
    mounts (CI, virtiofs); polling tests the guarantee without the flake. The
    reload itself is verified correct out-of-band — this only absorbs FS lag.

    We wait BEFORE the first query: on virtiofs a read that lands before the
    edit propagates can cache the stale content in the container, so we give
    the mount a head start rather than reading eagerly."""
    deadline = time.monotonic() + timeout
    rows = None
    while time.monotonic() < deadline:
        time.sleep(1.0)
        rows = _query_rows(client, sql)
        if rows == expected:
            return rows
    assert rows == expected  # timed out — surface a clear diff
    return rows


# ---- CREATE flex catalog via inline source ----


def test_create_flex_catalog_with_inline_source_materializes_and_queries(client, flex_modules_dir):
    name = _ucat()
    body = _create_flex_catalog(client, name, SIMPLE_V1)

    # Flex catalog properties (flex.module_path) are Core-managed and redacted
    # from client responses — never exposed or editable.
    cat = body["catalog"]
    assert cat["connector"] == "flex"
    assert cat["properties"] == {}
    assert body["reconcile"]["all_ok"] is True

    # FlexModule row exists with the original source.
    r = client.get(f"/flex-modules/{name}")
    assert r.status_code == 200, r.get_json()
    mod = r.get_json()
    assert mod["catalog_name"] == name
    assert mod["source_text"] == SIMPLE_V1

    # Materialized file exists on disk and matches the source.
    on_disk = (Path(flex_modules_dir) / f"{name}.py").read_text()
    assert on_disk == SIMPLE_V1

    # Trino can query the table the module declares.
    rows = _query_rows(client, f'SELECT id, label FROM "{name}".default.items ORDER BY id')
    assert rows == [[1, "first"], [2, "second"]]


# ---- PUT new source hot-swaps without DROP+CREATE ----


def test_put_source_hot_swaps_schema_without_catalog_drop(client):
    name = _ucat()
    _create_flex_catalog(client, name, SIMPLE_V1)

    # First query primes the worker on v1.
    _query_rows(client, f'SELECT id FROM "{name}".default.items')

    # PUT new source: an extra `count` column lands.
    # mtime resolution on most filesystems is 1s, so wait briefly to
    # guarantee the new write has a strictly-greater mtime.
    time.sleep(1.1)
    r = client.put(f"/flex-modules/{name}", json={"source": SIMPLE_V2})
    assert r.status_code == 200, r.get_json()

    # Next query sees the new schema and the new row. No DROP+CREATE
    # happened — the catalog name is unchanged from the client's
    # perspective, but the underlying worker has reloaded the module.
    _query_rows_eventually(
        client,
        f'SELECT id, label, count FROM "{name}".default.items ORDER BY id',
        [[1, "first", 100], [2, "second", 200], [3, "third", 300]],
    )


# ---- preview without committing ----


def test_preview_returns_sample_rows_and_doesnt_persist(client):
    r = client.post(
        "/flex-modules/preview",
        json={"source": SIMPLE_V1, "sample_limit": 5},
    )
    assert r.status_code == 200, r.get_json()
    body = r.get_json()
    assert len(body["tables"]) == 1
    table = body["tables"][0]
    assert table["schema"] == "default"
    assert table["name"] == "items"
    column_names = [c["name"] for c in table["columns"]]
    assert column_names == ["id", "label"]
    assert table["sample_rows"] == [[1, "first"], [2, "second"]]

    # No catalog left behind. SHOW CATALOGS only lists `system`
    # (no other catalogs created by other tests in this session
    # would start with `_flex_preview_` since we always clean up).
    r = client.post("/raw-trino-query", json={"sql": "SHOW CATALOGS"})
    catalogs = {row[0] for row in r.get_json()["rows"]}
    assert not any(c.startswith("_flex_preview_") for c in catalogs)


# ---- substring + line-range edits ----


def test_substring_replace_edits_source(client):
    name = _ucat()
    _create_flex_catalog(client, name, SIMPLE_V1)

    # NB: use a length-CHANGING replacement ("first" -> "first_edited"). The
    # reload is content-hash based and detects same-length edits fine on real
    # deployments, but the local/CI Trino runs on colima's virtiofs mount,
    # whose attribute cache can serve stale-but-same-size file content to the
    # container after the worker read it early (during data-source sync). A
    # size change reliably invalidates that cache. See the flex worker's
    # content-signature reload for the actual detection logic.
    r = client.post(
        f"/flex-modules/{name}/replace",
        json={"old_text": '"first"', "new_text": '"first_edited"'},
    )
    assert r.status_code == 200, r.get_json()

    _query_rows_eventually(
        client,
        f'SELECT label FROM "{name}".default.items ORDER BY id',
        [["first_edited"], ["second"]],
    )


def test_substring_replace_rejects_missing_text(client):
    name = _ucat()
    _create_flex_catalog(client, name, SIMPLE_V1)
    r = client.post(
        f"/flex-modules/{name}/replace",
        json={"old_text": "this text is not in the module", "new_text": "x"},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "old_text_not_found"


def test_substring_replace_rejects_ambiguous(client):
    name = _ucat()
    _create_flex_catalog(client, name, SIMPLE_V1)
    # The substring "id" appears many times in SIMPLE_V1.
    r = client.post(
        f"/flex-modules/{name}/replace",
        json={"old_text": "id", "new_text": "ident"},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "old_text_ambiguous"
    assert r.get_json()["occurrences"] > 1


def test_line_range_replace_edits_source(client):
    name = _ucat()
    _create_flex_catalog(client, name, SIMPLE_V1)

    # Inspect to find the line numbers of the rows-literal — but for
    # the test we know SIMPLE_V1 well enough to hardcode them. Find
    # the line that says `rows = [` and replace it + the row literal
    # below with three rows instead of two.
    source = client.get(f"/flex-modules/{name}").get_json()["source_text"]
    lines = source.splitlines()
    rows_line = next(i for i, l in enumerate(lines, start=1) if "rows = [" in l)

    r = client.post(
        f"/flex-modules/{name}/replace-lines",
        json={
            "start_line": rows_line,
            "end_line": rows_line,
            "new_text": '    rows = [{"id": 9, "label": "extra"}]',
        },
    )
    assert r.status_code == 200, r.get_json()

    _query_rows_eventually(
        client, f'SELECT id, label FROM "{name}".default.items', [[9, "extra"]]
    )


# ---- syntax validation ----


def test_create_rejects_invalid_python(client):
    name = _ucat()
    bad = "def get_tables(:\n  return []\n"
    r = client.post(
        "/catalogs",
        json={"name": name, "connector": "flex", "source": bad},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_python"


def test_put_rejects_invalid_python(client):
    name = _ucat()
    _create_flex_catalog(client, name, SIMPLE_V1)
    r = client.put(
        f"/flex-modules/{name}",
        json={"source": "this is not python @@@"},
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_python"

    # And the on-disk file is unchanged — failure was caught pre-write.
    r = client.get(f"/flex-modules/{name}")
    assert r.get_json()["source_text"] == SIMPLE_V1


# ---- delete cascade ----


def test_delete_catalog_removes_module_and_file(client, flex_modules_dir):
    name = _ucat()
    _create_flex_catalog(client, name, SIMPLE_V1)
    on_disk = Path(flex_modules_dir) / f"{name}.py"
    assert on_disk.exists()

    r = client.delete(f"/catalogs/{name}")
    assert r.status_code == 200, r.get_json()

    # FlexModule row gone via FK cascade.
    r = client.get(f"/flex-modules/{name}")
    assert r.status_code == 404

    # File gone via the catalog blueprint's flex-aware cleanup.
    assert not on_disk.exists()


# ---- flex.module_path vs source mutual exclusion ----


def test_supplying_both_source_and_module_path_is_rejected(client):
    name = _ucat()
    r = client.post(
        "/catalogs",
        json={
            "name": name,
            "connector": "flex",
            "source": SIMPLE_V1,
            "properties": {"flex.module_path": "/var/datapro-flex/whatever.py"},
        },
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "conflicting_flex_inputs"


def test_read_table_traceback_surfaces_through_flight(client):
    """When read_table throws on the Python side, the operator should
    see the actual Python error in the Trino error chain — not just
    "flex worker stream failed". Regression guard for the FlightRuntimeException
    description extraction in FlexPageSource."""
    bad = """\
from datapro_flex import batch_from_rows

TABLE = {
    "schema": "default",
    "name": "items",
    "columns": [{"name": "id", "type": "BIGINT"}],
}

def get_tables():
    return [TABLE]

def read_table(table):
    raise RuntimeError("intentional read_table failure for the test")
"""
    name = _ucat()
    _create_flex_catalog(client, name, bad)
    r = client.post(
        "/raw-trino-query",
        json={"sql": f'SELECT * FROM "{name}".default.items'},
    )
    assert r.status_code in (400, 502, 504), r.get_json()
    # The Python error message survives all the way back to the HTTP
    # response body — that's the whole point of the change.
    body = r.get_json()
    detail = json.dumps(body)
    assert "intentional read_table failure for the test" in detail, detail


def test_read_table_arity_mismatch_surfaces_python_traceback(client):
    """The case that bit the AI agent: a module callable defined with the
    wrong number of positional args. Should surface the Python
    TypeError, not the generic Flight failure."""
    bad = """\
from datapro_flex import batch_from_rows

TABLE = {
    "schema": "default",
    "name": "items",
    "columns": [{"name": "id", "type": "BIGINT"}],
}

def get_tables():
    return [TABLE]

def read_table(table, extra):   # worker calls read_table(name) with one arg
    yield batch_from_rows([{"id": 1}], table=TABLE)
"""
    name = _ucat()
    _create_flex_catalog(client, name, bad)
    r = client.post(
        "/raw-trino-query",
        json={"sql": f'SELECT * FROM "{name}".default.items'},
    )
    assert r.status_code in (400, 502, 504), r.get_json()
    detail = json.dumps(r.get_json())
    assert "read_table()" in detail and "positional argument" in detail, detail


def test_supplying_neither_source_nor_module_path_is_rejected(client):
    name = _ucat()
    r = client.post(
        "/catalogs", json={"name": name, "connector": "flex"}
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "flex_module_required"
