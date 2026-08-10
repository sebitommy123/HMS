"""Timing + baseline harness for the Core query performance suite.

Pure Python, no extra deps. The philosophy is *tracked baselines, non-blocking*:
scenarios never fail the run on a slow number. Instead each scenario records
its measured latency, we compare against a committed baseline, and regressions
surface as warnings + a terminal report. This keeps early, machine-noisy numbers
honest while still catching the "we made it 3x slower" class of change.

Two measurable endpoints are wrapped as `Outcome` producers:
  - `semantic_query` — POST /query (the semantic path; the executor absorbs
    Trino timeouts into result_status.errors[kind=timeout] with HTTP 200).
  - `raw_query`      — POST /raw-trino-query (504 on timeout, 400 on trino error).

`measure()` times wall-clock around N iterations and reduces to p50/p95.
`finalize()` (called from the pytest terminal-summary hook) renders the report,
writes a JSON artifact, and — under PERF_UPDATE_BASELINE — rewrites the baseline.
"""

from __future__ import annotations

import json
import os
import platform
import statistics
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

PERF_DIR = Path(__file__).resolve().parent
CORE_DIR = PERF_DIR.parents[1]  # .../core
BASELINE_PATH = PERF_DIR / "baselines" / "query_latency.json"
RESULTS_DIR = CORE_DIR / ".perf-results"


def _regression_pct() -> float:
    return float(os.environ.get("PERF_REGRESSION_PCT", "30"))


def _regression_min_ms() -> float:
    """Absolute floor: a delta must clear both the pct AND this many ms to count
    as a regression/improvement. Kills sub-second micro-benchmark jitter (a
    200ms→350ms swing is 75% but meaningless); real regressions here are
    seconds."""
    return float(os.environ.get("PERF_REGRESSION_MIN_MS", "250"))


def update_baseline_requested() -> bool:
    return os.environ.get("PERF_UPDATE_BASELINE", "").lower() in ("1", "true", "yes")


def scale_rows() -> int:
    """Row count per large seeded table. Tunable per machine."""
    return int(os.environ.get("PERF_SCALE_ROWS", "2000000"))


# --------------------------------------------------------------------------
# Outcome of a single query attempt (status + server-reported facts)
# --------------------------------------------------------------------------


@dataclass
class Outcome:
    status: str  # ok | timeout | error | degraded
    server_elapsed_s: float | None = None
    rows: int | None = None
    detail: str = ""


def semantic_query(
    client, from_type: str, *, limit: int = 25, timeout_seconds: int = 30
) -> Outcome:
    """Run POST /query and classify. The executor never 5xxs on a query-level
    failure — it returns 200 with result_status.errors — so we inspect the body."""
    resp = client.post(
        "/query",
        json={"from": from_type, "limit": limit, "timeout_seconds": timeout_seconds},
    )
    if resp.status_code != 200:
        body = resp.get_json() or {}
        return Outcome("error", detail=f"HTTP {resp.status_code}: {body.get('error')}")
    body = resp.get_json()
    rs = body.get("result_status", {})
    server = rs.get("elapsed_seconds")
    rows = len(body.get("rows", []))
    errs = rs.get("errors") or []
    if errs:
        kind = errs[0].get("kind")
        status = "timeout" if kind == "timeout" else "error"
        return Outcome(status, server, rows, detail=str(errs[0].get("message", ""))[:200])
    if not rs.get("all_ok", False):
        return Outcome("degraded", server, rows, detail="all_ok=false")
    return Outcome("ok", server, rows)


def register_catalog(client, body: dict) -> Outcome:
    """Register a catalog via POST /catalogs. The reconcile + data-source sync
    runs synchronously inside this call, so timing it measures the sync path.
    201 = ok; anything else is recorded as an error (never raises)."""
    resp = client.post("/catalogs", json=body)
    if resp.status_code == 201:
        return Outcome("ok")
    b = resp.get_json() or {}
    return Outcome("error", detail=f"HTTP {resp.status_code}: {b.get('error')}")


def raw_query(
    client, sql: str, *, timeout_seconds: int = 30, max_rows: int = 10_000
) -> Outcome:
    """Run POST /raw-trino-query and classify (504=timeout, 400=trino_error)."""
    resp = client.post(
        "/raw-trino-query",
        json={"sql": sql, "timeout_seconds": timeout_seconds, "max_rows": max_rows},
    )
    if resp.status_code == 200:
        body = resp.get_json()
        return Outcome("ok", body.get("elapsed_seconds"), body.get("row_count"))
    body = resp.get_json() or {}
    if resp.status_code == 504 or body.get("error") == "timeout":
        return Outcome("timeout", detail=str(body.get("details", ""))[:200])
    return Outcome("error", detail=f"HTTP {resp.status_code}: {body.get('error')}")


# --------------------------------------------------------------------------
# Measurement + registry
# --------------------------------------------------------------------------


@dataclass
class Measurement:
    name: str
    status: str
    p50_ms: float | None
    p95_ms: float | None
    server_elapsed_s: float | None
    rows: int | None
    iterations: int
    budget_ms: float
    detail: str = ""

    def to_baseline(self) -> dict:
        return {
            "status": self.status,
            "p50_ms": _round(self.p50_ms),
            "p95_ms": _round(self.p95_ms),
            "server_elapsed_s": _round(self.server_elapsed_s, 3),
            "rows": self.rows,
            "budget_ms": self.budget_ms,
        }


# Scenarios append here; the terminal-summary hook drains it.
_RECORDS: list[Measurement] = []


def records() -> list[Measurement]:
    return _RECORDS


def measure(
    name: str,
    budget_ms: float,
    fn: Callable[[], Outcome],
    *,
    warmup: int = 1,
    iterations: int = 5,
) -> Measurement:
    """Time `fn` (an Outcome producer) over warmup+iterations, reduce to p50/p95.

    Never raises. A non-ok outcome (timeout/error/degraded) is recorded from the
    first occurrence and short-circuits the remaining iterations — there's no
    point running a 30s timeout five times."""
    samples: list[float] = []
    status = "ok"
    detail = ""
    server_elapsed: float | None = None
    rows: int | None = None

    for i in range(warmup + iterations):
        t0 = time.perf_counter()
        try:
            outcome = fn()
        except Exception as exc:  # defensive: a scenario bug must not abort the suite
            elapsed_ms = (time.perf_counter() - t0) * 1000
            samples.append(elapsed_ms)
            status, detail = "error", f"{type(exc).__name__}: {exc}"
            break
        elapsed_ms = (time.perf_counter() - t0) * 1000

        server_elapsed = outcome.server_elapsed_s
        rows = outcome.rows
        if outcome.status != "ok":
            # Record this (possibly warmup) sample so slow/failed scenarios still
            # report a latency, then stop — don't pay the cost N more times.
            samples.append(elapsed_ms)
            status, detail = outcome.status, outcome.detail
            break
        if i >= warmup:
            samples.append(elapsed_ms)

    m = Measurement(
        name=name,
        status=status,
        p50_ms=statistics.median(samples) if samples else None,
        p95_ms=_percentile(samples, 95) if samples else None,
        server_elapsed_s=server_elapsed,
        rows=rows,
        iterations=len(samples),
        budget_ms=budget_ms,
        detail=detail,
    )
    _RECORDS.append(m)
    return m


# --------------------------------------------------------------------------
# Baseline load / compare / report
# --------------------------------------------------------------------------


@dataclass
class Comparison:
    measurement: Measurement
    verdict: str  # new | ok | improved | regressed | over_budget
    delta_pct: float | None
    note: str = ""


def load_baseline() -> dict:
    if BASELINE_PATH.exists():
        try:
            return json.loads(BASELINE_PATH.read_text())
        except Exception:
            return {}
    return {}


def _compare_one(m: Measurement, baseline: dict, pct: float) -> Comparison:
    scenarios = baseline.get("scenarios", {})
    base = scenarios.get(m.name)
    over_budget = m.p95_ms is not None and m.p95_ms > m.budget_ms

    if base is None:
        note = "new scenario — recorded"
        if over_budget or m.status != "ok":
            note = f"new scenario; over budget/status={m.status}"
        return Comparison(m, "new", None, note)

    # A status regression (was ok, now slow/failed) is the loudest signal.
    if base.get("status") == "ok" and m.status != "ok":
        return Comparison(m, "regressed", None, f"status ok → {m.status}")

    base_p95 = base.get("p95_ms")
    if base_p95 and m.p95_ms:
        delta = (m.p95_ms - base_p95) / base_p95 * 100.0
        abs_delta = m.p95_ms - base_p95
        floor = _regression_min_ms()
        # Require both relative AND absolute significance to flag.
        if delta > pct and abs_delta > floor:
            return Comparison(m, "regressed", delta, f"p95 +{delta:.0f}% vs baseline")
        if delta < -pct and -abs_delta > floor:
            return Comparison(m, "improved", delta, f"p95 {delta:.0f}% vs baseline")
        verdict = "over_budget" if over_budget else "ok"
        return Comparison(m, verdict, delta, "over SLO budget" if over_budget else "")

    verdict = "over_budget" if over_budget else "ok"
    return Comparison(m, verdict, None, "over SLO budget" if over_budget else "")


def _host_meta() -> dict:
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "scale_rows": scale_rows(),
    }


def finalize(write_line: Callable[[str], None]) -> None:
    """Render the report, persist a JSON artifact, and (optionally) rewrite the
    baseline. Called once from pytest_terminal_summary. `write_line` is the
    terminalreporter's line writer."""
    if not _RECORDS:
        return
    baseline = load_baseline()
    pct = _regression_pct()
    comparisons = [_compare_one(m, baseline, pct) for m in _RECORDS]

    write_line("")
    write_line("Query performance — tracked baselines (non-blocking)")
    write_line(f"  host: {_host_meta()['platform']} · scale_rows={scale_rows()}")
    header = f"  {'scenario':<38} {'status':<9} {'p50':>8} {'p95':>8} {'budget':>8}  verdict"
    write_line(header)
    write_line("  " + "-" * (len(header) - 2))
    for c in comparisons:
        m = c.measurement
        write_line(
            f"  {m.name:<38} {m.status:<9} "
            f"{_fmt_ms(m.p50_ms):>8} {_fmt_ms(m.p95_ms):>8} {_fmt_ms(m.budget_ms):>8}  "
            f"{c.verdict}{(' — ' + c.note) if c.note else ''}"
        )

    regressions = [c for c in comparisons if c.verdict == "regressed"]
    for c in regressions:
        warnings.warn(
            f"[perf] {c.measurement.name} regressed: {c.note}", stacklevel=1
        )
    if regressions:
        write_line("")
        write_line(f"  ⚠ {len(regressions)} regression(s) — non-blocking, see warnings")

    _write_artifact(comparisons)

    if update_baseline_requested():
        _write_baseline()
        write_line(f"  ✓ baseline updated → {BASELINE_PATH.relative_to(CORE_DIR)}")


def _write_artifact(comparisons: list[Comparison]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": _host_meta(),
        "scenarios": {
            c.measurement.name: {
                **c.measurement.to_baseline(),
                "verdict": c.verdict,
                "delta_pct": _round(c.delta_pct, 1),
                "note": c.note,
                "detail": c.measurement.detail,
            }
            for c in comparisons
        },
    }
    (RESULTS_DIR / "last-run.json").write_text(json.dumps(payload, indent=2))


def _write_baseline() -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": _host_meta(),
        "scenarios": {m.name: m.to_baseline() for m in _RECORDS},
    }
    BASELINE_PATH.write_text(json.dumps(payload, indent=2) + "\n")


# --------------------------------------------------------------------------
# small numeric helpers
# --------------------------------------------------------------------------


def _percentile(samples: list[float], pct: float) -> float | None:
    if not samples:
        return None
    s = sorted(samples)
    if len(s) == 1:
        return s[0]
    k = (pct / 100.0) * (len(s) - 1)
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return s[lo] * (1 - frac) + s[hi] * frac


def _round(v: float | None, ndigits: int = 1) -> float | None:
    return round(v, ndigits) if isinstance(v, (int, float)) else None


def _fmt_ms(v: float | None) -> str:
    return f"{v:.0f}ms" if isinstance(v, (int, float)) else "—"
