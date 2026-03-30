#!/usr/bin/env python3
"""View shared resources (deduped PDFs and subpages) across all permits."""

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


def show_summary(pdf_reg: dict, sub_reg: dict):
    """Default view: summary of all shared resources."""
    total_pdf_refs = sum(len(e.get("referenced_by_permits", [])) for e in pdf_reg.values())
    n_permits_with_pdfs = len({p for e in pdf_reg.values()
                                for p in e.get("referenced_by_permits", [])})
    console.print(f"\n[bold]Shared PDFs[/bold]")
    console.print(f"{len(pdf_reg)} unique PDFs, referenced {total_pdf_refs} times "
                  f"across {n_permits_with_pdfs} permits\n")

    top_pdfs = sorted(pdf_reg.items(),
                      key=lambda kv: len(kv[1].get("referenced_by_permits", [])),
                      reverse=True)
    table = Table(title="Top Shared PDFs")
    table.add_column("Hash", style="dim", width=10)
    table.add_column("Filename", style="cyan")
    table.add_column("Permits", justify="right", style="green")
    table.add_column("Size", justify="right", style="dim")
    for url, entry in top_pdfs[:20]:
        n = len(entry.get("referenced_by_permits", []))
        size_kb = entry.get("size_bytes", 0) / 1024
        table.add_row(
            entry.get("hash", "")[:8],
            entry.get("original_filename", "?"),
            str(n),
            f"{size_kb:.0f} KB",
        )
    console.print(table)

    total_sub_refs = sum(len(e.get("referenced_by_permits", [])) for e in sub_reg.values())
    n_crawled = sum(1 for e in sub_reg.values() if e.get("crawled"))
    n_permits_with_subs = len({p for e in sub_reg.values()
                                for p in e.get("referenced_by_permits", [])})
    console.print(f"\n[bold]Shared Subpages[/bold]")
    console.print(f"{n_crawled} unique subpages crawled, referenced {total_sub_refs} times "
                  f"across {n_permits_with_subs} permits\n")

    top_subs = sorted(sub_reg.items(),
                      key=lambda kv: len(kv[1].get("referenced_by_permits", [])),
                      reverse=True)
    table2 = Table(title="Top Shared Subpages")
    table2.add_column("Slug", style="cyan")
    table2.add_column("Title", style="bold")
    table2.add_column("Permits", justify="right", style="green")
    table2.add_column("Size", justify="right", style="dim")
    for url, entry in top_subs[:20]:
        n = len(entry.get("referenced_by_permits", []))
        size = entry.get("size_chars", 0)
        table2.add_row(
            entry.get("slug", "?"),
            entry.get("title", "?")[:60],
            str(n),
            f"{size:,} chars",
        )
    console.print(table2)


def show_pdf_detail(hash_prefix: str, pdf_reg: dict):
    """Show full detail for a PDF by hash prefix."""
    # Find matching registry entry
    matched_url = None
    matched_entry = None
    for url, entry in pdf_reg.items():
        if entry.get("hash", "").startswith(hash_prefix):
            matched_url = url
            matched_entry = entry
            break

    if not matched_entry:
        console.print(f"[yellow]No PDF matching hash prefix '{hash_prefix}'[/yellow]")
        console.print("Available hashes (first 8 chars):")
        for url, e in list(pdf_reg.items())[:20]:
            console.print(f"  {e.get('hash','')[:8]}  {e.get('original_filename','?')}")
        return

    h = matched_entry["hash"]
    console.print(f"\n[bold]{matched_entry.get('original_filename', '?')}[/bold]")
    console.print(f"Hash: {h}")
    console.print(f"Size: {matched_entry.get('size_bytes', 0):,} bytes")
    console.print(f"First seen on: {matched_entry.get('first_seen_on_permit', '?')}")
    console.print(f"Downloaded: {matched_entry.get('download_timestamp', '?')}")
    console.print(f"Source URL: [dim]{matched_url}[/dim]")
    permits = matched_entry.get("referenced_by_permits", [])
    console.print(f"Referenced by {len(permits)} permits: {', '.join(permits)}\n")

    txt_path = PDF_DIR / f"{h}.txt"
    if txt_path.exists():
        console.print("[bold]Extracted text:[/bold]\n")
        console.print(txt_path.read_text(encoding="utf-8"))
    else:
        console.print("[yellow]No extracted text found.[/yellow]")


def show_subpage_detail(subpage_slug: str, sub_reg: dict):
    """Show full detail for a shared subpage."""
    # Find by slug
    matched_url = None
    matched_entry = None
    for url, entry in sub_reg.items():
        if entry.get("slug") == subpage_slug or subpage_slug in entry.get("slug", ""):
            matched_url = url
            matched_entry = entry
            break

    if not matched_entry:
        console.print(f"[yellow]No subpage matching '{subpage_slug}'[/yellow]")
        console.print("Available subpages:")
        for url, e in sorted(sub_reg.items(), key=lambda kv: kv[1].get("slug", "")):
            console.print(f"  {e.get('slug', '?')}  ({e.get('title','')[:50]})")
        return

    slug = matched_entry["slug"]
    sub_dir = SUBPAGE_DIR / slug
    console.print(f"\n[bold]{matched_entry.get('title', slug)}[/bold]")
    console.print(f"Slug: {slug}")
    console.print(f"URL: [dim]{matched_url}[/dim]")
    console.print(f"Crawled: {matched_entry.get('crawled', False)}")
    console.print(f"Size: {matched_entry.get('size_chars', 0):,} chars")
    console.print(f"First seen on: {matched_entry.get('first_seen_on_permit', '?')}")
    permits = matched_entry.get("referenced_by_permits", [])
    console.print(f"Referenced by {len(permits)} permits: {', '.join(permits)}")

    # Further links on this subpage
    links_path = sub_dir / "links_found.json"
    if links_path.exists():
        with open(links_path) as f:
            links = json.load(f)
        pdf_links = [l for l in links if l["type"] == "pdf"]
        sub_links = [l for l in links if l["type"] == "internal_subpage"]
        console.print(f"\nFurther links: {len(pdf_links)} PDFs, {len(sub_links)} subpages")

    # Extracted text
    txt_path = sub_dir / "page_text.md"
    if txt_path.exists():
        console.print("\n[bold]Extracted text:[/bold]\n")
        console.print(Markdown(txt_path.read_text(encoding="utf-8")))
    else:
        console.print("[yellow]No page_text.md found.[/yellow]")


def show_orphans(pdf_reg: dict, sub_reg: dict):
    """Show resources in _shared/ not referenced by any permit."""
    orphan_pdfs = [
        (url, e) for url, e in pdf_reg.items()
        if not e.get("referenced_by_permits")
    ]
    orphan_subs = [
        (url, e) for url, e in sub_reg.items()
        if not e.get("referenced_by_permits")
    ]

    # Also find files on disk not in registry
    disk_pdfs = set(p.stem for p in PDF_DIR.glob("*.pdf")) if PDF_DIR.exists() else set()
    reg_hashes = {e["hash"] for e in pdf_reg.values()}
    unregistered_pdfs = disk_pdfs - reg_hashes

    if not orphan_pdfs and not orphan_subs and not unregistered_pdfs:
        console.print("[green]No orphaned resources found.[/green]")
        return

    if orphan_pdfs:
        console.print(f"\n[bold]Orphaned PDFs[/bold] (in registry but no permit references):")
        for url, e in orphan_pdfs:
            console.print(f"  {e.get('hash','')[:8]}  {e.get('original_filename','?')}")

    if orphan_subs:
        console.print(f"\n[bold]Orphaned subpages[/bold] (in registry but no permit references):")
        for url, e in orphan_subs:
            console.print(f"  {e.get('slug','?')}  {e.get('title','')}")

    if unregistered_pdfs:
        console.print(f"\n[bold]Unregistered PDFs[/bold] (on disk, not in registry):")
        for h in sorted(unregistered_pdfs):
            console.print(f"  {h[:8]}...")


def show_matrix(pdf_reg: dict, sub_reg: dict):
    """Permit × shared-resource matrix (only shared resources with >1 permit)."""
    # Gather all permits from references
    all_permits: set[str] = set()
    for e in pdf_reg.values():
        all_permits.update(e.get("referenced_by_permits", []))
    for e in sub_reg.values():
        all_permits.update(e.get("referenced_by_permits", []))
    permits_sorted = sorted(all_permits)

    # Shared resources only (referenced by >1 permit)
    shared_pdfs = [(e.get("hash","")[:8] + " " + e.get("original_filename","?")[:30], e)
                   for e in pdf_reg.values()
                   if len(e.get("referenced_by_permits", [])) > 1]
    shared_subs = [(e.get("slug","?")[:40], e)
                   for e in sub_reg.values()
                   if len(e.get("referenced_by_permits", [])) > 1]

    resources = shared_pdfs[:20] + shared_subs[:15]  # cap for display

    if not resources:
        console.print("[yellow]No shared resources found (need >1 permit per resource).[/yellow]")
        return

    table = Table(title="Permit × Shared Resource Matrix  (● = referenced)")
    table.add_column("Resource", style="cyan", width=36)
    table.add_column("Kind", style="dim", width=4)
    table.add_column("N", justify="right", style="green", width=4)
    for p in permits_sorted:
        short = p[:12]
        table.add_column(short, justify="center", width=len(short) + 2)

    for label, entry in resources:
        kind = "PDF" if "hash" in entry else "sub"
        n = len(entry.get("referenced_by_permits", []))
        refs = set(entry.get("referenced_by_permits", []))
        cells = ["●" if p in refs else "" for p in permits_sorted]
        table.add_row(label[:36], kind, str(n), *cells)

    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="View shared crawl resources")
    parser.add_argument("--pdf", type=str, metavar="HASH_PREFIX",
                        help="Show detail for a PDF by hash prefix")
    parser.add_argument("--subpage", type=str, metavar="SLUG",
                        help="Show detail for a subpage by slug")
    parser.add_argument("--orphans", action="store_true",
                        help="Show resources not referenced by any permit")
    parser.add_argument("--matrix", action="store_true",
                        help="Show permit × shared-resource matrix")
    args = parser.parse_args()

    if not SHARED_DIR.exists():
        console.print("[yellow]No _shared/ directory found. "
                      "Run 02_crawl_permit_pages.py first.[/yellow]")
        sys.exit(1)

    pdf_reg, sub_reg = load_registries()

    if args.pdf:
        show_pdf_detail(args.pdf, pdf_reg)
        return

    if args.subpage:
        show_subpage_detail(args.subpage, sub_reg)
        return

    if args.orphans:
        show_orphans(pdf_reg, sub_reg)
        return

    if args.matrix:
        show_matrix(pdf_reg, sub_reg)
        return

    show_summary(pdf_reg, sub_reg)


if __name__ == "__main__":
    main()
