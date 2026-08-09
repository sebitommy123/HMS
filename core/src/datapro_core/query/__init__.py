"""Semantic query engine.

Translates canonical-JSON queries against ObjectTypes into a single
Trino SQL statement that UNION ALL CORRESPONDINGs every ObjectFactory
that produces the requested type. Each branch tags its rows with a
synthetic ``_datasource`` column so the client can trace each row's
provenance.

The output is intentionally a thin tabular shape (columns + rows + a
result_status block) — the actual heavy lifting stays in Trino.
"""
