#!/usr/bin/env python3
"""Script 1: Crawl the Encinitas Applications & Information index page.

Tries the live site first; if Akamai blocks it (403), falls back to the
most recent Wayback Machine snapshot.

Page structure (confirmed from snapshot):
  .accordion-item[id="{DeptName}"]
    .accordion-heading .title  → department name
    .accordion-content .inner-content
      p > strong > a           → permit name + URL
      p (text after <br>)      → description
"""

import json
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from rich.console import Console
from rich.table import Table

sys.path.insert(0, ".")
import config

console = Console()

WAYBACK_SNAPSHOT = "https://web.archive.org/web/20260208114551/https://www.encinitasca.gov/government/departments/applications-and-information"
WAYBACK_PREFIX_RE = re.compile(r"(?:https?://web\.archive\.org)?/web/\d+/")


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


def strip_wayback(url: str) -> str:
    """Remove the Wayback Machine prefix from a URL."""
    return WAYBACK_PREFIX_RE.sub("", url)


def fetch_with_browser(url: str) -> tuple[int, str]:
    """Fetch using a headed (visible) Chrome window — bypasses Akamai."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, channel="chrome")
        context = browser.new_context()
        page = context.new_page()
        resp = page.goto(url, wait_until="networkidle", timeout=30000)
        status = resp.status if resp else 0
        html = page.content()
        browser.close()
    return status, html


def fetch_html(url: str) -> tuple[int, str, str]:
    """Fetch HTML, returning (status, html, source_label).

    Tries live site with headed Chrome (bypasses Akamai).
    Falls back to Wayback Machine if browser fetch fails.
    """
    # Try headed Chrome against live site
    try:
        console.print("  [dim]Opening Chrome browser...[/dim]")
        status, html = fetch_with_browser(url)
        if status == 200 and "Access Denied" not in html:
            return status, html, "live"
        console.print(f"  [yellow]Browser returned {status} — falling back to Wayback[/yellow]")
    except Exception as e:
        console.print(f"  [yellow]Browser failed ({e}) — falling back to Wayback[/yellow]")

    # Fall back to Wayback snapshot
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "text/html,*/*"}
    console.print(f"  [dim]Fetching Wayback snapshot...[/dim]")
    try:
        resp = requests.get(WAYBACK_SNAPSHOT, headers=headers, timeout=30)
        return resp.status_code, resp.text, "wayback"
    except requests.RequestException as e:
        return 0, "", f"error:{e}"


def parse_permits(html: str, source: str) -> tuple[list[dict], dict]:
    """Parse permit entries from the page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    permits = []
    dept_counts: dict[str, int] = {}

    # Confirmed structure: .accordion-item[id] with .accordion-heading .title
    # and .accordion-content .inner-content containing p > strong > a
    accordion_items = soup.select(".accordion-item")

    if not accordion_items:
        console.print("[yellow]  No .accordion-item elements found — trying fallback selectors[/yellow]")
        return _fallback_parse(soup, source), {}

    console.print(f"  Found {len(accordion_items)} accordion sections")

    for item in accordion_items:
        # Department name: prefer .title div, fall back to id attribute
        title_el = item.select_one(".accordion-heading .title, .accordion-heading")
        if title_el:
            dept_name = title_el.get_text(strip=True)
        else:
            dept_name = item.get("id", "Unknown")

        if not dept_name:
            dept_name = item.get("id", "Unknown")

        content = item.select_one(".accordion-content .inner-content, .accordion-content, .accordion-body")
        if not content:
            continue

        # Each permit is a <p> containing a <strong><a> and a description
        count = 0
        for para in content.find_all("p"):
            link = para.find("a", href=True)
            if not link:
                continue

            name = link.get_text(strip=True)
            if not name or len(name) < 4:
                continue

            raw_url = link.get("href", "")
            # Strip Wayback prefix if present, then make absolute
            clean_url = strip_wayback(raw_url)
            abs_url = urljoin(config.BASE_URL, clean_url)

            # Description: all text in the <p> after the <strong> tag
            # Remove the link/strong text from the paragraph text
            full_para_text = para.get_text(separator=" ", strip=True)
            desc = full_para_text[len(name):].strip().lstrip("–-: ").strip()

            slug = slugify(name)
            if not slug:
                continue

            permits.append({
                "permit_name": name,
                "permit_url": abs_url,
                "description": desc,
                "department": dept_name,
                "slug": slug,
            })
            count += 1

        if count:
            dept_counts[dept_name] = dept_counts.get(dept_name, 0) + count

    return permits, dept_counts


def _fallback_parse(soup, source: str) -> list[dict]:
    """Fallback: scan all bold links in main content."""
    permits = []
    main = soup.select_one("main, #content, .content-area, body")
    if not main:
        return permits

    for strong in main.find_all("strong"):
        link = strong.find("a", href=True)
        if not link:
            continue
        name = link.get_text(strip=True)
        if not name or len(name) < 4:
            continue
        raw_url = link.get("href", "")
        clean_url = strip_wayback(raw_url)
        abs_url = urljoin(config.BASE_URL, clean_url)
        slug = slugify(name)
        if slug:
            permits.append({
                "permit_name": name,
                "permit_url": abs_url,
                "description": "",
                "department": "Unknown",
                "slug": slug,
            })

    return permits


def crawl_index() -> tuple[dict, list]:
    log = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "url": config.INDEX_URL,
        "status_code": None,
        "page_size_bytes": None,
        "source": None,
        "permits_by_department": {},
        "total_permits": 0,
        "errors": [],
    }

    console.print(f"[bold]Crawling index:[/bold] {config.INDEX_URL}")
    status, html, source = fetch_html(config.INDEX_URL)

    log["status_code"] = status
    log["page_size_bytes"] = len(html.encode()) if html else 0
    log["source"] = source

    if not html:
        log["errors"].append(f"No HTML received (status={status}, source={source})")
        console.print(f"[red]Failed to fetch page[/red]")
        return log, []

    if source == "wayback":
        console.print(f"  [cyan]Using Wayback Machine snapshot (2026-02-08)[/cyan]")
    console.print(f"  HTTP {status or 'OK'}, {log['page_size_bytes']:,} bytes, source={source}")

    permits, dept_counts = parse_permits(html, source)

    # Deduplicate by slug
    seen: set[str] = set()
    unique = []
    for p in permits:
        if p["slug"] not in seen:
            seen.add(p["slug"])
            unique.append(p)

    log["permits_by_department"] = dept_counts
    log["total_permits"] = len(unique)

    return log, unique


def main():
    start = time.time()
    log, permits = crawl_index()
    elapsed = time.time() - start

    if permits:
        index_path = config.INDEX_DIR / "permits_index.json"
        with open(index_path, "w") as f:
            json.dump(permits, f, indent=2)
        console.print(f"\n[green]Saved {len(permits)} permits → {index_path}[/green]")

    log["elapsed_seconds"] = round(elapsed, 2)
    with open(config.INDEX_DIR / "crawl_log.json", "w") as f:
        json.dump(log, f, indent=2)

    table = Table(title="Permits by Department")
    table.add_column("Department", style="cyan")
    table.add_column("Count", justify="right", style="green")

    for dept, count in sorted(log["permits_by_department"].items()):
        table.add_row(dept, str(count))
    table.add_row("[bold]TOTAL[/bold]", f"[bold]{log['total_permits']}[/bold]")
    console.print(table)

    if log["total_permits"] == 0:
        console.print("[red bold]ERROR: No permits found![/red bold]")
        sys.exit(1)

    if log["errors"]:
        console.print(f"[red]Errors: {log['errors']}[/red]")

    console.print(f"\nCompleted in {elapsed:.1f}s  [dim](source: {log['source']})[/dim]")


if __name__ == "__main__":
    main()
