"""Locust load test — plan §M7-9.

Spec quality gates:
  * 200 concurrent users, mixed-read workload.
  * p95 < 1s for dataset list.
  * p95 < 3s for combined table (with warm cache).
  * Zero 5xx.

Run against prod::

    locust -f backend/tests/load/locustfile.py \\
      --host https://ndb-v2-production.up.railway.app \\
      --users 200 --spawn-rate 20 --run-time 5m \\
      --headless --csv load-run

Then verify the CSV against the assertions::

    python backend/tests/load/assert_gates.py load-run_stats.csv

Two live datasets are pinned so the proxy's Redis-backed table cache gets
hit repeatedly (plan §M4a-3). Cold-cache runs will blow through the 3s p95
budget — the plan explicitly says "with warm cache". Warm up by running
5-10 iterations sequentially before declaring the gate valid.
"""
from __future__ import annotations

import random

from locust import HttpUser, between, task

# Pinned datasets — Haley + Van Hooser. Dabrowska is empty on prod.
HALEY = "682e7772cdf3f24938176fac"
VAN_HOOSER = "68839b1fbf243809c0800a01"
DATASETS = [HALEY, VAN_HOOSER]

# Name-normalize endpoints with variable path segments so Locust's stats
# aren't split into 1656 separate lines ("one per doc id"). All requests
# below pass `name=` so the CSV report stays small + readable.


class BrowserUser(HttpUser):
    """Read-only anonymous catalog + tables + documents traffic.

    Approximates what a tutorial-following researcher would exercise:
    land on catalog, open one dataset, open the combined table, open a
    document. Ratios roughly match the M4-M5 workflow budgets in
    docs/workflows.md.
    """

    # 1-3 s think-time between pages — realistic for human browsing.
    wait_time = between(1, 3)

    def on_start(self) -> None:
        # Warm the table cache once per simulated user so the later combined
        # + subject table requests don't all pay the cold-build cost.
        for ds in DATASETS:
            self.client.get(
                f"/api/datasets/{ds}/tables/subject",
                name="/api/datasets/{id}/tables/subject (warmup)",
            )

    @task(5)
    def browse_catalog(self) -> None:
        self.client.get("/api/datasets/published?page=1&pageSize=20",
                        name="/api/datasets/published")

    @task(3)
    def open_dataset(self) -> None:
        ds = random.choice(DATASETS)
        self.client.get(f"/api/datasets/{ds}",
                        name="/api/datasets/{id}")
        self.client.get(f"/api/datasets/{ds}/document-class-counts",
                        name="/api/datasets/{id}/document-class-counts")

    @task(2)
    def combined_table(self) -> None:
        ds = random.choice(DATASETS)
        # Combined table is the heaviest table — this is the p95<3s gate.
        self.client.get(f"/api/datasets/{ds}/tables/combined",
                        name="/api/datasets/{id}/tables/combined")

    @task(2)
    def subject_table(self) -> None:
        ds = random.choice(DATASETS)
        self.client.get(f"/api/datasets/{ds}/tables/subject",
                        name="/api/datasets/{id}/tables/subject")

    @task(1)
    def open_document(self) -> None:
        # Listing first, then opening the first id — mirrors the UI flow.
        ds = random.choice(DATASETS)
        r = self.client.get(
            f"/api/datasets/{ds}/documents?class=subject&page=1&pageSize=20",
            name="/api/datasets/{id}/documents?class=subject",
        )
        if r.status_code != 200:
            return
        docs = (r.json() or {}).get("documents") or []
        if not docs:
            return
        doc_id = docs[0].get("id") or docs[0].get("ndiId")
        if not doc_id:
            return
        self.client.get(
            f"/api/datasets/{ds}/documents/{doc_id}",
            name="/api/datasets/{id}/documents/{docId}",
        )

    @task(1)
    def health(self) -> None:
        # Lightweight probe — verifies the whole stack is up, not just
        # the cloud-backed endpoints.
        self.client.get("/api/health/ready", name="/api/health/ready")
