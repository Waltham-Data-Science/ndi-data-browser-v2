"""Dataset list / detail / class-counts — all cloud-backed with TTL caching."""
from __future__ import annotations

from typing import Any

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

    async def list_mine(self, *, access_token: str, user_id: str) -> dict[str, Any]:
        # User-scoped — do not share across users.
        key = f"mine:{user_id}"
        return await ProxyCaches.datasets_list.get_or_compute(
            key,
            lambda: self.cloud.get_my_datasets(access_token=access_token),
        )

    async def detail(
        self, dataset_id: str, *, access_token: str | None,
    ) -> dict[str, Any]:
        key = f"detail:{dataset_id}:{'pub' if access_token is None else 'priv'}"
        return await ProxyCaches.dataset_detail.get_or_compute(
            key,
            lambda: self.cloud.get_dataset(dataset_id, access_token=access_token),
        )

    async def class_counts(
        self, dataset_id: str, *, access_token: str | None,
    ) -> dict[str, Any]:
        key = f"classcounts:{dataset_id}:{'pub' if access_token is None else 'priv'}"
        return await ProxyCaches.class_counts.get_or_compute(
            key,
            lambda: self.cloud.get_document_class_counts(dataset_id, access_token=access_token),
        )
