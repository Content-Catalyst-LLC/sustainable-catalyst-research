"""Research public graph analysis; only reviewed nodes and relations are traversed."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research-api"))
from app.graph_analysis import (
    analyze_public_graph, neighborhood_public_graph, shortest_public_path,
)


def graph():
    return {
        "nodes": [
            {"id":"a", "kind":"dataset", "title":"Published dataset"},
            {"id":"b", "kind":"figure", "title":"Reviewed figure"},
            {"id":"c", "kind":"source", "title":"Independent source"},
        ],
        "edges": [
            {"source":"a","target":"b","relation":"derived-from","note":"Reviewed lineage"},
        ],
    }


def test_graph_summary_reports_structure_not_truth():
    report=analyze_public_graph(graph())
    assert report["nodes"] == 3
    assert report["edges"] == 1
    assert report["connected_components"] == 2
    assert report["isolated_nodes"] == 1
    assert report["object_kinds"] == {"dataset":1,"figure":1,"source":1}
    assert report["relation_types"] == {"derived-from":1}
    assert "not establish scientific validity" in report["interpretation"]


def test_neighborhood_returns_only_existing_links():
    near=neighborhood_public_graph(graph(),"a",depth=1)
    assert {x["id"] for x in near["nodes"]}=={"a","b"}
    assert len(near["edges"])==1
    assert neighborhood_public_graph(graph(),"c",depth=1)["edges"]==[]
    assert neighborhood_public_graph(graph(),"private-id") is None


def test_shortest_path_keeps_reviewed_direction_metadata():
    path=shortest_public_path(graph(),"b","a")
    assert [x["id"] for x in path["nodes"]]==["b","a"]
    assert path["edges"][0]["source"]=="a"
    assert path["length"]==1
    assert shortest_public_path(graph(),"a","c") is None
    assert shortest_public_path(graph(),"a","private-id") is None
    assert shortest_public_path(graph(),"a","a")["length"]==0


def test_bad_depth_and_large_graph_rejected():
    with pytest.raises(ValueError):
        neighborhood_public_graph(graph(),"a",0)
    with pytest.raises(ValueError):
        shortest_public_path(graph(),"a","b",8)
    large={"nodes":[{"id":str(i),"kind":"source"} for i in range(151)],"edges":[]}
    with pytest.raises(ValueError):
        analyze_public_graph(large)
