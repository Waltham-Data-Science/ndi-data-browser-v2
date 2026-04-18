"""DependencyGraphService — upstream / downstream BFS helpers."""
from __future__ import annotations

from backend.services.dependency_graph_service import (
    MAX_DEPTH_HARD_CAP,
    _add_node,
    _class_name,
    _deduplicate_edges,
    _dep_graph_key,
    _depends_on_edges,
    _doc_name,
    _empty_graph,
    _ndi_id,
)


class TestDependsOnEdges:
    def test_dict_shape(self) -> None:
        doc = {"data": {"depends_on": {"name": "element_id", "value": "ELEM1"}}}
        assert _depends_on_edges(doc) == [{"name": "element_id", "value": "ELEM1"}]

    def test_list_shape_filters_empties(self) -> None:
        doc = {"data": {"depends_on": [
            {"name": "subject_id", "value": "SUBJ"},
            {"name": "openminds", "value": ""},
            {"name": "bad", "value": None},
        ]}}
        assert _depends_on_edges(doc) == [{"name": "subject_id", "value": "SUBJ"}]

    def test_missing_returns_empty_list(self) -> None:
        assert _depends_on_edges({}) == []
        assert _depends_on_edges(None) == []
        assert _depends_on_edges({"data": {}}) == []

    def test_default_edge_name(self) -> None:
        doc = {"data": {"depends_on": [{"value": "X"}]}}
        assert _depends_on_edges(doc) == [{"name": "depends_on", "value": "X"}]


class TestExtractors:
    def test_ndi_id_from_base_or_top_level(self) -> None:
        assert _ndi_id({"data": {"base": {"id": "A"}}}) == "A"
        assert _ndi_id({"ndiId": "B"}) == "B"
        assert _ndi_id({}) is None

    def test_class_name_fallback_chain(self) -> None:
        doc = {"data": {"document_class": {"class_name": "subject"}}}
        assert _class_name(doc) == "subject"
        assert _class_name({"className": "explicit"}) == "explicit"

    def test_doc_name_fallback(self) -> None:
        assert _doc_name({"name": "hello"}) == "hello"
        assert _doc_name({"data": {"base": {"name": "base_name"}}}) == "base_name"
        assert _doc_name({}) == ""


class TestAddNode:
    def test_inserts_new_node(self) -> None:
        nodes: dict = {}
        _add_node(nodes, ndi_id="A", mongo_id="m1", name="NodeA", class_name="subject")
        assert nodes["A"] == {
            "id": "m1", "ndiId": "A", "name": "NodeA", "className": "subject", "isTarget": False,
        }

    def test_preserves_is_target_on_revisit(self) -> None:
        nodes: dict = {}
        _add_node(nodes, ndi_id="A", mongo_id="m1", name="", class_name="subject", is_target=True)
        _add_node(nodes, ndi_id="A", mongo_id="m1", name="fuller", class_name="")
        assert nodes["A"]["isTarget"] is True
        # Richer name overwrites empty.
        assert nodes["A"]["name"] == "fuller"

    def test_promotes_to_target_on_revisit(self) -> None:
        nodes: dict = {}
        _add_node(nodes, ndi_id="A", mongo_id="m1", name="n", class_name="c")
        _add_node(nodes, ndi_id="A", mongo_id="m1", name="n", class_name="c", is_target=True)
        assert nodes["A"]["isTarget"] is True


class TestDeduplicateEdges:
    def test_collapses_identical_triples(self) -> None:
        edges = [
            {"source": "A", "target": "B", "direction": "upstream", "label": "element_id"},
            {"source": "A", "target": "B", "direction": "upstream", "label": "element_id"},
            {"source": "A", "target": "B", "direction": "downstream", "label": "depends_on"},
        ]
        out = _deduplicate_edges(edges)
        assert len(out) == 2

    def test_preserves_first_seen_label(self) -> None:
        edges = [
            {"source": "A", "target": "B", "direction": "upstream", "label": "element_id"},
            {"source": "A", "target": "B", "direction": "upstream", "label": "other"},
        ]
        out = _deduplicate_edges(edges)
        assert out == [
            {"source": "A", "target": "B", "direction": "upstream", "label": "element_id"},
        ]


class TestCacheKey:
    def test_key_shape(self) -> None:
        from backend.cache.redis_table import RedisTableCache
        v = RedisTableCache.SCHEMA_VERSION
        assert (
            _dep_graph_key("DS1", "DOC1", 3, user_scope="public")
            == f"depgraph:{v}:DS1:DOC1:3:public"
        )
        assert (
            _dep_graph_key(
                "DS1", "DOC1", 3, user_scope="u:cafebabecafebabe",
            )
            == f"depgraph:{v}:DS1:DOC1:3:u:cafebabecafebabe"
        )


class TestEmptyGraph:
    def test_shape_with_reason(self) -> None:
        out = _empty_graph("DOC", reason="target missing ndiId")
        assert out["target_id"] == "DOC"
        assert out["nodes"] == []
        assert out["edges"] == []
        assert out["error"] == "target missing ndiId"


def test_max_depth_hard_cap_locked_at_3() -> None:
    """Guard against accidental raise — cloud cost scales super-linearly."""
    assert MAX_DEPTH_HARD_CAP == 3
