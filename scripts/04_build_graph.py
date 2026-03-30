#!/usr/bin/env python3
"""Script 4: Build knowledge graph and embeddings from structured permit data.

Creates nodes/edges from structured permits, detects shared facts,
generates embeddings, and stores in ChromaDB.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.table import Table

sys.path.insert(0, ".")
import config

console = Console()


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def load_structured_data() -> list[dict]:
    """Load all structured JSON files."""
    permits = []
    for path in sorted(config.STRUCTURED_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        with open(path) as f:
            permits.append(json.load(f))
    return permits


def build_nodes(permits: list[dict]) -> list[dict]:
    """Step 1: Create nodes from permits and their documents."""
    nodes = []
    seen_ids = set()

    for permit in permits:
        slug = permit.get("slug", "")
        # Permit node
        node_id = f"permit:{slug}"
        if node_id not in seen_ids:
            seen_ids.add(node_id)
            nodes.append({
                "id": node_id,
                "type": "permit",
                "name": permit.get("permit_name", slug),
                "description": permit.get("description", ""),
                "metadata": {
                    "department": permit.get("department", ""),
                    "plain_english_summary": permit.get("plain_english_summary", ""),
                    "fees": permit.get("fees"),
                    "review_routing": permit.get("review_routing"),
                    "process_steps": permit.get("process_steps", []),
                },
                "source_url": None,
                "municipality": "encinitas",
            })

        # Required document nodes
        for doc in permit.get("always_required", []):
            doc_id = f"document:{slugify(doc.get('name', ''))}"
            if doc_id not in seen_ids and doc_id != "document:":
                seen_ids.add(doc_id)
                nodes.append({
                    "id": doc_id,
                    "type": "required_document",
                    "name": doc.get("name", ""),
                    "description": doc.get("description", ""),
                    "metadata": {
                        "document_type": doc.get("document_type", "other"),
                    },
                    "source_url": doc.get("url"),
                    "municipality": "encinitas",
                })

        # Conditional document nodes
        for doc in permit.get("conditionally_required", []):
            doc_id = f"document:{slugify(doc.get('name', ''))}"
            if doc_id not in seen_ids and doc_id != "document:":
                seen_ids.add(doc_id)
                nodes.append({
                    "id": doc_id,
                    "type": "conditional_document",
                    "name": doc.get("name", ""),
                    "description": doc.get("description", ""),
                    "metadata": {
                        "condition_text": doc.get("condition_text", ""),
                        "condition_structured": doc.get("condition_structured"),
                    },
                    "source_url": doc.get("url"),
                    "municipality": "encinitas",
                })

        # Reference nodes
        for ref in permit.get("references", []):
            ref_id = f"reference:{slugify(ref.get('name', ''))}"
            if ref_id not in seen_ids and ref_id != "reference:":
                seen_ids.add(ref_id)
                nodes.append({
                    "id": ref_id,
                    "type": "reference",
                    "name": ref.get("name", ""),
                    "description": ref.get("context", ""),
                    "metadata": {},
                    "source_url": ref.get("url"),
                    "municipality": "encinitas",
                })

    return nodes


def build_edges(permits: list[dict]) -> list[dict]:
    """Step 2: Create edges from relationships."""
    edges = []

    for permit in permits:
        slug = permit.get("slug", "")
        permit_id = f"permit:{slug}"

        # Required document edges
        for doc in permit.get("always_required", []):
            doc_id = f"document:{slugify(doc.get('name', ''))}"
            if doc_id != "document:":
                edges.append({
                    "from_id": permit_id,
                    "to_id": doc_id,
                    "edge_type": "requires",
                    "condition": None,
                    "label": f"requires {doc.get('name', '')}",
                })

        # Conditional document edges
        for doc in permit.get("conditionally_required", []):
            doc_id = f"document:{slugify(doc.get('name', ''))}"
            if doc_id != "document:":
                edges.append({
                    "from_id": permit_id,
                    "to_id": doc_id,
                    "edge_type": "may_require_if",
                    "condition": doc.get("condition_text", ""),
                    "label": f"may require {doc.get('name', '')} if {doc.get('condition_text', '')}",
                })

        # Related permit edges
        for rel in permit.get("related_permits", []):
            rel_slug = slugify(rel.get("name", ""))
            rel_id = f"permit:{rel_slug}"
            if rel_slug:
                edges.append({
                    "from_id": permit_id,
                    "to_id": rel_id,
                    "edge_type": rel.get("relationship", "may_also_need"),
                    "condition": rel.get("condition_text"),
                    "label": f"{rel.get('relationship', '')} {rel.get('name', '')}",
                })

        # Reference edges
        for ref in permit.get("references", []):
            ref_id = f"reference:{slugify(ref.get('name', ''))}"
            if ref_id != "reference:":
                edges.append({
                    "from_id": permit_id,
                    "to_id": ref_id,
                    "edge_type": "references",
                    "condition": None,
                    "label": f"references {ref.get('name', '')}",
                })

        # Review routing edges
        routing = permit.get("review_routing", {})
        if routing:
            for division in routing.get("divisions", []):
                div_id = f"division:{slugify(division)}"
                edges.append({
                    "from_id": div_id,
                    "to_id": permit_id,
                    "edge_type": "reviews",
                    "condition": None,
                    "label": f"{division} reviews {permit.get('permit_name', '')}",
                })

    return edges


def detect_shared_facts(permits: list[dict], edges: list[dict]) -> list[dict]:
    """Step 3: Find shared form fields across permits and create edges."""
    fact_map: dict[str, list[str]] = {}

    for permit in permits:
        slug = permit.get("slug", "")
        for field in permit.get("form_fields", []):
            fact_type = field.get("shared_fact_type")
            if fact_type:
                fact_map.setdefault(fact_type, []).append(slug)

    shared_facts = []
    for fact_type, slugs in fact_map.items():
        unique_slugs = list(set(slugs))
        if len(unique_slugs) > 1:
            shared_facts.append({
                "fact_type": fact_type,
                "appears_on_permits": unique_slugs,
            })
            # Create edges between all pairs
            for i, s1 in enumerate(unique_slugs):
                for s2 in unique_slugs[i + 1 :]:
                    edges.append({
                        "from_id": f"permit:{s1}",
                        "to_id": f"permit:{s2}",
                        "edge_type": "shares_field_with",
                        "condition": f"shared field: {fact_type}",
                        "label": f"both require {fact_type}",
                    })

    return shared_facts


def build_embeddings(nodes: list[dict]):
    """Step 4: Generate embeddings and store in ChromaDB."""
    console.print("\n[bold]Building embeddings...[/bold]")

    # Build contextualized text for each node
    texts = []
    ids = []
    metadatas = []

    for node in nodes:
        parts = [p for p in [
            node.get("name") or node["id"],
            node.get("description"),
            (f"Department: {node.get('metadata', {}).get('department')}"
             if node.get("metadata", {}).get("department") else None),
            node.get("metadata", {}).get("plain_english_summary"),
        ] if p]

        text = ". ".join(parts)
        texts.append(text)
        ids.append(node["id"])
        metadatas.append({
            "type": node["type"],
            "name": node["name"],
            "municipality": node.get("municipality", "encinitas"),
        })

    # Use sentence-transformers for embeddings
    try:
        from sentence_transformers import SentenceTransformer
        console.print("  Using sentence-transformers (all-MiniLM-L6-v2)")
        model = SentenceTransformer("all-MiniLM-L6-v2")
        embeddings = model.encode(texts, show_progress_bar=True).tolist()
    except ImportError:
        console.print("[yellow]sentence-transformers not installed, skipping embeddings[/yellow]")
        return

    # Store in ChromaDB
    try:
        import chromadb

        chroma_path = str(config.GRAPH_DIR / "chroma_db")
        client = chromadb.PersistentClient(path=chroma_path)

        # Delete existing collection if it exists
        try:
            client.delete_collection("encinitas_permits")
        except Exception:
            pass

        collection = client.create_collection(
            name="encinitas_permits",
            metadata={"description": "Encinitas permit knowledge graph nodes"},
        )

        # Add in batches (ChromaDB has limits)
        batch_size = 100
        for i in range(0, len(ids), batch_size):
            end = min(i + batch_size, len(ids))
            collection.add(
                ids=ids[i:end],
                embeddings=embeddings[i:end],
                documents=texts[i:end],
                metadatas=metadatas[i:end],
            )

        console.print(f"  [green]Stored {len(ids)} embeddings in ChromaDB[/green]")

    except ImportError:
        console.print("[yellow]chromadb not installed, skipping vector store[/yellow]")
    except Exception as e:
        console.print(f"[red]ChromaDB error: {e}[/red]")


def compute_stats(nodes: list[dict], edges: list[dict], shared_facts: list[dict]) -> dict:
    """Compute graph statistics."""
    from collections import Counter

    node_types = Counter(n["type"] for n in nodes)
    edge_types = Counter(e["edge_type"] for e in edges)

    # Find orphan nodes (no edges)
    connected = set()
    for e in edges:
        connected.add(e["from_id"])
        connected.add(e["to_id"])

    orphans = [n["id"] for n in nodes if n["id"] not in connected]

    # Most connected nodes
    edge_count: dict[str, int] = {}
    for e in edges:
        edge_count[e["from_id"]] = edge_count.get(e["from_id"], 0) + 1
        edge_count[e["to_id"]] = edge_count.get(e["to_id"], 0) + 1

    most_connected = sorted(edge_count.items(), key=lambda x: -x[1])[:10]

    return {
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "nodes_by_type": dict(node_types),
        "edges_by_type": dict(edge_types),
        "orphan_nodes": orphans,
        "most_connected_nodes": [
            {"id": nid, "edge_count": cnt} for nid, cnt in most_connected
        ],
        "shared_facts_detected": shared_facts,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def main():
    permits = load_structured_data()
    if not permits:
        console.print("[red]No structured data found. Run 03_extract_structured.py first.[/red]")
        sys.exit(1)

    console.print(f"[bold]Loaded {len(permits)} structured permits[/bold]\n")

    # Step 1: Build nodes
    console.print("[bold]Step 1:[/bold] Creating nodes...")
    nodes = build_nodes(permits)
    console.print(f"  Created {len(nodes)} nodes")

    # Step 2: Build edges
    console.print("[bold]Step 2:[/bold] Creating edges...")
    edges = build_edges(permits)
    console.print(f"  Created {len(edges)} edges")

    # Step 3: Detect shared facts
    console.print("[bold]Step 3:[/bold] Detecting shared facts...")
    shared_facts = detect_shared_facts(permits, edges)
    console.print(f"  Found {len(shared_facts)} shared fact types")
    console.print(f"  Total edges after shared facts: {len(edges)}")

    # Step 4: Build embeddings
    build_embeddings(nodes)

    # Step 5: Save outputs
    console.print("\n[bold]Step 5:[/bold] Saving outputs...")

    with open(config.GRAPH_DIR / "nodes.json", "w") as f:
        json.dump(nodes, f, indent=2)

    with open(config.GRAPH_DIR / "edges.json", "w") as f:
        json.dump(edges, f, indent=2)

    stats = compute_stats(nodes, edges, shared_facts)
    with open(config.GRAPH_DIR / "graph_stats.json", "w") as f:
        json.dump(stats, f, indent=2)

    console.print(f"  [green]Saved to {config.GRAPH_DIR}/[/green]")

    # Print stats summary
    console.print("\n[bold]Graph Statistics:[/bold]")
    table = Table()
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="green")

    table.add_row("Total Nodes", str(stats["total_nodes"]))
    table.add_row("Total Edges", str(stats["total_edges"]))
    table.add_row("", "")

    for ntype, count in sorted(stats["nodes_by_type"].items()):
        table.add_row(f"  Nodes: {ntype}", str(count))
    for etype, count in sorted(stats["edges_by_type"].items()):
        table.add_row(f"  Edges: {etype}", str(count))

    table.add_row("", "")
    table.add_row("Orphan Nodes", str(len(stats["orphan_nodes"])))

    console.print(table)

    if stats["most_connected_nodes"]:
        console.print("\n[bold]Most Connected Nodes:[/bold]")
        for item in stats["most_connected_nodes"]:
            console.print(f"  {item['id']}: {item['edge_count']} edges")

    if stats["orphan_nodes"]:
        console.print(f"\n[yellow]Orphan nodes ({len(stats['orphan_nodes'])}):[/yellow]")
        for oid in stats["orphan_nodes"][:20]:
            console.print(f"  {oid}")

    if shared_facts:
        console.print("\n[bold]Shared Facts:[/bold]")
        for sf in shared_facts:
            console.print(
                f"  {sf['fact_type']}: appears on {len(sf['appears_on_permits'])} permits"
            )


if __name__ == "__main__":
    main()
