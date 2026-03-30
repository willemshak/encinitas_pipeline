#!/usr/bin/env python3
"""View raw crawled permit data."""

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

sys.path.insert(0, ".")
import config

console = Console()

SHARED_DIR = config.RAW_DIR / "_shared"
PDF_DIR = SHARED_DIR / "pdfs"
SUBPAGE_DIR = SHARED_DIR / "subpages"


def load_registries() -> tuple[dict, dict]:
    pdf_reg: dict = {}
    sub_reg: dict = {}
    if (SHARED_DIR / "pdf_registry.json").exists():
        with open(SHARED_DIR / "pdf_registry.json") as f:
            pdf_reg = json.load(f)
    if (SHARED_DIR / "subpage_registry.json").exists():
        with open(SHARED_DIR / "subpage_registry.json") as f:
            sub_reg = json.load(f)
    return pdf_reg, sub_reg


def load_refs(permit_dir: Path) -> dict:
    refs_path = permit_dir / "references.json"
    if refs_path.exists():
        with open(refs_path) as f:
            return json.load(f)
    return {"permit_type": "unknown", "items": []}


def show_default(slug: str, pdf_reg: dict, sub_reg: dict):
    permit_dir = config.RAW_DIR / slug
    refs = load_refs(permit_dir)
    permit_type = refs.get("permit_type", "unknown")
    permit_name = refs.get("permit_name", slug)

    console.print(f"\n[bold cyan]{permit_name}[/bold cyan] ({slug}) — {permit_type} page\n")

    # Main page text (HTML permits only)
    text_path = permit_dir / "page_text.md"
    if text_path.exists():
        main_chars = len(text_path.read_text(encoding="utf-8"))
        console.print(f"Main page: {main_chars:,} chars\n")
        console.print(Markdown(text_path.read_text(encoding="utf-8")))
    elif permit_type == "pdf_only":
        console.print("[dim]PDF-only permit — no HTML page[/dim]\n")

    # References breakdown
    items = refs.get("items", [])
    pdf_items = [i for i in items if i.get("type") == "pdf"]
    sub_items = [i for i in items if i.get("type") == "subpage"]

    unique_pdfs = len({i.get("hash") for i in pdf_items if i.get("hash")})
    shared_pdfs = sum(1 for i in pdf_items
                      if len(pdf_reg.get(i.get("url",""), {}).get("referenced_by_permits", [])) > 1)
    unique_subs = sum(1 for i in sub_items if i.get("crawled", True))
    shared_subs = sum(1 for i in sub_items
                      if len(sub_reg.get(i.get("url",""), {}).get("referenced_by_permits", [])) > 1)

    console.print(f"\n[bold]References:[/bold] {len(pdf_items)} PDFs "
                  f"({unique_pdfs} unique, {shared_pdfs} shared), "
                  f"{len(sub_items)} subpages "
                  f"({unique_subs} crawled, {shared_subs} shared)")

    if pdf_items:
        console.print("  [bold]PDFs:[/bold]")
        for item in pdf_items:
            h = item.get("hash", "")
            h8 = h[:8] if h else "?"
            url = item.get("url", "")
            n = len(pdf_reg.get(url, {}).get("referenced_by_permits", []))
            share_str = f"shared by {n} permits" if n > 1 else "unique to this permit"
            console.print(f"    {item.get('name', h8)} ({h8}) — {share_str}")

    if sub_items:
        console.print("  [bold]Subpages:[/bold]")
        _print_sub_tree(sub_items, sub_reg, indent="    ")

    # Total content estimate
    total_chars = (len(text_path.read_text(encoding="utf-8")) if text_path.exists() else 0)
    for item in sub_items:
        sub_slug = item.get("slug", "")
        if sub_slug:
            p = SUBPAGE_DIR / sub_slug / "page_text.md"
            if p.exists():
                total_chars += len(p.read_text(encoding="utf-8"))
    for item in pdf_items:
        h = item.get("hash", "")
        if h:
            p = PDF_DIR / f"{h}.txt"
            if p.exists():
                total_chars += len(p.read_text(encoding="utf-8"))

    console.print(f"\nTotal assembled content: {total_chars:,} chars (~{total_chars//4:,} tokens)")


def _print_sub_tree(sub_items: list[dict], sub_reg: dict, indent: str):
    for i, item in enumerate(sub_items):
        is_last = (i == len(sub_items) - 1)
        connector = "└─" if is_last else "├─"
        url = item.get("url", "")
        n = len(sub_reg.get(url, {}).get("referenced_by_permits", []))
        share_str = f"shared by {n} permits" if n > 1 else "unique"
        crawled = item.get("crawled", True)
        status = "" if crawled else " [dim](not crawled)[/dim]"
        console.print(f"{indent}{connector} {item.get('name', item.get('slug','?'))} — {share_str}{status}")

        # Sub-items from this subpage's references.json
        sub_slug = item.get("slug", "")
        if sub_slug and crawled:
            sub_refs_path = SUBPAGE_DIR / sub_slug / "references.json"
            if sub_refs_path.exists():
                with open(sub_refs_path) as f:
                    sub_refs = json.load(f)
                child_subs = [x for x in sub_refs.get("items", []) if x.get("type") == "subpage"]
                if child_subs:
                    child_indent = indent + ("   " if is_last else "│  ")
                    _print_sub_tree(child_subs, sub_reg, child_indent)


def show_tree(slug: str):
    tree_path = config.RAW_DIR / "_crawl_tree.json"
    if not tree_path.exists():
        console.print("[yellow]No _crawl_tree.json found.[/yellow]")
        return
    with open(tree_path) as f:
        tree = json.load(f)
    if slug not in tree:
        console.print(f"[yellow]No tree entry for '{slug}'[/yellow]")
        return

    def _print(node: dict, prefix: str = "", is_last: bool = True):
        depth = node.get("depth", 0)
        connector = ("└─" if is_last else "├─") if depth > 0 else ""
        label = node.get("anchor_text") or node.get("name") or node.get("slug") or node.get("url", "?")
        status = node.get("status_code") or "?"
        ms = node.get("fetch_time_ms", 0)
        action = node.get("action", "")
        kind = node.get("type", "")

        if kind == "pdf":
            h8 = (node.get("hash") or "")[:8]
            action_str = f"[green]{action}[/green]" if action == "downloaded" else f"[dim]{action}[/dim]"
            console.print(f"{prefix}{connector} [cyan][PDF][/cyan] {label} — {action_str}, hash: {h8}")
        elif depth == 0:
            n_pdf = sum(1 for c in node.get("children", []) if c.get("type") == "pdf")
            n_sub = sum(1 for c in node.get("children", []) if c.get("type") == "subpage")
            console.print(f"[bold]{label}[/bold]  {status} OK, "
                          f"{n_pdf} PDFs, {n_sub} subpages ({ms/1000:.1f}s)")
        else:
            action_str = f"[green]{action}[/green]" if action == "crawled" else f"[dim]{action}[/dim]"
            console.print(f"{prefix}{connector} [yellow][d{depth}][/yellow] {label} — "
                          f"{action_str} ({ms/1000:.1f}s)")

        children = node.get("children", [])
        child_prefix = prefix + ("   " if is_last else "│  ")
        for i, child in enumerate(children):
            _print(child, child_prefix, is_last=(i == len(children) - 1))

    _print(tree[slug])


def show_subpage(slug: str, subpage_slug: str):
    """Show extracted text for a specific shared subpage."""
    # Look up by slug in the registry
    sub_reg: dict = {}
    if (SHARED_DIR / "subpage_registry.json").exists():
        with open(SHARED_DIR / "subpage_registry.json") as f:
            sub_reg = json.load(f)

    # Find matching slug (partial match ok)
    matched_slug = None
    for url, entry in sub_reg.items():
        if entry.get("slug") == subpage_slug or subpage_slug in entry.get("slug", ""):
            matched_slug = entry["slug"]
            break

    if not matched_slug:
        console.print(f"[yellow]No subpage matching '{subpage_slug}' in registry.[/yellow]")
        # Show available
        permit_dir = config.RAW_DIR / slug
        refs = load_refs(permit_dir)
        sub_items = [i for i in refs.get("items", []) if i.get("type") == "subpage"]
        if sub_items:
            console.print("Subpages for this permit:")
            for item in sub_items:
                console.print(f"  {item.get('slug')} — {item.get('name', '')}")
        return

    sub_dir = SUBPAGE_DIR / matched_slug
    txt_path = sub_dir / "page_text.md"
    if not txt_path.exists():
        console.print(f"[yellow]No page_text.md for subpage '{matched_slug}'[/yellow]")
        return

    console.print(f"[bold cyan]{matched_slug}[/bold cyan]\n")
    console.print(Markdown(txt_path.read_text(encoding="utf-8")))


def show_pdf(slug: str, hash_prefix: str):
    """Show extracted text for a PDF by hash prefix."""
    matches = list(PDF_DIR.glob(f"{hash_prefix}*.txt")) if PDF_DIR.exists() else []
    if not matches:
        # Fall back: find PDFs referenced by this permit
        permit_dir = config.RAW_DIR / slug
        refs = load_refs(permit_dir)
        pdf_items = [i for i in refs.get("items", []) if i.get("type") == "pdf"]
        console.print(f"[yellow]No PDF matching hash prefix '{hash_prefix}'[/yellow]")
        if pdf_items:
            console.print("PDFs for this permit:")
            for item in pdf_items:
                h = item.get("hash", "")
                console.print(f"  {h[:8]}  {item.get('name', '?')}")
        return
    text = matches[0].read_text(encoding="utf-8")
    console.print(f"[bold]PDF: {matches[0].stem[:8]}...[/bold]\n")
    console.print(text)


def show_links(slug: str):
    permit_dir = config.RAW_DIR / slug
    links_path = permit_dir / "links_found.json"
    if not links_path.exists():
        console.print("[yellow]No links_found.json[/yellow]")
        return
    with open(links_path) as f:
        links = json.load(f)
    table = Table(title=f"Links for {slug}")
    table.add_column("Type", style="cyan")
    table.add_column("Text", style="bold")
    table.add_column("URL", style="dim")
    for link in links:
        table.add_row(link["type"], link["text"], link["href"])
    console.print(table)


def show_stats():
    """Aggregate crawl stats across all permits."""
    pdf_reg, sub_reg = load_registries()
    permit_dirs = [d for d in sorted(config.RAW_DIR.iterdir())
                   if d.is_dir() and not d.name.startswith("_")]

    table = Table(title="Crawl Stats by Permit")
    table.add_column("Permit", style="cyan")
    table.add_column("Type", style="dim")
    table.add_column("Main chars", justify="right")
    table.add_column("PDFs refs", justify="right", style="green")
    table.add_column("Subpage refs", justify="right", style="yellow")
    table.add_column("Shared PDFs", justify="right")
    table.add_column("Shared subs", justify="right")

    rows = []
    for permit_dir in permit_dirs:
        slug = permit_dir.name
        refs = load_refs(permit_dir)
        ptype = refs.get("permit_type", "?")
        main_chars = 0
        txt = permit_dir / "page_text.md"
        if txt.exists():
            main_chars = len(txt.read_text(encoding="utf-8"))

        items = refs.get("items", [])
        pdf_items = [i for i in items if i.get("type") == "pdf"]
        sub_items = [i for i in items if i.get("type") == "subpage"]
        n_shared_pdfs = sum(1 for i in pdf_items
                            if len(pdf_reg.get(i.get("url",""), {}).get("referenced_by_permits", [])) > 1)
        n_shared_subs = sum(1 for i in sub_items
                            if len(sub_reg.get(i.get("url",""), {}).get("referenced_by_permits", [])) > 1)
        rows.append((slug, ptype, main_chars, len(pdf_items), len(sub_items),
                     n_shared_pdfs, n_shared_subs))

    rows.sort(key=lambda r: r[4] + r[3], reverse=True)  # sort by total refs
    for slug, ptype, chars, n_pdf, n_sub, sh_pdf, sh_sub in rows:
        table.add_row(slug, ptype, f"{chars:,}", str(n_pdf), str(n_sub),
                      str(sh_pdf) if sh_pdf else "", str(sh_sub) if sh_sub else "")

    console.print(table)
    unique_pdfs = len(pdf_reg)
    unique_subs = sum(1 for e in sub_reg.values() if e.get("crawled"))
    total_pdf_refs = sum(len(e.get("referenced_by_permits", [])) for e in pdf_reg.values())
    console.print(f"\nUnique PDFs: {unique_pdfs} ({total_pdf_refs} total refs)")
    console.print(f"Unique subpages: {unique_subs} crawled")


def main():
    parser = argparse.ArgumentParser(description="View raw permit data")
    parser.add_argument("--permit", type=str, help="Permit slug")
    parser.add_argument("--links", action="store_true", help="Show links_found.json")
    parser.add_argument("--pdf", type=str, help="Show extracted PDF text by hash prefix")
    parser.add_argument("--tree", action="store_true", help="Show full crawl tree")
    parser.add_argument("--subpage", type=str, help="Show specific subpage by slug")
    parser.add_argument("--stats", action="store_true", help="Show aggregate crawl stats")
    args = parser.parse_args()

    if args.stats:
        show_stats()
        return

    if not args.permit:
        parser.print_help()
        sys.exit(1)

    permit_dir = config.RAW_DIR / args.permit
    if not permit_dir.exists():
        console.print(f"[red]No data for permit '{args.permit}'. "
                      f"Run 02_crawl_permit_pages.py first.[/red]")
        sys.exit(1)

    if args.tree:
        show_tree(args.permit)
        return

    if args.subpage:
        show_subpage(args.permit, args.subpage)
        return

    if args.links:
        show_links(args.permit)
        return

    if args.pdf:
        show_pdf(args.permit, args.pdf)
        return

    pdf_reg, sub_reg = load_registries()
    show_default(args.permit, pdf_reg, sub_reg)


if __name__ == "__main__":
    main()
