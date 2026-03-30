#!/usr/bin/env python3
"""View the permit index."""

import argparse
import json
import sys
from collections import Counter

from rich.console import Console
from rich.table import Table

sys.path.insert(0, ".")
import config

console = Console()


def load_index() -> list[dict]:
    path = config.INDEX_DIR / "permits_index.json"
    if not path.exists():
        console.print("[red]No index found. Run 01_crawl_index.py first.[/red]")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def main():
    parser = argparse.ArgumentParser(description="View permit index")
    parser.add_argument("--dept", type=str, help="Filter to one department")
    parser.add_argument("--stats", action="store_true", help="Show department counts")
    args = parser.parse_args()

    permits = load_index()

    if args.stats:
        counts = Counter(p["department"] for p in permits)
        table = Table(title="Permits by Department")
        table.add_column("Department", style="cyan")
        table.add_column("Count", justify="right", style="green")
        for dept, count in sorted(counts.items(), key=lambda x: -x[1]):
            table.add_row(dept, str(count))
        table.add_row("[bold]Total[/bold]", f"[bold]{len(permits)}[/bold]")
        console.print(table)
        return

    if args.dept:
        permits = [p for p in permits if args.dept.lower() in p["department"].lower()]
        if not permits:
            console.print(f"[yellow]No permits found for department '{args.dept}'[/yellow]")
            return

    table = Table(title=f"Permit Index ({len(permits)} permits)")
    table.add_column("Department", style="cyan", max_width=20)
    table.add_column("Name", style="bold")
    table.add_column("URL", style="dim", max_width=50)

    for p in permits:
        table.add_row(p["department"], p["permit_name"], p["permit_url"])

    console.print(table)


if __name__ == "__main__":
    main()
