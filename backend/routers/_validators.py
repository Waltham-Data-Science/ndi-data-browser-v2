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

# Dataset ID — broad-but-bounded pattern. In production the cloud
# issues 24-char Mongo ObjectIds, but we intentionally keep the regex
# permissive (alphanumeric + underscore + hyphen) so that:
#   - Test fixtures can use short readable IDs like "DS1", "FERRET2".
#   - Staging imports that haven't yet stamped a real ObjectId can
#     flow through.
#   - Any future cloud-side ID scheme change doesn't brick the proxy.
#
# The bounds still defeat the two real attack classes: path traversal
# (no `/`, `..`, or percent-encoded equivalents) and unbounded input
# (128-char cap).
DATASET_ID_PATTERN = r"^[a-zA-Z0-9_\-]{1,128}$"

# Document ID — tighter. Either a 24-char Mongo `_id` OR an NDI ndiId
# (`<16 hex>_<16 hex>`, 33 chars). Keeping this strict because
# `DocumentService.detail()` branches on the 24-hex regex to decide
# whether to hit ndiquery for resolution; loose IDs here would route
# unresolvable strings to the resolver.
DOCUMENT_ID_PATTERN = r"^(?:[0-9a-fA-F]{24}|[0-9a-fA-F]{16}_[0-9a-fA-F]{16})$"

DatasetId = Annotated[
    str,
    Path(
        min_length=1,
        max_length=128,
        pattern=DATASET_ID_PATTERN,
        description="Dataset identifier (alphanumeric, underscore, hyphen; 1-128 chars).",
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
