"""Thin, env-scoped HTTP client over Core's REST API.

The resolver talks to Core *only* through this interface — every mutation is an
ordinary Core endpoint call carrying ``?env=<id>``. That's the whole point of
the split: staging execution is the same code path as prod, so it can never
behave differently. ``CoreClient`` is a Protocol so tests can drop in a fake.
"""

from __future__ import annotations

from typing import Any, Protocol

import requests


class CoreError(Exception):
    def __init__(self, message: str, status: int | None = None, body: Any = None):
        self.status = status
        self.body = body
        super().__init__(message)


class CoreClient(Protocol):
    def make_env(self, label: str = "") -> str: ...
    def drop_env(self, env: str) -> None: ...
    def promote_env(self, env: str) -> dict: ...
    def conflicts(self, env: str) -> list[dict]: ...
    def create_catalog(
        self, env: str, name: str, connector: str, properties: dict, source: str | None
    ) -> dict: ...
    def update_catalog(self, env: str, name: str, **fields) -> dict: ...
    def delete_catalog(self, env: str, name: str) -> dict: ...
    def set_flex_module(self, env: str, name: str, source: str) -> dict: ...
    def create_object_type(self, env: str, name: str, description: str) -> dict: ...
    def update_object_type(self, env: str, type_id: str, **fields) -> dict: ...
    def delete_object_type(self, env: str, type_id: str) -> dict: ...
    def add_trait(self, env: str, type_id: str, trait: str) -> dict: ...
    def remove_trait(self, env: str, type_id: str, trait: str) -> dict: ...
    def resolve_data_source(
        self, env: str, catalog_logical: str, schema: str, table: str
    ) -> str: ...
    def create_object_factory(self, env: str, **fields) -> dict: ...
    def update_object_factory(self, env: str, factory_id: str, **fields) -> dict: ...
    def delete_object_factory(self, env: str, factory_id: str) -> dict: ...
    def query(self, env: str, body: dict) -> dict: ...


class HttpCoreClient:
    """The real client. ``env`` of ``None``/"" would target prod, but the
    resolver always passes a real env — prod is only ever reached via promote."""

    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # -- low level ---------------------------------------------------------
    def _url(self, path: str, env: str | None) -> str:
        url = f"{self.base_url}{path}"
        if env:
            sep = "&" if "?" in path else "?"
            url = f"{url}{sep}env={env}"
        return url

    def _req(self, method: str, path: str, env: str | None = None, **kw) -> dict:
        try:
            r = requests.request(method, self._url(path, env), timeout=self.timeout, **kw)
        except requests.RequestException as exc:
            raise CoreError(f"could not reach Core: {exc}") from exc
        body: Any
        try:
            body = r.json()
        except ValueError:
            body = r.text
        if r.status_code >= 400:
            raise CoreError(
                f"Core {method} {path} → {r.status_code}", status=r.status_code, body=body
            )
        return body

    # -- environments ------------------------------------------------------
    def make_env(self, label: str = "") -> str:
        return self._req("POST", "/environments", json={"label": label})["id"]

    def drop_env(self, env: str) -> None:
        self._req("DELETE", f"/environments/{env}")

    def promote_env(self, env: str) -> dict:
        return self._req("POST", f"/environments/{env}/promote")

    def conflicts(self, env: str) -> list[dict]:
        return self._req("GET", f"/environments/{env}/conflicts")["conflicts"]

    # -- catalogs ----------------------------------------------------------
    def create_catalog(self, env, name, connector, properties, source):
        body = {"name": name, "connector": connector, "properties": properties or {}}
        if source is not None:
            body["source"] = source
        return self._req("POST", "/catalogs", env=env, json=body)["catalog"]

    def update_catalog(self, env, name, **fields):
        return self._req("PATCH", f"/catalogs/{name}", env=env, json=fields)["catalog"]

    def delete_catalog(self, env, name):
        return self._req("DELETE", f"/catalogs/{name}", env=env)

    def set_flex_module(self, env, name, source):
        return self._req("PUT", f"/flex-modules/{name}", env=env, json={"source": source})

    # -- object types ------------------------------------------------------
    def create_object_type(self, env, name, description):
        return self._req(
            "POST", "/object-types", env=env, json={"name": name, "description": description}
        )

    def update_object_type(self, env, type_id, **fields):
        return self._req("PATCH", f"/object-types/{type_id}", env=env, json=fields)

    def delete_object_type(self, env, type_id):
        return self._req("DELETE", f"/object-types/{type_id}", env=env)

    def add_trait(self, env, type_id, trait):
        return self._req("PUT", f"/object-types/{type_id}/traits/{trait}", env=env)

    def remove_trait(self, env, type_id, trait):
        return self._req("DELETE", f"/object-types/{type_id}/traits/{trait}", env=env)

    # -- data sources / factories -----------------------------------------
    def resolve_data_source(self, env, catalog_logical, schema, table):
        rows = self._req(
            "GET", f"/data-sources?catalog={catalog_logical}", env=env
        )
        for r in rows:
            if r["schema_name"] == schema and r["table_name"] == table:
                return r["id"]
        raise CoreError(
            f"data source {catalog_logical}.{schema}.{table} not found in env {env}"
        )

    def create_object_factory(self, env, **fields):
        return self._req("POST", "/object-factories", env=env, json=fields)

    def update_object_factory(self, env, factory_id, **fields):
        return self._req("PATCH", f"/object-factories/{factory_id}", env=env, json=fields)

    def delete_object_factory(self, env, factory_id):
        return self._req("DELETE", f"/object-factories/{factory_id}", env=env)

    def query(self, env, body):
        return self._req("POST", "/query", env=env, json=body)
