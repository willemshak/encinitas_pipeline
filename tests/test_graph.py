"""Tests for the knowledge graph."""

import json
import sys

sys.path.insert(0, ".")
import config


def load_graph():
    nodes_path = config.GRAPH_DIR / "nodes.json"
    edges_path = config.GRAPH_DIR / "edges.json"
    if not nodes_path.exists():
        return None, None
    with open(nodes_path) as f:
        nodes = json.load(f)
    with open(edges_path) as f:
        edges = json.load(f)
    return nodes, edges


def test_graph_exists():
    """Verify graph files exist."""
    assert (config.GRAPH_DIR / "nodes.json").exists(), "nodes.json not found"
    assert (config.GRAPH_DIR / "edges.json").exists(), "edges.json not found"
    assert (config.GRAPH_DIR / "graph_stats.json").exists(), "graph_stats.json not found"


def test_graph_has_nodes():
    """Verify graph has nodes."""
    nodes, edges = load_graph()
    if nodes is None:
        return
    assert len(nodes) > 0, "Graph has no nodes"


def test_graph_has_edges():
    """Verify graph has edges."""
    nodes, edges = load_graph()
    if edges is None:
        return
    assert len(edges) > 0, "Graph has no edges"


def test_no_orphan_permits():
    """Verify every permit node has at least one edge."""
    nodes, edges = load_graph()
    if nodes is None:
        return

    permit_ids = {n["id"] for n in nodes if n["type"] == "permit"}
    connected = set()
    for e in edges:
        connected.add(e["from_id"])
        connected.add(e["to_id"])

    orphan_permits = permit_ids - connected
    assert len(orphan_permits) == 0, (
        f"Orphan permits (no edges): {orphan_permits}"
    )


def test_node_schema():
    """Verify nodes have required fields."""
    nodes, _ = load_graph()
    if nodes is None:
        return

    for node in nodes:
        assert "id" in node, "Node missing 'id'"
        assert "type" in node, f"Node {node.get('id')} missing 'type'"
        assert "name" in node, f"Node {node.get('id')} missing 'name'"


def test_edge_schema():
    """Verify edges have required fields."""
    _, edges = load_graph()
    if edges is None:
        return

    for edge in edges:
        assert "from_id" in edge, "Edge missing 'from_id'"
        assert "to_id" in edge, "Edge missing 'to_id'"
        assert "edge_type" in edge, "Edge missing 'edge_type'"


def test_graph_stats():
    """Verify graph_stats.json has expected fields."""
    path = config.GRAPH_DIR / "graph_stats.json"
    if not path.exists():
        return

    with open(path) as f:
        stats = json.load(f)

    assert "total_nodes" in stats
    assert "total_edges" in stats
    assert "nodes_by_type" in stats
    assert "edges_by_type" in stats
    assert stats["total_nodes"] > 0


if __name__ == "__main__":
    tests = [
        test_graph_exists,
        test_graph_has_nodes,
        test_graph_has_edges,
        test_no_orphan_permits,
        test_node_schema,
        test_edge_schema,
        test_graph_stats,
    ]
    passed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS: {test.__name__}")
            passed += 1
        except (AssertionError, Exception) as e:
            print(f"  FAIL: {test.__name__} — {e}")

    print(f"\n{passed}/{len(tests)} tests passed")
