"""RH-05.2 — Analysis of already-approved public Research knowledge graphs.

This module never fetches, edits, approves, or infers scientific evidence.
Traversal reports *structural relationships* from accepted source metadata
only; even a short path is NOT a scientific proof or source citation.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from typing import Any

MAX_NODES = 150
MAX_EDGES = 300
MAX_DEPTH = 6


def _graph_index(graph: dict) -> tuple[dict[str, dict], dict[str, list[tuple[str, dict]]]]:
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    if len(nodes) > MAX_NODES or len(edges) > MAX_EDGES:
        raise ValueError("Graph view exceeds analysis bounds")
    lookup = {node["id"]: node for node in nodes}
    adjacency: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for edge in edges:
        if edge["source"] not in lookup or edge["target"] not in lookup:
            continue
        if edge["source"] == edge["target"]:
            continue
        adjacency[edge["source"]].append((edge["target"], edge))
        # Undirected navigation supports exploration; keep the original
        # source/target order and predicate on every edge.
        adjacency[edge["target"]].append((edge["source"], edge))
    for key in adjacency:
        adjacency[key].sort(key=lambda e: (e[0], e[1]["relation"]))
    return lookup, adjacency


def analyze_public_graph(graph: dict) -> dict:
    lookup, adjacency = _graph_index(graph)
    kind_counts = dict(sorted(Counter(n["kind"] for n in lookup.values()).items()))
    relation_counts = dict(sorted(Counter(edge["relation"] for edge in graph["edges"]).items()))
    isolated = sorted(key for key in lookup if not adjacency[key])
    seen = set()
    components = 0
    for key in sorted(lookup):
        if key in seen:
            continue
        components += 1
        queue = deque([key])
        seen.add(key)
        while queue:
            for neighbor, _ in adjacency[queue.popleft()]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
    return {
        "schema": "sc-research-public-graph-analysis/1.0",
        "nodes": len(lookup),
        "edges": len(graph["edges"]),
        "connected_components": components,
        "isolated_nodes": len(isolated),
        "object_kinds": kind_counts,
        "relation_types": relation_counts,
        "interpretation": "Structural analysis of reviewed metadata; does not establish scientific validity.",
    }


def neighborhood_public_graph(graph: dict, node_id: str, depth: int = 1) -> dict | None:
    if depth < 1 or depth > MAX_DEPTH:
        raise ValueError("Depth out of range")
    lookup, adjacency = _graph_index(graph)
    if node_id not in lookup:
        return None
    visited = {node_id}
    queue = deque([(node_id, 0)])
    while queue:
        current, distance = queue.popleft()
        if distance >= depth:
            continue
        for next_node, _ in adjacency[current]:
            if next_node not in visited:
                visited.add(next_node)
                queue.append((next_node, distance + 1))
    edges = [
        edge for edge in graph["edges"]
        if edge["source"] in visited and edge["target"] in visited
    ]
    return {
        "schema": "sc-research-public-neighborhood/1.0",
        "root": node_id,
        "depth": depth,
        "nodes": [lookup[key] for key in sorted(visited)],
        "edges": edges,
        "interpretation": "Reviewed structural metadata neighborhood; no causal inference.",
    }


def shortest_public_path(
    graph: dict, source: str, target: str, max_depth: int = MAX_DEPTH
) -> dict | None:
    if max_depth < 1 or max_depth > MAX_DEPTH:
        raise ValueError("Maximum depth out of range")
    lookup, adjacency = _graph_index(graph)
    if source not in lookup or target not in lookup:
        return None
    if source == target:
        return {"nodes": [lookup[source]], "edges": [], "length": 0,
                "interpretation": "Identity path; not scientific validation."}
    queue = deque([source])
    previous: dict[str, tuple[str, dict] | None] = {source: None}
    distance = {source: 0}
    while queue:
        current = queue.popleft()
        if distance[current] >= max_depth:
            continue
        for adjacent, edge in adjacency[current]:
            if adjacent in previous:
                continue
            previous[adjacent] = (current, edge)
            distance[adjacent] = distance[current] + 1
            if adjacent == target:
                ordered = [target]
                links: list[dict] = []
                cursor = target
                while cursor != source:
                    parent, current_edge = previous[cursor]  # type: ignore[misc]
                    links.append(current_edge)
                    ordered.append(parent)
                    cursor = parent
                ordered.reverse()
                links.reverse()
                return {
                    "nodes": [lookup[key] for key in ordered],
                    "edges": links, "length": len(links),
                    "interpretation": "Graph traversal of reviewed structural links; not causal or evidentiary proof.",
                }
            queue.append(adjacent)
    return None
