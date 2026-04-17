"""SQLite-backed ontology term cache.

This is the ONLY SQLite file v2 writes — small (<10 MB), ephemeral, unrelated to
dataset storage. Warms as users browse. If the disk is wiped, no data is lost;
the cache re-populates from the providers.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any

from ..config import get_settings
from ..observability.logging import get_logger
from ..observability.metrics import (
    ontology_cache_hits_total,
    ontology_cache_misses_total,
)

log = get_logger(__name__)


@dataclass(slots=True)
class OntologyTerm:
    provider: str
    term_id: str
    label: str | None
    definition: str | None
    url: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "termId": self.term_id,
            "label": self.label,
            "definition": self.definition,
            "url": self.url,
        }


class OntologyCache:
    def __init__(self, db_path: str | None = None, ttl_days: int | None = None) -> None:
        settings = get_settings()
        self.db_path = db_path or settings.ONTOLOGY_CACHE_DB_PATH
        self.ttl_seconds = (ttl_days or settings.ONTOLOGY_CACHE_TTL_DAYS) * 86400
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ontology_terms (
                    provider TEXT NOT NULL,
                    term_id TEXT NOT NULL,
                    payload TEXT,
                    fetched_at INTEGER NOT NULL,
                    PRIMARY KEY (provider, term_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ontology_fetched ON ontology_terms(fetched_at)")
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def get(self, provider: str, term_id: str) -> OntologyTerm | None:
        with self._lock, self._conn() as conn:
            row = conn.execute(
                "SELECT payload, fetched_at FROM ontology_terms WHERE provider = ? AND term_id = ?",
                (provider, term_id),
            ).fetchone()
        if row is None:
            ontology_cache_misses_total.labels(provider=provider).inc()
            return None
        payload, fetched_at = row
        if time.time() - fetched_at > self.ttl_seconds:
            ontology_cache_misses_total.labels(provider=provider).inc()
            return None
        try:
            data = json.loads(payload) if payload else None
        except (TypeError, ValueError):
            ontology_cache_misses_total.labels(provider=provider).inc()
            return None
        ontology_cache_hits_total.labels(provider=provider).inc()
        if data is None:
            return OntologyTerm(provider=provider, term_id=term_id, label=None, definition=None, url=None)
        # Payload was stored via to_dict() which uses camelCase `termId`; the
        # dataclass ctor takes snake_case `term_id`. Translate, tolerating
        # legacy rows that might have been stored with snake_case.
        return OntologyTerm(
            provider=data.get("provider", provider),
            term_id=data.get("termId") or data.get("term_id") or term_id,
            label=data.get("label"),
            definition=data.get("definition"),
            url=data.get("url"),
        )

    def set(self, term: OntologyTerm) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO ontology_terms (provider, term_id, payload, fetched_at) VALUES (?, ?, ?, ?)",
                (term.provider, term.term_id, json.dumps(term.to_dict()), int(time.time())),
            )
            conn.commit()

    def stats(self) -> dict[str, Any]:
        with self._lock, self._conn() as conn:
            count = conn.execute("SELECT COUNT(*) FROM ontology_terms").fetchone()[0]
            by_provider = conn.execute(
                "SELECT provider, COUNT(*) FROM ontology_terms GROUP BY provider"
            ).fetchall()
        return {"total": count, "byProvider": dict(by_provider)}
