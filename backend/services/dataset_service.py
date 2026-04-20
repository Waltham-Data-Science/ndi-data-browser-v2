"""Dataset list / detail / class-counts — all cloud-backed with TTL caching.

All authenticated cache entries are scoped by a stable per-user identifier
(``user_scope_for(session)``) rather than the 1-bit ``authed`` flag used
prior to PR-3. See ``backend/auth/session.py::user_scope_for`` for the
scope derivation and ``backend/cache/redis_table.py`` for the rationale.

List-with-summary enrichment (Plan B B2)
----------------------------------------

:meth:`DatasetService.list_published_with_summaries` and
:meth:`list_mine_with_summaries` call the cloud's list endpoint and then,
for each row in the page, invoke :class:`DatasetSummaryService` concurrently
(Semaphore 3, same discipline as :mod:`summary_table_service`). The resulting
:class:`CompactDatasetSummary` is attached to each row as ``summary``.

Per-dataset errors do NOT fail the page: a synthesizer timeout or a cloud
404 on one dataset surfaces as ``summary: null`` on that row (the frontend
falls back to raw-record rendering). Rationale — a flaky synthesizer must
never prevent a user from seeing their catalog.

Short-circuit (dormant)
-----------------------

``_compact_summary_from_cloud_fields`` inspects the cloud's list response for
``species``/``brainRegions``/``numberOfSubjects`` fields. These are already
on the Mongoose schema but currently elided by ``DatasetListResult``; see
ndi-cloud-node#15 for the 5-line additive change that would expose them.
When it ships, the enricher short-circuits to cloud-provided fields and
skips the synthesizer fanout entirely — zero breaking change to the v2
response shape, just less compute on our side.
"""
from __future__ import annotations

import asyncio
from typing import Any

from ..auth.session import SessionData, user_scope_for
from ..cache.ttl import ProxyCaches
from ..clients.ndi_cloud import NdiCloudClient
from ..observability.logging import get_logger
from .dataset_summary_service import (
    CompactDatasetSummary,
    CompactDatasetSummaryCitation,
    CompactDatasetSummaryCounts,
    DatasetSummaryService,
    OntologyTerm,
)

log = get_logger(__name__)

# Same discipline as summary_table_service — bounds Lambda concurrency the
# catalog page can trigger. Raising this needs a cloud-side conversation
# about the 1000 concurrent-Lambda account budget.
MAX_CONCURRENT_SUMMARIES = 3


class DatasetService:
    def __init__(self, cloud: NdiCloudClient) -> None:
        self.cloud = cloud

    async def list_published(self, *, page: int, page_size: int) -> dict[str, Any]:
        key = f"published:p{page}:ps{page_size}"
        return await ProxyCaches.datasets_list.get_or_compute(
            key,
            lambda: self.cloud.get_published_datasets(page=page, page_size=page_size),
        )

    async def list_mine(
        self, *, session: SessionData,
    ) -> dict[str, Any]:
        """Aggregate every dataset owned by any org the caller belongs to.

        The cloud's ``/datasets/unpublished`` is a narrow slice (filter:
        ``isPublished=false AND isSubmitted=true``) that hides the common
        cases a scientist expects on their "My org" view — their own
        published work, and drafts they haven't submitted yet. Instead we
        fan out to ``GET /organizations/:orgId/datasets`` for each org in
        ``session.organization_ids`` (captured at login from the cloud's
        ``UserWithOrganizationsResult``) and return the union.

        If the caller has zero orgs on their session, we return the
        empty shape. This is the right answer for admins who aren't
        enrolled in any org — they can still see everything via the
        public catalog; the frontend renders a helpful empty-state.

        Cache key is per-user so two members of the same org don't share
        a cached aggregation (each user's permission filter on the cloud
        side may expose a slightly different set).
        """
        key = f"mine:{user_scope_for(session)}"
        return await ProxyCaches.datasets_list.get_or_compute(
            key,
            lambda: self._aggregate_org_datasets(session=session),
        )

    async def _aggregate_org_datasets(
        self, *, session: SessionData,
    ) -> dict[str, Any]:
        org_ids = list(session.organization_ids)
        if not org_ids:
            return {"totalNumber": 0, "datasets": []}
        # Bounded concurrency on the fan-out — matches the Semaphore(3)
        # discipline `summary_table_service` + the summary-enricher use,
        # and keeps us well under the cloud Lambda account concurrency
        # budget even for users in many orgs.
        sem = asyncio.Semaphore(MAX_CONCURRENT_SUMMARIES)

        async def _one(org_id: str) -> dict[str, Any] | None:
            async with sem:
                try:
                    return await self.cloud.get_organization_datasets(
                        org_id,
                        access_token=session.access_token,
                        page=1,
                        page_size=100,
                    )
                except Exception as e:
                    # Don't fail the whole /my view because one of the
                    # caller's orgs had a transient hiccup — log and skip.
                    log.warning(
                        "list_mine.org_fetch_failed",
                        organization_id=org_id,
                        error=str(e),
                    )
                    return None

        results = await asyncio.gather(*[_one(oid) for oid in org_ids])
        merged: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        total = 0
        for r in results:
            if not isinstance(r, dict):
                continue
            total += int(r.get("totalNumber") or 0)
            for row in r.get("datasets") or []:
                if not isinstance(row, dict):
                    continue
                did = row.get("id")
                if isinstance(did, str):
                    if did in seen_ids:
                        # Shouldn't happen (a dataset belongs to one org)
                        # but defense-in-depth against upstream changes.
                        continue
                    seen_ids.add(did)
                merged.append(row)
        return {"totalNumber": total, "datasets": merged}

    async def detail(
        self, dataset_id: str, *, session: SessionData | None,
    ) -> dict[str, Any]:
        key = f"detail:{dataset_id}:{user_scope_for(session)}"
        access_token = session.access_token if session else None
        return await ProxyCaches.dataset_detail.get_or_compute(
            key,
            lambda: self.cloud.get_dataset(dataset_id, access_token=access_token),
        )

    async def class_counts(
        self, dataset_id: str, *, session: SessionData | None,
    ) -> dict[str, Any]:
        key = f"classcounts:{dataset_id}:{user_scope_for(session)}"
        access_token = session.access_token if session else None
        return await ProxyCaches.class_counts.get_or_compute(
            key,
            lambda: self.cloud.get_document_class_counts(
                dataset_id, access_token=access_token,
            ),
        )

    # --- B2: list-with-summary enrichers ----------------------------------

    async def list_published_with_summaries(
        self,
        *,
        page: int,
        page_size: int,
        summary_service: DatasetSummaryService,
        session: SessionData | None,
    ) -> dict[str, Any]:
        """Catalog list + embedded per-row compact summary.

        Returns the raw cloud response with an extra ``summary`` key on each
        entry in ``datasets``. ``summary`` is ``None`` if:
          - the short-circuit detects no summary-relevant fields AND
          - the synthesizer failed for that dataset (error logged, page
            still renders).
        """
        payload = await self.list_published(page=page, page_size=page_size)
        await self._enrich_list_response(
            payload, summary_service=summary_service, session=session,
        )
        return payload

    async def list_mine_with_summaries(
        self,
        *,
        session: SessionData,
        summary_service: DatasetSummaryService,
    ) -> dict[str, Any]:
        """Authenticated ``/my`` list + embedded per-row compact summary.

        Mirrors :meth:`list_published_with_summaries`; a per-user cache
        scope protects cross-user leakage of the raw list (summaries have
        their own scoping via B1's cache key).
        """
        payload = await self.list_mine(session=session)
        await self._enrich_list_response(
            payload, summary_service=summary_service, session=session,
        )
        return payload

    async def _enrich_list_response(
        self,
        payload: dict[str, Any],
        *,
        summary_service: DatasetSummaryService,
        session: SessionData | None,
    ) -> None:
        """Mutate ``payload['datasets']`` in place, attaching ``summary`` on
        each row. Summaries are built concurrently under Semaphore(3)
        (:data:`MAX_CONCURRENT_SUMMARIES`), with per-dataset errors
        downgraded to ``summary: null`` rather than propagated.
        """
        datasets = payload.get("datasets") or []
        if not isinstance(datasets, list) or not datasets:
            return

        sem = asyncio.Semaphore(MAX_CONCURRENT_SUMMARIES)

        async def _one(row: dict[str, Any]) -> CompactDatasetSummary | None:
            dataset_id = _row_dataset_id(row)
            if not dataset_id:
                return None

            # Short-circuit: if ndi-cloud-node#15 has shipped the
            # DatasetListResult serializer expansion, the cloud already
            # hands us species / brainRegions / numberOfSubjects. Build the
            # compact summary directly from the row — zero extra cloud
            # calls. This branch is dormant until #15 merges.
            short_circuited = _compact_summary_from_cloud_fields(row)
            if short_circuited is not None:
                return short_circuited

            async with sem:
                try:
                    full = await summary_service.build_summary(
                        dataset_id, session=session,
                    )
                except Exception as e:
                    # Any synth failure degrades gracefully. The card falls
                    # back to raw-record rendering; we keep the list usable.
                    log.warning(
                        "catalog.summary_enrichment_failed",
                        dataset_id=dataset_id,
                        error=str(e),
                    )
                    return None
            return CompactDatasetSummary.from_full(full)

        compact_summaries = await asyncio.gather(
            *[_one(row) for row in datasets],
            return_exceptions=False,  # _one swallows its own exceptions
        )
        for row, compact in zip(datasets, compact_summaries, strict=True):
            if isinstance(row, dict):
                row["summary"] = (
                    compact.model_dump(mode="json") if compact is not None else None
                )


def _row_dataset_id(row: dict[str, Any]) -> str | None:
    """Extract the dataset ID from a ``DatasetListResult`` row. ``id`` is the
    canonical key; ``_id`` is the Mongo-native form (appears on detail but
    sometimes leaks through to list endpoints too).
    """
    for key in ("id", "_id", "datasetId"):
        v = row.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def _compact_summary_from_cloud_fields(
    row: dict[str, Any],
) -> CompactDatasetSummary | None:
    """Short-circuit: if the cloud's list response already contains enough
    structured fields, build the compact summary directly. Returns ``None``
    when the cloud hasn't shipped ndi-cloud-node#15 yet, signaling the
    caller to fall through to the synthesizer.

    Heuristic: the cloud-provided fields are strings like
    ``"Rattus norvegicus, Mus musculus"`` (CSV). Without ontology IDs we
    can't render pills with resolver links, but the compact shape marks
    ``ontologyId=None`` which the frontend handles. This is a strictly
    additive branch: the synthesizer's output is always preferred when
    it runs; this one only fires when we detect cloud-side fields AND the
    synthesizer can be skipped.
    """
    has_species = isinstance(row.get("species"), str)
    has_brain_regions = isinstance(row.get("brainRegions"), str)
    has_subjects = isinstance(row.get("numberOfSubjects"), int)
    # Conservative gate: require all three so a partial schema upgrade
    # doesn't drop us into a lossy short-circuit.
    if not (has_species and has_brain_regions and has_subjects):
        return None

    dataset_id = _row_dataset_id(row)
    if not dataset_id:
        return None

    species = _csv_to_ontology_terms(row.get("species"))
    brain_regions = _csv_to_ontology_terms(row.get("brainRegions"))

    title_val = row.get("name")
    title = title_val if isinstance(title_val, str) else dataset_id
    license_val = row.get("license")
    doi_val = row.get("doi")
    created = row.get("createdAt")

    year: int | None = None
    if isinstance(created, str):
        try:
            from datetime import datetime
            year = datetime.fromisoformat(created.replace("Z", "+00:00")).year
        except ValueError:
            year = None

    doc_count = row.get("documentCount")
    total_documents = int(doc_count) if isinstance(doc_count, int) else 0

    return CompactDatasetSummary(
        datasetId=dataset_id,
        counts=CompactDatasetSummaryCounts(
            subjects=int(row["numberOfSubjects"]),
            totalDocuments=total_documents,
        ),
        species=species,
        brainRegions=brain_regions,
        citation=CompactDatasetSummaryCitation(
            title=title,
            license=license_val if isinstance(license_val, str) else None,
            datasetDoi=doi_val if isinstance(doi_val, str) else None,
            year=year,
        ),
    )


def _csv_to_ontology_terms(value: Any) -> list[OntologyTerm] | None:
    """Split a cloud-provided CSV like ``"Rattus norvegicus, Mus musculus"``
    into :class:`OntologyTerm` entries with ``ontologyId=None`` (the cloud
    fields don't carry ontology IDs on the list endpoint — just labels).
    Returns ``None`` for missing/blank values so the card distinguishes
    "cloud hasn't populated this" from "fact genuinely absent".
    """
    if not isinstance(value, str):
        return None
    parts = [p.strip() for p in value.split(",") if p.strip()]
    if not parts:
        return []
    # Dedupe while preserving first-seen order.
    seen: set[str] = set()
    terms: list[OntologyTerm] = []
    for p in parts:
        if p in seen:
            continue
        seen.add(p)
        terms.append(OntologyTerm(label=p, ontologyId=None))
    return terms
