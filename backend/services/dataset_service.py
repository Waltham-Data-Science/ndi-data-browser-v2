"""Dataset list / detail / class-counts — all cloud-backed with TTL caching.

All authenticated cache entries are scoped by a stable per-user identifier
(``user_scope_for(session)``) rather than the 1-bit ``authed`` flag used
prior to PR-3. See ``backend/auth/session.py::user_scope_for`` for the
scope derivation and ``backend/cache/redis_table.py`` for the rationale.
"""
from __future__ import annotations

from typing import Any

from ..auth.session import SessionData, user_scope_for
from ..cache.ttl import ProxyCaches
from ..clients.ndi_cloud import NdiCloudClient


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
        # User-scoped — do not share across users. `user_scope_for` hashes the
        # user_id so the key string carries no PII even in redis debugging.
        key = f"mine:{user_scope_for(session)}"
        return await ProxyCaches.datasets_list.get_or_compute(
            key,
            lambda: self.cloud.get_my_datasets(access_token=session.access_token),
        )

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
