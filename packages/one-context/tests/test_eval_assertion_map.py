from __future__ import annotations

from one_context.eval_lite.assertion_map import (
    ALL_KINDS,
    DEFAULT_DIM_WIN,
    default_link,
    links_for,
)


def test_all_kinds_in_default_dim_win():
    for k in ALL_KINDS:
        assert k in DEFAULT_DIM_WIN, f"{k} missing from DEFAULT_DIM_WIN"


def test_default_dim_win_covers_only_all_kinds():
    for k in DEFAULT_DIM_WIN:
        assert k in ALL_KINDS, f"{k} in DEFAULT_DIM_WIN but not ALL_KINDS"


def test_default_link_known_kind():
    link = default_link("file_exists")
    assert link is not None
    assert link.dimension == "outcome"
    assert link.role == "gate"


def test_default_link_unknown_kind_returns_none():
    assert default_link("bogus_kind") is None


def test_default_link_with_threshold():
    link = default_link("count_compare", threshold=0.9)
    assert link is not None
    assert link.threshold == 0.9


def test_default_link_no_threshold():
    link = default_link("file_exists")
    assert link.threshold is None


def test_links_for_basic():
    specs = [
        {"id": "a1", "kind": "file_exists"},
        {"id": "a2", "kind": "json_valid"},
    ]
    links = links_for(specs)
    assert len(links) == 2
    assert links[0].assertion_id == "a1"
    assert links[1].assertion_id == "a2"


def test_links_for_blocking_forces_gate():
    # count_compare defaults to score; blocking=True should override to gate
    specs = [{"id": "c1", "kind": "count_compare", "blocking": True}]
    links = links_for(specs)
    assert len(links) == 1
    assert links[0].role == "gate"


def test_links_for_non_blocking_keeps_default():
    specs = [{"id": "c1", "kind": "count_compare", "blocking": False}]
    links = links_for(specs)
    assert links[0].role == "score"


def test_links_for_unknown_kind_skipped():
    specs = [
        {"id": "a1", "kind": "file_exists"},
        {"id": "a2", "kind": "unknown"},
    ]
    links = links_for(specs)
    assert len(links) == 1
    assert links[0].assertion_id == "a1"


def test_links_for_empty():
    assert links_for([]) == []