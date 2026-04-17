#!/usr/bin/env python3
"""Refresh pinned E2E response fixtures from prod.

Regenerates `frontend/tests-e2e/_fixtures/responses/*.json` from the live
prod deployment. Drops the bulky `documents` and `files` arrays out of
dataset detail — the frontend only consumes metadata fields from the
detail response, and those arrays push the fixture past 5 MB each.

Usage:
    make fixtures-refresh
    # or:
    python3 scripts/refresh-e2e-fixtures.py

Set BASE_URL to hit a different deployment (e.g. a preview env).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("BASE_URL", "https://ndb-v2-production.up.railway.app")
OUT_DIR = Path(__file__).resolve().parents[1] / "frontend/tests-e2e/_fixtures/responses"

HALEY = "682e7772cdf3f24938176fac"
VH = "68839b1fbf243809c0800a01"


def fetch(path: str) -> dict:
    url = f"{BASE_URL}{path}"
    print(f"  GET {url}", file=sys.stderr)
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 — static BASE_URL
        return json.load(resp)


def trim_detail(d: dict) -> dict:
    """The UI consumes metadata only; the bulk arrays are noise."""
    d = dict(d)
    d["documents"] = []
    d["files"] = []
    return d


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Refreshing fixtures against {BASE_URL}", file=sys.stderr)

    pairs: list[tuple[str, dict]] = [
        ("datasets-published.json", fetch("/api/datasets/published?page=1&pageSize=20")),
        ("haley-detail.json",       trim_detail(fetch(f"/api/datasets/{HALEY}"))),
        ("vh-detail.json",          trim_detail(fetch(f"/api/datasets/{VH}"))),
        ("haley-classcounts.json",  fetch(f"/api/datasets/{HALEY}/document-class-counts")),
        ("vh-classcounts.json",     fetch(f"/api/datasets/{VH}/document-class-counts")),
    ]

    for name, data in pairs:
        out = OUT_DIR / name
        out.write_text(json.dumps(data, indent=2))
        print(f"  wrote {out.relative_to(OUT_DIR.parents[3])}  ({out.stat().st_size:,} bytes)",
              file=sys.stderr)


if __name__ == "__main__":
    main()
