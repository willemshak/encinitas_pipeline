#!/usr/bin/env python3
"""View knowledge graph data."""

import argparse
import json
import sys
from collections import deque

from rich.console import Console
from rich.table import Table
from rich.tree import Tree

sys.path.insert(0, ".")
import config

console = Console()


def load_graph():
    nodes_path = config.GRAPH_DIR / "nodes.json"
    edges_path = config.GRAPH_DIR / "edges.json"
    stats_path = config.GRAPH_DIR / "graph_stats.json"

    if not nodes_path.exists():
        console.print("[red]No graph data found. Run 04_build_graph.py first.[/red]")
        sys.exit(1)

    with open(nodes_path) as f:
        nodes = json.load(f)
    with open(edges_path) as f:
        edges = json.load(f)

    stats = {}
    if stats_path.exists():
        with open(stats_path) as f:
            stats = json.load(f)

    return nodes, edges, stats


def show_stats(stats: dict):
    """Print graph statistics."""
    table = Table(title="Graph Statistics")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="green")

    table.add_row("Total Nodes", str(stats.get("total_nodes", 0)))
    table.add_row("Total Edges", str(stats.get("total_edges", 0)))
    table.add_row("Orphan Nodes", str(len(stats.get("orphan_nodes", []))))

    for ntype, count in sorted(stats.get("nodes_by_type", {}).items()):
        table.add_row(f"  {ntype}", str(count))
    for etype, count in sorted(stats.get("edges_by_type", {}).items()):
        table.add_row(f"  {etype}", str(count))

    console.print(table)

    if stats.get("most_connected_nodes"):
        console.print("\n[bold]Most Connected:[/bold]")
        for item in stats["most_connected_nodes"]:
            console.print(f"  {item['id']}: {item['edge_count']} edges")


def show_node(node_id: str, nodes: list, edges: list):
    """Show all edges to/from a node."""
    # Find the node — try exact match, then partial
    node = None
    for n in nodes:
        if n["id"] == node_id or n["id"] == f"permit:{node_id}":
            node = n
            break
    if not node:
        # Partial match
        matches = [n for n in nodes if node_id in n["id"]]
        if len(matches) == 1:
            node = matches[0]
        elif matches:
            console.print(f"[yellow]Multiple matches for '{node_id}':[/yellow]")
            for m in matches:
                console.print(f"  {m['id']}")
            return
        else:
            console.print(f"[red]Node '{node_id}' not found[/red]")
            return

    console.print(f"\n[bold]{node['id']}[/bold] ({node['type']})")
    console.print(f"  Name: {node['name']}")
    if node.get("description"):
        console.print(f"  Description: {node['description'][:200]}")

    # Find connected edges
    outgoing = [e for e in edges if e["from_id"] == node["id"]]
    incoming = [e for e in edges if e["to_id"] == node["id"]]

    if outgoing:
        tree = Tree(f"[green]Outgoing ({len(outgoing)})[/green]")
        for e in outgoing:
            branch = tree.add(f"[cyan]{e['edge_type']}[/cyan] -> {e['to_id']}")
            if e.get("condition"):
                branch.add(f"[dim]{e['condition']}[/dim]")
        console.print(tree)

    if incoming:
        tree = Tree(f"[blue]Incoming ({len(incoming)})[/blue]")
        for e in incoming:
            branch = tree.add(f"{e['from_id']} -> [cyan]{e['edge_type']}[/cyan]")
            if e.get("condition"):
                branch.add(f"[dim]{e['condition']}[/dim]")
        console.print(tree)


def show_path(start: str, end: str, nodes: list, edges: list):
    """BFS to find path between two nodes."""
    # Resolve node IDs
    def resolve(name):
        for n in nodes:
            if n["id"] == name or n["id"] == f"permit:{name}":
                return n["id"]
        matches = [n for n in nodes if name in n["id"]]
        return matches[0]["id"] if len(matches) == 1 else None

    start_id = resolve(start)
    end_id = resolve(end)

    if not start_id:
        console.print(f"[red]Node '{start}' not found[/red]")
        return
    if not end_id:
        console.print(f"[red]Node '{end}' not found[/red]")
        return

    # Build adjacency (undirected)
    adj: dict[str, list[tuple[str, dict]]] = {}
    for e in edges:
        adj.setdefault(e["from_id"], []).append((e["to_id"], e))
        adj.setdefault(e["to_id"], []).append((e["from_id"], e))

    # BFS
    visited = {start_id}
    queue = deque([(start_id, [])])

    while queue:
        current, path = queue.popleft()
        if current == end_id:
            console.print(f"\n[green]Path found ({len(path)} hops):[/green]")
            for step_node, step_edge in path:
                console.print(
                    f"  {step_edge['from_id']} "
                    f"--[cyan]{step_edge['edge_type']}[/cyan]--> "
                    f"{step_edge['to_id']}"
                )
            return

        for neighbor, edge in adj.get(current, []):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [(neighbor, edge)]))

    console.print(f"[yellow]No path found between {start_id} and {end_id}[/yellow]")


def main():
    parser = argparse.ArgumentParser(description="View knowledge graph")
    parser.add_argument("--node", type=str, help="Show edges for a node")
    parser.add_argument("--shared-facts", action="store_true", help="Show shared facts")
    parser.add_argument("--orphans", action="store_true", help="Show orphan nodes")
    parser.add_argument("--path", nargs=2, metavar=("FROM", "TO"), help="Find path between nodes")
    args = parser.parse_args()

    nodes, edges, stats = load_graph()

    if args.node:
        show_node(args.node, nodes, edges)
    elif args.shared_facts:
        facts = stats.get("shared_facts_detected", [])
        if not facts:
            console.print("[yellow]No shared facts detected[/yellow]")
            return
        for sf in facts:
            console.print(f"\n[bold cyan]{sf['fact_type']}[/bold cyan]")
            for slug in sf["appears_on_permits"]:
                console.print(f"  - {slug}")
    elif args.orphans:
        orphans = stats.get("orphan_nodes", [])
        if not orphans:
            console.print("[green]No orphan nodes[/green]")
            return
        console.print(f"[yellow]Orphan nodes ({len(orphans)}):[/yellow]")
        for oid in orphans:
            console.print(f"  {oid}")
    elif args.path:
        show_path(args.path[0], args.path[1], nodes, edges)
    else:
        show_stats(stats)


if __name__ == "__main__":
    main()
