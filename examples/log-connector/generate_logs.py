#!/usr/bin/env python3
"""Generate dummy logs in `logs/<app>/<YYYY-MM-DD>.log`.

Each file is plain text, one record per line, format:
    <ISO-8601 timestamp>,<message>

Three apps, three days each. Realistic-ish messages so queries have something
to find."""

from __future__ import annotations

import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

random.seed(42)

OUT = Path(__file__).resolve().parent / "data"

APPS = {
    "checkout":  ["card declined", "payment ok", "fraud check passed", "fraud check FAILED", "session expired"],
    "search":    ["query=shoes", "query=laptop", "indexer lag 12s", "cache miss", "result count 0"],
    "inventory": ["sku=ABC-123 in stock", "sku=XYZ-999 OUT of stock", "stock count refreshed", "warehouse sync done", "DB connection reset"],
}

DAYS = ["2025-04-09", "2025-04-10", "2025-04-11"]


def gen_day(app: str, day: str) -> list[str]:
    base = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    n = random.randint(8, 15)
    lines = []
    msgs = APPS[app]
    cursor = base
    for _ in range(n):
        cursor += timedelta(seconds=random.randint(60, 7200))
        msg = random.choice(msgs)
        lines.append(f"{cursor.isoformat()},{msg}")
    return lines


def main() -> None:
    if OUT.exists():
        for p in sorted(OUT.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                p.rmdir()
    OUT.mkdir(parents=True, exist_ok=True)

    total = 0
    for app in APPS:
        (OUT / app).mkdir(exist_ok=True)
        for day in DAYS:
            lines = gen_day(app, day)
            (OUT / app / f"{day}.log").write_text("\n".join(lines) + "\n")
            total += len(lines)
            print(f"  {app}/{day}.log  ({len(lines)} lines)")

    print(f"\nWrote {total} log lines across {len(APPS)} apps × {len(DAYS)} days into {OUT}")


if __name__ == "__main__":
    main()
