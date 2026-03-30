#!/usr/bin/env python3
"""RAG search over the Encinitas permit knowledge graph.

Usage: python query/search.py "I want to build an ADU in Encinitas"
"""

import json
import sys

from rich.console import Console
from rich.panel import Panel

sys.path.insert(0, ".")
import config

console = Console()


def load_graph():
    """Load graph nodes and edges."""
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


def get_related_nodes(node_id: str, nodes: dict, edges: list, depth: int = 2) -> list[dict]:
    """Traverse graph from a node to get related context."""
    visited = set()
    result = []
    frontier = [(node_id, 0)]

    while frontier:
        current_id, current_depth = frontier.pop(0)
        if current_id in visited or current_depth > depth:
            continue
        visited.add(current_id)

        node = nodes.get(current_id)
        if node:
            result.append(node)

        if current_depth < depth:
            for edge in edges:
                if edge["from_id"] == current_id and edge["to_id"] not in visited:
                    frontier.append((edge["to_id"], current_depth + 1))
                if edge["to_id"] == current_id and edge["from_id"] not in visited:
                    frontier.append((edge["from_id"], current_depth + 1))

    return result


def search(query: str, top_k: int = 5):
    """Run the RAG search pipeline."""
    nodes, edges = load_graph()

    # Step 1: Embed query and search ChromaDB
    console.print(f"\n[bold]Query:[/bold] {query}\n")
    console.print("[dim]Searching vector store...[/dim]")

    retrieved_ids = []
    retrieved_docs = []

    try:
        import chromadb
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer("all-MiniLM-L6-v2")
        query_embedding = model.encode([query]).tolist()

        chroma_path = str(config.GRAPH_DIR / "chroma_db")
        client = chromadb.PersistentClient(path=chroma_path)
        collection = client.get_collection("encinitas_permits")

        results = collection.query(
            query_embeddings=query_embedding,
            n_results=top_k,
        )

        retrieved_ids = results["ids"][0] if results["ids"] else []
        retrieved_docs = results["documents"][0] if results["documents"] else []

    except Exception as e:
        console.print(f"[yellow]Vector search failed: {e}[/yellow]")
        console.print("[yellow]Falling back to keyword search...[/yellow]")

        # Keyword fallback
        query_lower = query.lower()
        scored = []
        for nid, node in nodes.items():
            text = f"{node['name']} {node.get('description', '')}".lower()
            score = sum(1 for word in query_lower.split() if word in text)
            if score > 0:
                scored.append((score, nid, text))
        scored.sort(key=lambda x: -x[0])
        for score, nid, text in scored[:top_k]:
            retrieved_ids.append(nid)
            retrieved_docs.append(text)

    if not retrieved_ids:
        console.print("[yellow]No relevant results found.[/yellow]")
        return

    # Step 2: Show retrieved nodes
    console.print(f"\n[bold green]Retrieved {len(retrieved_ids)} nodes:[/bold green]")
    for i, (nid, doc) in enumerate(zip(retrieved_ids, retrieved_docs), 1):
        node = nodes.get(nid, {})
        console.print(Panel(
            f"[bold]{node.get('name', nid)}[/bold] ({node.get('type', '?')})\n"
            f"[dim]{doc[:200]}[/dim]",
            title=f"Result {i}: {nid}",
        ))

    # Step 3: Traverse graph for dependencies
    console.print("\n[dim]Traversing graph for related context...[/dim]")
    all_related = []
    for nid in retrieved_ids:
        related = get_related_nodes(nid, nodes, edges, depth=2)
        all_related.extend(related)

    # Deduplicate
    seen = set()
    unique_context = []
    for node in all_related:
        if node["id"] not in seen:
            seen.add(node["id"])
            unique_context.append(node)

    console.print(f"[dim]Total context nodes: {len(unique_context)}[/dim]")

    # Step 4: Build context and call Claude
    context_parts = []
    for node in unique_context:
        parts = [f"- {node['name']} ({node['type']})"]
        if node.get("description"):
            parts.append(f"  Description: {node['description']}")
        meta = node.get("metadata", {})
        if meta.get("plain_english_summary"):
            parts.append(f"  Summary: {meta['plain_english_summary']}")
        if meta.get("department"):
            parts.append(f"  Department: {meta['department']}")
        context_parts.append("\n".join(parts))

    # Include relevant edges
    edge_context = []
    for edge in edges:
        if edge["from_id"] in seen or edge["to_id"] in seen:
            edge_context.append(
                f"- {edge['from_id']} --[{edge['edge_type']}]--> {edge['to_id']}"
                + (f" (condition: {edge['condition']})" if edge.get("condition") else "")
            )

    context_text = "RELEVANT PERMITS AND DOCUMENTS:\n" + "\n\n".join(context_parts)
    if edge_context:
        context_text += "\n\nRELATIONSHIPS:\n" + "\n".join(edge_context[:50])

    # Call Claude for answer generation
    if not config.ANTHROPIC_API_KEY:
        console.print("\n[yellow]ANTHROPIC_API_KEY not set — showing raw context only[/yellow]")
        console.print(Panel(context_text, title="Raw Context"))
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
                "Be specific about required documents, conditions, and process steps. "
                "If information is missing from the context, say so clearly."
            ),
            messages=[{
                "role": "user",
                "content": f"Based on the following permit data, answer this question: {query}\n\n{context_text}",
            }],
        )

        answer = response.content[0].text
        console.print(Panel(answer, title="[bold green]Answer[/bold green]", border_style="green"))
        console.print(
            f"\n[dim]Tokens: {response.usage.input_tokens} in / {response.usage.output_tokens} out[/dim]"
        )

    except Exception as e:
        console.print(f"[red]Claude API error: {e}[/red]")
        console.print(Panel(context_text, title="Raw Context (API failed)"))


def main():
    if len(sys.argv) < 2:
        console.print("Usage: python query/search.py \"your question here\"")
        console.print("Example: python query/search.py \"I want to build an ADU in Encinitas\"")
        sys.exit(1)

    query = " ".join(sys.argv[1:])
    search(query)


if __name__ == "__main__":
    main()
