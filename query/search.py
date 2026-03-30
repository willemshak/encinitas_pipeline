#!/usr/bin/env python3
"""RAG search over the Encinitas permit knowledge graph.

Search strategy:
  1. Query `encinitas_permit_content` collection (one doc per permit, full JSON)
     → returns top-k permits by semantic similarity
  2. Query `encinitas_permits` node collection with where={type: permit} filter
     → additional permit seeds via hybrid keyword boost
  3. Light graph traversal (depth=1) from permit seeds to pull required docs / related permits
  4. Build compact context from permit JSONs + neighbour nodes
  5. Claude generates the answer

Usage: python query/search.py "I want to build an ADU in Encinitas"
       python query/search.py --top-k 8 "I want to add a fence"
"""

import json
import re
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

sys.path.insert(0, ".")
import config

console = Console()

EMBEDDING_MODEL = "all-mpnet-base-v2"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _keyword_score(text: str, query_words: list[str]) -> float:
    """Count query word hits in text (case-insensitive)."""
    text_lower = text.lower()
    return sum(1.0 for w in query_words if w in text_lower)


def load_graph():
    nodes_path = config.GRAPH_DIR / "nodes.json"
    edges_path = config.GRAPH_DIR / "edges.json"
    if not nodes_path.exists():
        console.print("[red]No graph data found. Run the pipeline first.[/red]")
        sys.exit(1)
    with open(nodes_path) as f:
        nodes = {n["id"]: n for n in json.load(f)}
    with open(edges_path) as f:
        edges = json.load(f)
    return nodes, edges


def get_neighbours(node_id: str, nodes: dict, edges: list, depth: int = 1) -> list[dict]:
    """BFS from node_id up to `depth` hops. Returns neighbour nodes (not the seed itself)."""
    visited = {node_id}
    frontier = [(node_id, 0)]
    result = []
    while frontier:
        current_id, d = frontier.pop(0)
        if d >= depth:
            continue
        for edge in edges:
            for neighbour in (edge["to_id"] if edge["from_id"] == current_id else None,
                              edge["from_id"] if edge["to_id"] == current_id else None):
                if neighbour and neighbour not in visited:
                    visited.add(neighbour)
                    if neighbour in nodes:
                        result.append(nodes[neighbour])
                    frontier.append((neighbour, d + 1))
    return result


def _permit_summary_text(permit: dict) -> str:
    """Compact human-readable summary of a permit JSON for the LLM context."""
    slug = permit.get("slug", "")
    lines = [
        f"## {permit.get('permit_name', slug)} (slug: {slug})",
    ]
    if permit.get("department"):
        lines.append(f"Department: {permit['department']}")
    if permit.get("plain_english_summary"):
        lines.append(f"Summary: {permit['plain_english_summary']}")
    if permit.get("description"):
        lines.append(f"Description: {permit['description']}")

    always = permit.get("always_required", [])
    if always:
        lines.append("Always required documents:")
        for d in always:
            lines.append(f"  - {d.get('name', '')}: {d.get('description', '')}")

    cond = permit.get("conditionally_required", [])
    if cond:
        lines.append("Conditionally required documents:")
        for d in cond:
            lines.append(f"  - {d.get('name', '')} (when: {d.get('condition_text', '')}): {d.get('description', '')}")

    steps = permit.get("process_steps", [])
    if steps:
        lines.append("Process steps: " + "; ".join(str(s) for s in steps))

    routing = permit.get("review_routing") or {}
    divisions = routing.get("divisions", [])
    if divisions:
        lines.append("Reviewed by: " + ", ".join(divisions))

    fees = permit.get("fees")
    if fees:
        lines.append(f"Fees: {fees}")

    related = permit.get("related_permits", [])
    if related:
        lines.append("Related permits: " + ", ".join(r.get("name", "") for r in related))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core search
# ---------------------------------------------------------------------------

def search(query: str, top_k: int = 5):
    nodes, edges = load_graph()
    query_words = [w for w in re.split(r"\W+", query.lower()) if len(w) > 2]

    console.print(f"\n[bold]Query:[/bold] {query}\n")

    # ------------------------------------------------------------------
    # Step 1: Vector search
    # ------------------------------------------------------------------
    console.print("[dim]Searching vector store...[/dim]")

    permit_hits: list[dict] = []   # full permit JSON objects from content collection
    node_hit_ids: list[str] = []   # node IDs from node collection (permit type)

    try:
        import chromadb
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(EMBEDDING_MODEL)
        query_embedding = model.encode([query]).tolist()

        chroma_path = str(config.GRAPH_DIR / "chroma_db")
        client = chromadb.PersistentClient(path=chroma_path)

        # --- Primary: permit content collection ---
        try:
            content_col = client.get_collection("encinitas_permit_content")
            n_content = content_col.count()
            k = min(top_k, n_content)
            results = content_col.query(query_embeddings=query_embedding, n_results=k)
            raw_docs = results["documents"][0] if results["documents"] else []
            raw_meta = results["metadatas"][0] if results["metadatas"] else []
            raw_ids  = results["ids"][0] if results["ids"] else []
            raw_dist = results["distances"][0] if results["distances"] else []

            for doc, meta, nid, dist in zip(raw_docs, raw_meta, raw_ids, raw_dist):
                try:
                    permit_data = json.loads(doc)
                except json.JSONDecodeError:
                    permit_data = {"slug": meta.get("slug", ""), "permit_name": meta.get("name", "")}
                # hybrid score: lower distance = better; boost by keyword hits
                kw_boost = _keyword_score(
                    f"{meta.get('name','')} {meta.get('department','')} {doc[:500]}", query_words
                )
                permit_hits.append({
                    "permit": permit_data,
                    "id": nid,
                    "distance": dist,
                    "kw_boost": kw_boost,
                    "score": -dist + kw_boost * 0.15,
                })
        except Exception as e:
            console.print(f"[yellow]permit_content collection unavailable: {e}[/yellow]")
            console.print("[yellow]Re-run scripts/04_build_graph.py to rebuild embeddings.[/yellow]")

        # --- Secondary: node collection filtered to permit type ---
        try:
            node_col = client.get_collection("encinitas_permits")
            n_nodes = node_col.count()
            k2 = min(top_k * 2, n_nodes)
            node_results = node_col.query(
                query_embeddings=query_embedding,
                n_results=k2,
                where={"type": "permit"},
            )
            node_hit_ids = node_results["ids"][0] if node_results["ids"] else []
        except Exception:
            pass

    except ImportError:
        console.print("[yellow]sentence-transformers / chromadb not installed — using keyword fallback[/yellow]")

    # ------------------------------------------------------------------
    # Keyword fallback (if vector search yielded nothing)
    # ------------------------------------------------------------------
    if not permit_hits and not node_hit_ids:
        console.print("[yellow]No vector results — falling back to keyword search[/yellow]")
        scored = []
        for nid, node in nodes.items():
            if node.get("type") != "permit":
                continue
            text = f"{node['name']} {node.get('description', '')} {node.get('metadata', {}).get('plain_english_summary', '')}".lower()
            score = _keyword_score(text, query_words)
            if score > 0:
                scored.append((score, nid))
        scored.sort(key=lambda x: -x[0])
        node_hit_ids = [nid for _, nid in scored[:top_k]]

    # ------------------------------------------------------------------
    # Merge seeds: permit_hits (primary) + node_hit_ids (secondary)
    # ------------------------------------------------------------------
    permit_hits.sort(key=lambda x: -x["score"])
    permit_hits = permit_hits[:top_k]

    # Collect permit slugs already covered
    covered_ids = {h["id"] for h in permit_hits}
    extra_node_ids = [nid for nid in node_hit_ids if nid not in covered_ids][:max(0, top_k - len(permit_hits))]

    # ------------------------------------------------------------------
    # Step 2: Show retrieved permits
    # ------------------------------------------------------------------
    table = Table(title="Retrieved Permits", show_header=True)
    table.add_column("Rank", style="dim", width=4)
    table.add_column("Permit", style="cyan")
    table.add_column("Score", justify="right", style="green", width=8)
    table.add_column("KW hits", justify="right", style="dim", width=7)

    for i, h in enumerate(permit_hits, 1):
        name = h["permit"].get("permit_name", h["id"])
        table.add_row(str(i), name, f"{h['score']:.3f}", str(int(h["kw_boost"])))

    if extra_node_ids:
        for nid in extra_node_ids:
            node = nodes.get(nid, {})
            table.add_row("~", node.get("name", nid), "(node)", "")

    console.print(table)

    # ------------------------------------------------------------------
    # Step 3: Light graph traversal (depth=1) from permit seeds
    # ------------------------------------------------------------------
    console.print("\n[dim]Fetching related documents (depth=1)...[/dim]")

    seed_ids = [h["id"] for h in permit_hits] + extra_node_ids
    neighbour_nodes: list[dict] = []
    seen_neighbours: set[str] = set(seed_ids)

    for nid in seed_ids:
        for nb in get_neighbours(nid, nodes, edges, depth=1):
            if nb["id"] not in seen_neighbours:
                seen_neighbours.add(nb["id"])
                neighbour_nodes.append(nb)

    console.print(f"[dim]Seeds: {len(seed_ids)}, neighbours: {len(neighbour_nodes)}[/dim]")

    # ------------------------------------------------------------------
    # Step 4: Build context
    # ------------------------------------------------------------------

    # Primary context: full permit JSON summaries (best for answering)
    context_parts = []
    for h in permit_hits:
        context_parts.append(_permit_summary_text(h["permit"]))

    # For node-only hits (no permit JSON), use node data from graph
    for nid in extra_node_ids:
        node = nodes.get(nid)
        if node:
            lines = [f"## {node.get('name', nid)} (permit)"]
            meta = node.get("metadata", {})
            if node.get("description"):
                lines.append(node["description"])
            if meta.get("plain_english_summary"):
                lines.append(meta["plain_english_summary"])
            context_parts.append("\n".join(lines))

    # Secondary context: neighbour nodes (documents, references)
    neighbour_parts = []
    for node in neighbour_nodes:
        ntype = node.get("type", "")
        parts = [f"- [{ntype}] {node.get('name', node['id'])}"]
        if node.get("description"):
            parts.append(f"  {node['description']}")
        meta = node.get("metadata", {})
        if meta.get("condition_text"):
            parts.append(f"  Required when: {meta['condition_text']}")
        neighbour_parts.append("\n".join(parts))

    # Relevant edges for the seeds
    edge_lines = []
    for edge in edges:
        if edge["from_id"] in seen_neighbours or edge["to_id"] in seen_neighbours:
            line = f"- {edge['from_id']} --[{edge['edge_type']}]--> {edge['to_id']}"
            if edge.get("condition"):
                line += f" (if: {edge['condition']})"
            edge_lines.append(line)

    context_text = "# PERMIT INFORMATION\n\n" + "\n\n---\n\n".join(context_parts)
    if neighbour_parts:
        context_text += "\n\n# RELATED DOCUMENTS & REFERENCES\n\n" + "\n".join(neighbour_parts[:60])
    if edge_lines:
        context_text += "\n\n# RELATIONSHIPS\n\n" + "\n".join(edge_lines[:80])

    # Guard against massive context
    if len(context_text) > 120_000:
        context_text = context_text[:120_000] + "\n\n[...truncated...]"

    # ------------------------------------------------------------------
    # Step 5: Claude answer generation
    # ------------------------------------------------------------------
    if not config.ANTHROPIC_API_KEY:
        console.print("\n[yellow]ANTHROPIC_API_KEY not set — showing raw context[/yellow]")
        console.print(Panel(context_text[:3000], title="Raw Context (truncated)"))
        return

    console.print("\n[dim]Generating answer with Claude...[/dim]")

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=2048,
            system=(
                "You are a helpful municipal permit assistant for the City of Encinitas, CA. "
                "Answer the user's question based on the provided permit data. "
                "Be specific about required documents, conditions, fees, and process steps. "
                "If the user describes a project, identify which permit(s) apply and what they need to submit. "
                "If information is missing from the context, say so clearly."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Question: {query}\n\n"
                    f"{context_text}"
                ),
            }],
        )

        answer = response.content[0].text
        console.print(Panel(answer, title="[bold green]Answer[/bold green]", border_style="green"))
        console.print(
            f"\n[dim]Tokens: {response.usage.input_tokens} in / {response.usage.output_tokens} out[/dim]"
        )

    except Exception as e:
        console.print(f"[red]Claude API error: {e}[/red]")
        console.print(Panel(context_text[:3000], title="Raw Context (API failed)"))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Search Encinitas permit data")
    parser.add_argument("query", nargs="+", help="Natural language query")
    parser.add_argument("--top-k", type=int, default=5, help="Number of permits to retrieve (default: 5)")
    args = parser.parse_args()

    search(" ".join(args.query), top_k=args.top_k)


if __name__ == "__main__":
    main()
