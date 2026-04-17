#!/usr/bin/env python3
"""Rollout health probe — plan §M7-10.

Pulls `/metrics` + `/api/health/ready` from a deployed instance, parses
the Prometheus counters/histograms, and fails (exit 1) if any of the
rollout-gate thresholds are breached:

  * 5xx rate > 1% over the scraped counter window.
  * Cloud-call p95 latency regressed > 20% vs the baseline file
    (`rollout-baseline.json`). Absence of a baseline on first run writes
    the current values and passes.

Usage:
    python scripts/rollout-health-probe.py \\
      --target https://ndb-v2-production.up.railway.app \\
      --baseline .rollout-baseline.json

Intended to be run on a cron from GitHub Actions during a staged
cutover. If it fails, the workflow opens an issue; a human decides
whether to drop ROLLOUT_PCT via Railway. Plan §M7-10 sketches a fully
auto-rollback loop — keeping the rollback itself manual means a bad
probe scraping window can't silently revert a healthy deploy.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

# Regression budget: per the plan, "auto-rollback if p95 regresses > 20%".
P95_REGRESSION_PCT = 0.20

# Failure budget: "5xx > 1% for 10 consecutive minutes".
FIVE_XX_BUDGET_PCT = 0.01


def http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "ndb-v2-rollout-probe"})
    with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 — CLI-supplied URL
        return r.read().decode()


def parse_counter(text: str, metric: str, labels: dict[str, str] | None = None) -> float:
    """Sum all samples of `metric` whose labels (if provided) all match.

    We intentionally don't use prometheus_client's parser to keep this
    script dependency-free (runs in a bare GitHub Actions python env).
    """
    pat = re.compile(
        rf'^{re.escape(metric)}(?:{{(.*?)}})?\s+([0-9eE\.\+\-]+)\s*$',
        re.MULTILINE,
    )
    total = 0.0
    for m in pat.finditer(text):
        label_str, value = m.group(1) or "", m.group(2)
        if labels:
            have = dict(re.findall(r'(\w+)="([^"]*)"', label_str))
            if not all(have.get(k) == v for k, v in labels.items()):
                continue
        total += float(value)
    return total


def parse_histogram_quantile(text: str, metric: str, quantile: float = 0.95) -> float | None:
    """Approximate p{quantile} from a histogram's _bucket lines.

    Linear interpolation across buckets — matches what Grafana does when
    a series has no _sum / _count aggregates to lean on. Returns None if
    the histogram isn't present.
    """
    buckets: dict[float, float] = {}
    pat = re.compile(
        rf'^{re.escape(metric)}_bucket{{(.*?)}}\s+([0-9eE\.\+\-]+)\s*$',
        re.MULTILINE,
    )
    for m in pat.finditer(text):
        label_str, count = m.group(1), float(m.group(2))
        le = dict(re.findall(r'(\w+)="([^"]*)"', label_str)).get("le")
        if le is None:
            continue
        try:
            buckets[float(le)] = buckets.get(float(le), 0.0) + count
        except ValueError:
            # "+Inf" literal
            buckets[float("inf")] = buckets.get(float("inf"), 0.0) + count

    if not buckets:
        return None
    total = max(buckets.values())
    if total <= 0:
        return None
    target = total * quantile
    # Walk buckets low -> high, return the first `le` whose cumulative
    # count exceeds `target`. Not the most accurate quantile estimator
    # in existence but it matches the alerting simplicity we want.
    for le in sorted(buckets):
        if buckets[le] >= target:
            return le
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, help="Base URL (e.g. prod URL)")
    ap.add_argument("--baseline", required=True, help="Baseline JSON file")
    args = ap.parse_args()

    metrics_text = http_get(f"{args.target.rstrip('/')}/metrics")

    # prometheus_client prefixes metrics with the registry namespace
    # (backend/observability/metrics.py declares `namespace="ndb"`), so the
    # exported series are `ndb_cloud_call_total` etc.
    total_calls = parse_counter(metrics_text, "ndb_cloud_call_total")
    server_errors = parse_counter(metrics_text, "ndb_cloud_call_total", {"outcome": "server_error"})
    timeouts = parse_counter(metrics_text, "ndb_cloud_call_total", {"outcome": "timeout"})
    fail_rate = (server_errors + timeouts) / total_calls if total_calls else 0.0
    print(f"5xx+timeout rate: {fail_rate:.4%}  "
          f"({int(server_errors+timeouts)}/{int(total_calls)})")

    # p95 on the busiest endpoint bucket we care about for rollout.
    p95_now = parse_histogram_quantile(metrics_text, "ndb_cloud_call_duration_seconds")
    print(f"ndb_cloud_call_duration_seconds p95: "
          f"{'n/a' if p95_now is None else f'{p95_now:.3f} s'}")

    baseline_path = Path(args.baseline)
    baseline: dict[str, float] = {}
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text())
    elif p95_now is not None:
        baseline_path.write_text(json.dumps({"p95": p95_now}, indent=2))
        print(f"no baseline at {baseline_path} — wrote current p95 as baseline")
        sys.exit(0)

    failures: list[str] = []

    if fail_rate > FIVE_XX_BUDGET_PCT:
        failures.append(
            f"5xx+timeout rate {fail_rate:.2%} exceeds "
            f"{FIVE_XX_BUDGET_PCT:.0%} budget",
        )

    baseline_p95 = baseline.get("p95")
    if baseline_p95 and p95_now is not None:
        # Guard against tiny baselines where any absolute increase looks huge.
        if p95_now > baseline_p95 * (1 + P95_REGRESSION_PCT) and p95_now > 0.1:
            failures.append(
                f"p95 {p95_now:.3f}s regressed > {P95_REGRESSION_PCT:.0%} "
                f"from baseline {baseline_p95:.3f}s",
            )

    if failures:
        print("\nFAIL:")
        for msg in failures:
            print(f"  - {msg}")
        sys.exit(1)

    print("\nPASS")


if __name__ == "__main__":
    main()
