#!/usr/bin/env python3
"""View structured permit extraction data."""

import argparse
import json
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

sys.path.insert(0, ".")
import config

console = Console()


def load_permit(slug: str) -> dict:
    path = config.STRUCTURED_DIR / f"{slug}.json"
    if not path.exists():
        console.print(f"[red]No structured data for '{slug}'. Run 03_extract_structured.py first.[/red]")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def load_all_permits() -> list[dict]:
    permits = []
    for path in sorted(config.STRUCTURED_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        with open(path) as f:
            permits.append(json.load(f))
    return permits


def show_full(data: dict):
    """Pretty-print the full structured data."""
    console.print(Panel(
        f"[bold]{data.get('permit_name', '')}[/bold]\n"
        f"[dim]{data.get('department', '')}[/dim]\n\n"
        f"{data.get('plain_english_summary', '')}",
        title="Permit Overview",
    ))

    # Required documents
    if data.get("always_required"):
        tree = Tree("[bold green]Always Required[/bold green]")
        for doc in data["always_required"]:
            branch = tree.add(f"[bold]{doc.get('name', '')}[/bold] [{doc.get('document_type', '')}]")
            if doc.get("description"):
                branch.add(f"[dim]{doc['description']}[/dim]")
            if doc.get("url"):
                branch.add(f"[blue]{doc['url']}[/blue]")
        console.print(tree)

    # Conditional documents
    if data.get("conditionally_required"):
        tree = Tree("[bold yellow]Conditionally Required[/bold yellow]")
        for doc in data["conditionally_required"]:
            branch = tree.add(f"[bold]{doc.get('name', '')}[/bold]")
            if doc.get("condition_text"):
                branch.add(f"[yellow]IF: {doc['condition_text']}[/yellow]")
            if doc.get("description"):
                branch.add(f"[dim]{doc['description']}[/dim]")
        console.print(tree)

    # Related permits
    if data.get("related_permits"):
        table = Table(title="Related Permits")
        table.add_column("Name", style="bold")
        table.add_column("Relationship", style="cyan")
        table.add_column("Condition", style="dim")
        for rel in data["related_permits"]:
            table.add_row(
                rel.get("name", ""),
                rel.get("relationship", ""),
                rel.get("condition_text", ""),
            )
        console.print(table)

    # Process steps
    if data.get("process_steps"):
        console.print("\n[bold]Process Steps:[/bold]")
        for step in data["process_steps"]:
            console.print(f"  {step.get('step_number', '?')}. {step.get('description', '')}")

    # Fees
    fees = data.get("fees", {})
    if fees and (fees.get("description") or fees.get("fee_schedule_url")):
        console.print(f"\n[bold]Fees:[/bold] {fees.get('description', 'N/A')}")
        if fees.get("fee_schedule_url"):
            console.print(f"  [blue]{fees['fee_schedule_url']}[/blue]")

    # Review routing
    routing = data.get("review_routing", {})
    if routing:
        console.print(f"\n[bold]Review Routing:[/bold]")
        if routing.get("divisions"):
            console.print(f"  Divisions: {', '.join(routing['divisions'])}")
        if routing.get("timeline"):
            console.print(f"  Timeline: {routing['timeline']}")
        if routing.get("submission_method"):
            console.print(f"  Submit via: {routing['submission_method']}")


def main():
    parser = argparse.ArgumentParser(description="View structured permit data")
    parser.add_argument("--permit", type=str, help="Permit slug")
    parser.add_argument("--required", action="store_true", help="Show only required items")
    parser.add_argument("--conditions", action="store_true", help="Show only conditional items")
    parser.add_argument("--related", action="store_true", help="Show related permits")
    parser.add_argument("--all", action="store_true", help="Show all permits")
    parser.add_argument("--summary", action="store_true", help="One-line summary per permit")
    parser.add_argument("--costs", action="store_true", help="Show LLM extraction costs")
    args = parser.parse_args()

    if args.costs:
        log_path = config.STRUCTURED_DIR / "_extraction_log.json"
        if not log_path.exists():
            console.print("[red]No extraction log found[/red]")
            return
        with open(log_path) as f:
            log = json.load(f)
        table = Table(title="LLM Extraction Costs")
        table.add_column("Slug", style="cyan")
        table.add_column("Input Tokens", justify="right")
        table.add_column("Output Tokens", justify="right")
        table.add_column("Cost", justify="right", style="green")
        table.add_column("Status", style="bold")
        total_cost = 0
        for entry in log:
            if entry.get("skipped"):
                continue
            cost = entry.get("cost_estimate", 0)
            total_cost += cost
            status = "[green]OK[/green]" if entry.get("success") else f"[red]{entry.get('error', 'FAIL')}[/red]"
            table.add_row(
                entry["slug"],
                f"{entry.get('input_tokens', 0):,}",
                f"{entry.get('output_tokens', 0):,}",
                f"${cost:.4f}",
                status,
            )
        table.add_row("[bold]TOTAL[/bold]", "", "", f"[bold]${total_cost:.4f}[/bold]", "")
        console.print(table)
        return

    if args.all and args.summary:
        permits = load_all_permits()
        table = Table(title=f"All Permits ({len(permits)})")
        table.add_column("Slug", style="cyan")
        table.add_column("Department", style="dim")
        table.add_column("Summary", max_width=80)
        for p in permits:
            table.add_row(
                p.get("slug", ""),
                p.get("department", ""),
                p.get("plain_english_summary", "")[:120],
            )
        console.print(table)
        return

    if not args.permit:
        console.print("[yellow]Use --permit <slug> or --all --summary or --costs[/yellow]")
        return

    data = load_permit(args.permit)

    if args.required:
        tree = Tree("[bold green]Always Required[/bold green]")
        for doc in data.get("always_required", []):
            branch = tree.add(f"[bold]{doc.get('name', '')}[/bold] [{doc.get('document_type', '')}]")
            if doc.get("description"):
                branch.add(f"[dim]{doc['description']}[/dim]")
        console.print(tree)
    elif args.conditions:
        tree = Tree("[bold yellow]Conditionally Required[/bold yellow]")
        for doc in data.get("conditionally_required", []):
            branch = tree.add(f"[bold]{doc.get('name', '')}[/bold]")
            if doc.get("condition_text"):
                branch.add(f"[yellow]IF: {doc['condition_text']}[/yellow]")
            cs = doc.get("condition_structured", {})
            if cs:
                branch.add(f"[dim]Field: {cs.get('field', '')} {cs.get('operator', '')} {cs.get('value', '')}[/dim]")
        console.print(tree)
    elif args.related:
        table = Table(title="Related Permits")
        table.add_column("Name", style="bold")
        table.add_column("Relationship", style="cyan")
        table.add_column("Condition", style="dim")
        for rel in data.get("related_permits", []):
            table.add_row(rel.get("name", ""), rel.get("relationship", ""), rel.get("condition_text", ""))
        console.print(table)
    else:
        show_full(data)


if __name__ == "__main__":
    main()
