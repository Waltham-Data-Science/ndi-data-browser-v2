#!/usr/bin/env python3
"""Assert the plan §M7-9 quality gates against a Locust run's CSV.

Usage:
    python backend/tests/load/assert_gates.py path/to/load-run_stats.csv

Exit code 0 on pass, 1 on fail. Fails fast on first violation so the
output stays readable.

Gates (from plan §M7-9):
  * Zero 5xx responses.
  * p95 < 1000 ms for the dataset-list endpoint.
  * p95 < 3000 ms for the combined-table endpoint (warm cache).

Other endpoints are reported but not gated — adding more gates means
more deploy noise when cloud latency drifts.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

GATES: dict[str, int] = {
    "/api/datasets/published": 1_000,
    "/api/datasets/{id}/tables/combined": 3_000,
}


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: assert_gates.py <stats.csv>", file=sys.stderr)
        sys.exit(2)
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"no such file: {path}", file=sys.stderr)
        sys.exit(2)

    failures: list[str] = []
    total_5xx = 0
    saw_gate: set[str] = set()

    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("Name", "").strip()
            # Locust aggregate row key is "Aggregated"; skip to count 5xx later.
            if name == "Aggregated":
                # The CSV has a "Failure Count" but not a 5xx-specific count —
                # we treat any failure as a gate violation.
                fails = int(row.get("Failure Count") or 0)
                if fails:
                    failures.append(f"Aggregated: {fails} failed requests (zero-5xx gate)")
                continue

            p95_raw = row.get("95%") or row.get("95%ile") or "0"
            try:
                p95_ms = float(p95_raw)
            except ValueError:
                continue

            if name in GATES:
                saw_gate.add(name)
                budget = GATES[name]
                if p95_ms > budget:
                    failures.append(
                        f"{name}: p95 = {p95_ms:.0f} ms, budget {budget} ms",
                    )
                else:
                    print(f"  OK: {name}  p95 = {p95_ms:.0f} ms  (budget {budget})")

            # Count 5xx per endpoint if the CSV includes it (Locust >= 2.x
            # dropped per-status breakdowns from the default stats.csv — if
            # they're missing we rely on the Aggregated Failure Count above).
            errors = int(row.get("Failure Count") or 0)
            total_5xx += errors

    missing = [name for name in GATES if name not in saw_gate]
    for m in missing:
        failures.append(f"{m}: no samples in stats.csv — did the endpoint run?")

    if failures:
        print("\nFAIL:")
        for line in failures:
            print(f"  - {line}")
        sys.exit(1)

    print(f"\nPASS: all gates satisfied; {total_5xx} total failed requests.")


if __name__ == "__main__":
    main()
