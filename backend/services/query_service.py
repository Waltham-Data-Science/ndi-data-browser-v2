"""NDI query service — validates query DSL and forwards to cloud."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..clients.ndi_cloud import NdiCloudClient
from ..errors import QueryInvalidNegation, ValidationFailed

ALLOWED_OPS = {
    "isa", "depends_on", "or",
    "exact_string", "exact_string_anycase", "contains_string",
    "regexp", "exact_number", "lessthan", "lessthaneq",
    "greaterthan", "greaterthaneq", "hasfield", "hasmember",
    "hasanysubfield_contains_string", "hasanysubfield_exact_string",
    # Negated variants (prefix ~). Server validates elsewhere; we accept them here.
}

ScopeKeyword = Literal["public", "private", "all"]


class QueryNode(BaseModel):
    operation: str = Field(..., min_length=1, max_length=100)
    field: str | None = None
    param1: Any = None
    param2: Any = None

    @field_validator("operation")
    @classmethod
    def _check_op(cls, v: str) -> str:
        base = v.removeprefix("~")
        if base not in ALLOWED_OPS:
            raise ValueError(f"Unknown query operation: {v}")
        if v == "~or":
            raise ValueError("`~or` is not a supported operation.")
        return v


class QueryRequest(BaseModel):
    searchstructure: list[QueryNode]
    scope: str = Field(..., min_length=1, max_length=2048)

    @field_validator("scope")
    @classmethod
    def _check_scope(cls, v: str) -> str:
        if v in ("public", "private", "all"):
            return v
        # CSV of 24-char hex ObjectIds.
        parts = [p.strip() for p in v.split(",") if p.strip()]
        if not parts:
            raise ValueError("scope must be a keyword or comma-separated dataset IDs")
        import re
        if not all(re.fullmatch(r"[a-fA-F0-9]{24}", p) for p in parts):
            raise ValueError("scope dataset IDs must be 24-char hex")
        return ",".join(parts)


class QueryService:
    def __init__(self, cloud: NdiCloudClient) -> None:
        self.cloud = cloud

    async def execute(
        self,
        req: QueryRequest,
        *,
        access_token: str | None,
    ) -> dict[str, Any]:
        # Pre-validate ~or anywhere in the tree.
        for node in _walk(req.searchstructure):
            if node.operation == "~or":
                raise QueryInvalidNegation()

        try:
            return await self.cloud.ndiquery(
                searchstructure=[_node_to_cloud(n) for n in req.searchstructure],
                scope=req.scope,
                access_token=access_token,
            )
        except QueryInvalidNegation:
            raise
        except ValueError as e:
            raise ValidationFailed(str(e)) from e

    async def appears_elsewhere(
        self,
        *,
        document_id: str,
        exclude_dataset_id: str | None,
        access_token: str | None,
    ) -> list[dict[str, Any]]:
        scope = "public" if access_token is None else "all"
        body = await self.cloud.ndiquery(
            searchstructure=[
                {"operation": "depends_on", "param1": "*", "param2": document_id},
            ],
            scope=scope,
            access_token=access_token,
        )
        # Group by datasetId, count. Sample up to 5 doc IDs.
        by_dataset: dict[str, dict[str, Any]] = {}
        for doc in body.get("documents", []):
            ds = doc.get("datasetId") or doc.get("dataset")
            if ds == exclude_dataset_id or not ds:
                continue
            entry = by_dataset.setdefault(ds, {"datasetId": ds, "count": 0, "sampleDocIds": []})
            entry["count"] += 1
            if len(entry["sampleDocIds"]) < 5:
                entry["sampleDocIds"].append(doc.get("id") or doc.get("ndiId"))
        return sorted(by_dataset.values(), key=lambda x: -int(x["count"]))


def _walk(nodes: list[QueryNode]) -> list[QueryNode]:
    out: list[QueryNode] = []
    for n in nodes:
        out.append(n)
        if n.operation in ("or", "~or") and isinstance(n.param1, list):
            out.extend(_walk([QueryNode(**p) for p in n.param1 if isinstance(p, dict)]))
        if n.operation in ("or", "~or") and isinstance(n.param2, list):
            out.extend(_walk([QueryNode(**p) for p in n.param2 if isinstance(p, dict)]))
    return out


def _node_to_cloud(n: QueryNode) -> dict[str, Any]:
    d: dict[str, Any] = {"operation": n.operation}
    if n.field is not None:
        d["field"] = n.field
    if n.param1 is not None:
        d["param1"] = n.param1
    if n.param2 is not None:
        d["param2"] = n.param2
    return d
