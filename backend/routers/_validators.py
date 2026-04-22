"""Shared path-parameter validators for routers.

Every router that accepts `dataset_id` or `document_id` as a path
parameter should use these annotated types instead of plain `str`.
The validators enforce a strict character-set + length bound before
the value flows into:

- an f-string URL path (``f"/datasets/{dataset_id}/..."``) that reaches
  ndi-cloud-node,
- an ndiquery ``param1`` field embedded in a JSON body, or
- a Mongoose ``findById`` cast on the upstream.

The upstream doesn't re-validate path segments (it trusts the proxy),
so the proxy is the sole line of defence against traversal sequences
(``..``, percent-encoded variants), length exhaustion, or unicode
edge cases that break the ndiquery indexer.

Formats accepted
────────────────
``dataset_id``:
    24-char Mongo `_id` hex — `[0-9a-fA-F]{24}`.

``document_id``:
    Either the 24-char Mongo `_id` OR an NDI ndiId of the form
    `<16 hex>_<16 hex>` (33 chars with underscore). Any hit outside
    that union is rejected before we try to resolve.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import Path

# Mongo ObjectId
DATASET_ID_PATTERN = r"^[0-9a-fA-F]{24}$"

# Union of 24-char Mongo hex OR ndiId (16_16 hex with underscore).
# We keep the two alternatives explicit so an obvious malformed value
# (e.g. 40 chars of hex) doesn't sneak through either branch.
DOCUMENT_ID_PATTERN = r"^(?:[0-9a-fA-F]{24}|[0-9a-fA-F]{16}_[0-9a-fA-F]{16})$"

DatasetId = Annotated[
    str,
    Path(
        min_length=24,
        max_length=24,
        pattern=DATASET_ID_PATTERN,
        description="24-character Mongo ObjectId of the dataset.",
    ),
]

DocumentId = Annotated[
    str,
    Path(
        min_length=24,
        max_length=33,
        pattern=DOCUMENT_ID_PATTERN,
        description=(
            "24-character Mongo ObjectId OR NDI ndiId "
            "(`<16 hex>_<16 hex>`, 33 chars with underscore)."
        ),
    ),
]
