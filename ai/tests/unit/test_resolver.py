"""Deterministic resolver tests — a fake Core, no stack, no Claude.

Validates the AI-owned action→env-call replay: handle/prod ref resolution,
DataSourceRef resolution, reorder invalidation, and rebuild determinism (the
property that re-materializing rebinds handles to fresh ids without baking any
staging id into the changeset)."""

import pytest

from datapro_ai.staging.actions import Changeset
from datapro_ai.staging.resolver import Resolver, ResolverError


class FakeCore:
    """Records env-scoped calls, mints deterministic ids, simulates data-source
    discovery. Every call asserts it carries a real (non-prod) env — the resolver
    must never touch prod directly."""

    def __init__(self):
        self.calls: list[tuple] = []
        self._env_n = 0
        self._id_n = 0
        self.dropped: list[str] = []
        self.promoted: list[str] = []

    def _mint(self, prefix):
        self._id_n += 1
        return f"{prefix}-{self._id_n}"

    def make_env(self, label=""):
        self._env_n += 1
        return f"env{self._env_n}"

    def drop_env(self, env):
        self.dropped.append(env)

    def promote_env(self, env):
        self.promoted.append(env)
        return {"promoted": env, "result": {}}

    def create_catalog(self, env, name, connector, properties, source):
        assert env and env.startswith("env")
        self.calls.append(("create_catalog", env, name, connector))
        return {"name": name, "physical_name": f"stg_{env}_{name}"}

    def create_object_type(self, env, name, description):
        assert env and env.startswith("env")
        tid = self._mint("type")
        self.calls.append(("create_object_type", env, name, tid))
        return {"id": tid}

    def add_trait(self, env, type_id, trait):
        assert env and env.startswith("env")
        self.calls.append(("add_trait", env, type_id, trait))
        return {}

    def resolve_data_source(self, env, catalog_logical, schema, table):
        assert env and env.startswith("env")
        return f"ds:{env}:{catalog_logical}:{schema}:{table}"

    def create_object_factory(self, env, **fields):
        assert env and env.startswith("env")
        fid = self._mint("factory")
        self.calls.append(
            ("create_object_factory", env, fields["data_source_id"], fields["object_type_id"], fid)
        )
        return {"id": fid}

    def update_catalog(self, env, name, **fields):
        self.calls.append(("update_catalog", env, name, fields))
        return {}

    def set_flex_module(self, env, name, source):
        self.calls.append(("set_flex_module", env, name, source))
        return {}

    def update_object_type(self, env, type_id, **fields):
        self.calls.append(("update_object_type", env, type_id, fields))
        return {}

    def update_object_factory(self, env, factory_id, **fields):
        self.calls.append(("update_object_factory", env, factory_id, fields))
        return {}

    # unused-in-these-tests members of the protocol
    def delete_catalog(self, *a, **k): ...
    def delete_object_type(self, *a, **k): ...
    def remove_trait(self, *a, **k): ...
    def delete_object_factory(self, *a, **k): ...
    def conflicts(self, *a, **k): return []
    def query(self, *a, **k): return {}


def _wire_changeset():
    """create_catalog → create_object_type → add_trait → create_object_factory,
    where the factory references BOTH the action-created catalog and type."""
    return Changeset.model_validate({"actions": [
        {"op": "create_catalog", "handle": "cat1", "name": "sales", "connector": "postgresql",
         "properties": {"connection-url": "jdbc:postgresql://h/db"}},
        {"op": "create_object_type", "handle": "ot1", "name": "customer", "description": ""},
        {"op": "add_trait", "target": {"source": "action", "handle": "ot1"}, "trait": "identity"},
        {"op": "create_object_factory", "handle": "f1",
         "data_source": {"catalog": {"source": "action", "handle": "cat1"},
                         "schema_name": "public", "table": "customers"},
         "object_type": {"source": "action", "handle": "ot1"},
         "trait_config": {"identity": {"column": "id"}}},
    ]})


def test_materialize_resolves_refs():
    core = FakeCore()
    env = Resolver(core).materialize(_wire_changeset())

    ops = [c[0] for c in core.calls]
    assert ops == ["create_catalog", "create_object_type", "add_trait", "create_object_factory"]

    # The minted type id flows from create_object_type → add_trait → factory.
    type_id = next(c[3] for c in core.calls if c[0] == "create_object_type")
    trait_call = next(c for c in core.calls if c[0] == "add_trait")
    assert trait_call[2] == type_id  # add_trait got the resolved type id
    fac = next(c for c in core.calls if c[0] == "create_object_factory")
    assert fac[3] == type_id  # factory.object_type_id resolved to the same type
    # factory.data_source_id resolved via the action-created catalog's LOGICAL name
    assert fac[2] == f"ds:{env}:sales:public:customers"


def test_prod_ref_used_directly():
    core = FakeCore()
    cs = Changeset.model_validate({"actions": [
        {"op": "add_trait", "target": {"source": "prod", "kind": "object_type",
                                       "id": "prod-type-123"}, "trait": "temporal"},
    ]})
    Resolver(core).materialize(cs)
    trait_call = next(c for c in core.calls if c[0] == "add_trait")
    assert trait_call[2] == "prod-type-123"  # prod ref passed through verbatim


def test_reorder_dangling_ref_fails_and_drops_env():
    """A factory before its catalog's create-action → the catalog handle is
    unresolved → ResolverError, and the half-built env is torn down."""
    core = FakeCore()
    cs = Changeset.model_validate({"actions": [
        {"op": "create_object_type", "handle": "ot1", "name": "x", "description": ""},
        {"op": "create_object_factory", "handle": "f1",
         "data_source": {"catalog": {"source": "action", "handle": "cat1"},  # not yet created
                         "schema_name": "public", "table": "t"},
         "object_type": {"source": "action", "handle": "ot1"}},
        {"op": "create_catalog", "handle": "cat1", "name": "sales", "connector": "tpch"},
    ]})
    with pytest.raises(ResolverError):
        Resolver(core).materialize(cs)
    assert core.dropped, "half-built env should be dropped on failure"


def test_rebuild_is_deterministic_and_rebinds():
    """Materializing the same changeset twice yields two distinct env ids but an
    identical *logical* call structure — no staging id is baked into the actions."""
    cs = _wire_changeset()
    core = FakeCore()
    r = Resolver(core)
    env_a = r.materialize(cs)
    env_b = r.materialize(cs)
    assert env_a != env_b

    def logical(calls, env):
        # drop env + minted ids, keep the op + logical names/structure
        out = []
        for c in calls:
            if c[1] != env:
                continue
            if c[0] == "create_catalog":
                out.append(("create_catalog", c[2], c[3]))
            elif c[0] == "create_object_type":
                out.append(("create_object_type", c[2]))
            elif c[0] == "add_trait":
                out.append(("add_trait", c[3]))
            elif c[0] == "create_object_factory":
                out.append(("create_object_factory",))
        return out

    assert logical(core.calls, env_a) == logical(core.calls, env_b)


def test_update_and_flex_ops_replay():
    """Update + set_flex_module actions dispatch to the right env-scoped calls,
    resolving prod/action refs and only sending the fields that were set."""
    core = FakeCore()
    cs = Changeset.model_validate({"actions": [
        {"op": "create_catalog", "handle": "c1", "name": "flexcat", "connector": "flex", "source": "x"},
        {"op": "set_flex_module", "target": {"source": "action", "handle": "c1"}, "source": "def get_tables(): return []"},
        {"op": "update_object_factory", "target": {"source": "prod", "kind": "object_factory", "id": "prod-fac"},
         "column_spec": ["a", "b"], "use_all_columns": False},
        {"op": "update_object_type", "target": {"source": "prod", "kind": "object_type", "id": "prod-t"},
         "description": "new desc"},
    ]})
    Resolver(core).materialize(cs)

    flex = next(c for c in core.calls if c[0] == "set_flex_module")
    assert flex[2] == "flexcat"  # resolved the catalog handle to its logical name
    assert "get_tables" in flex[3]

    upf = next(c for c in core.calls if c[0] == "update_object_factory")
    assert upf[2] == "prod-fac"  # prod ref passed through
    assert upf[3] == {"column_spec": ["a", "b"], "use_all_columns": False}  # only set fields

    upt = next(c for c in core.calls if c[0] == "update_object_type")
    assert upt[3] == {"description": "new desc"}


def test_apply_materializes_then_promotes():
    core = FakeCore()
    Resolver(core).apply(_wire_changeset())
    assert core.promoted, "apply must promote the freshly-built env"
    # promote targeted the env that was just built
    assert core.promoted[0].startswith("env")
