#!/usr/bin/env python3
"""Script 2: Recursively crawl permit detail pages with shared resource deduplication.

Directory structure:
  data/02_raw/
  ├── _shared/
  │   ├── pdfs/{hash}.pdf + {hash}.txt
  │   ├── pdf_registry.json     URL -> {hash, size, first_seen_on, referenced_by_permits, ...}
  │   ├── subpages/{slug}/page.html + page_text.md + links_found.json + references.json
  │   └── subpage_registry.json URL -> {slug, title, size, crawled, first_seen_on, referenced_by, ...}
  ├── {permit-slug}/
  │   ├── page.html + page_text.md + links_found.json  (HTML permits only)
  │   └── references.json  (all PDFs + subpages this permit links to)
  ├── _crawl_log.json
  └── _crawl_tree.json
"""

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urldefrag, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from rich.console import Console

sys.path.insert(0, ".")
import config

console = Console()

# ── Constants ──────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
WAYBACK_TIMESTAMP = "20260208114551"
WAYBACK_PREFIX_RE = re.compile(r"(?:https?://web\.archive\.org)?/web/\d+/")

SHARED_DIR = config.RAW_DIR / "_shared"
PDF_DIR = SHARED_DIR / "pdfs"
SUBPAGE_DIR = SHARED_DIR / "subpages"
PDF_REGISTRY_PATH = SHARED_DIR / "pdf_registry.json"
SUBPAGE_REGISTRY_PATH = SHARED_DIR / "subpage_registry.json"

SKIP_DOMAINS = {
    "web.archive.org", "archive.org", "web-static.archive.org",
    "static.archive.org", "analytics.archive.org",
}
PDF_PATH_PATTERNS = ["showpublisheddocument", "showdocument", "/home/showpublished"]
# Fragments that identify the index / nav pages — don't recurse into these
INDEX_URL_FRAGMENTS = ["applications-and-information", "/government/departments"]
# File extensions we can't extract useful text from
NON_EXTRACTABLE_EXTENSIONS = {
    ".dwg", ".dxf", ".rvt",          # CAD / BIM
    ".xlsx", ".xls", ".xlsm",        # Excel
    ".docx", ".doc",                  # Word
    ".pptx", ".ppt",                  # PowerPoint
    ".zip", ".gz", ".tar",            # Archives
    ".dwf", ".dgn",                   # Other CAD
}

# ── Registry state ─────────────────────────────────────────────────────────────

_pdf_registry: dict = {}
_subpage_registry: dict = {}
_crawl_log: list = []


def load_registries():
    global _pdf_registry, _subpage_registry
    SHARED_DIR.mkdir(parents=True, exist_ok=True)
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    SUBPAGE_DIR.mkdir(parents=True, exist_ok=True)
    if PDF_REGISTRY_PATH.exists():
        with open(PDF_REGISTRY_PATH) as f:
            _pdf_registry = json.load(f)
    if SUBPAGE_REGISTRY_PATH.exists():
        with open(SUBPAGE_REGISTRY_PATH) as f:
            _subpage_registry = json.load(f)


def save_pdf_registry():
    with open(PDF_REGISTRY_PATH, "w") as f:
        json.dump(_pdf_registry, f, indent=2)


def save_subpage_registry():
    with open(SUBPAGE_REGISTRY_PATH, "w") as f:
        json.dump(_subpage_registry, f, indent=2)


# ── URL utilities ──────────────────────────────────────────────────────────────

def strip_wayback(url: str) -> str:
    return WAYBACK_PREFIX_RE.sub("", url)


def normalize_url(url: str) -> str:
    """Strip fragment; normalize trailing slash; preserve query params."""
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    path = parsed.path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, parsed.query, ""))


def slugify_path(url: str) -> str:
    """Create a readable slug from the last 3 URL path components."""
    parsed = urlparse(url)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    slug_parts = parts[-3:] if len(parts) > 3 else parts
    readable = "-".join(re.sub(r"[^\w]", "-", p).strip("-") for p in slug_parts)
    readable = re.sub(r"-+", "-", readable).strip("-")
    return readable[:80] or hashlib.md5(url.encode()).hexdigest()[:16]


def slugify_filename(name: str) -> str:
    name = re.sub(r"[^\w\s.-]", "", name)
    name = re.sub(r"\s+", "_", name)
    return name[:100]


def get_url_extension(url: str) -> str:
    return Path(urlparse(url).path).suffix.lower()


def is_non_extractable_url(url: str) -> bool:
    return get_url_extension(url) in NON_EXTRACTABLE_EXTENSIONS


def is_pdf_url(url: str) -> bool:
    """Check if URL points directly to a PDF (for PDF-only permit detection)."""
    if is_non_extractable_url(url):
        return False  # don't misclassify .xlsx/.dwg as PDF
    lower = url.lower()
    if lower.endswith(".pdf"):
        return True
    path = urlparse(lower).path
    return any(pat in path for pat in PDF_PATH_PATTERNS) or "laserfiche" in lower


def classify_link(href: str) -> str:
    """Classify as pdf / non_extractable / internal_subpage / external / anchor."""
    if href.startswith("#"):
        return "anchor"
    if is_non_extractable_url(href):
        return "non_extractable"
    lower = href.lower()
    path = urlparse(lower).path
    if lower.endswith(".pdf") or any(pat in path for pat in PDF_PATH_PATTERNS):
        return "pdf"
    if "laserfiche" in lower:
        return "pdf"
    netloc = urlparse(href).netloc.lower()
    base_netloc = urlparse(config.BASE_URL).netloc.lower()
    if not netloc or netloc == base_netloc:
        if any(frag in href for frag in INDEX_URL_FRAGMENTS):
            return "external"
        return "internal_subpage"
    return "external"


# ── Page / PDF fetching ────────────────────────────────────────────────────────

def fetch_page(url: str, browser_page=None) -> tuple[int, str, str]:
    """Fetch HTML. Returns (status, html, source_label)."""
    if browser_page is not None:
        try:
            resp = browser_page.goto(url, wait_until="networkidle", timeout=30000)
            status = resp.status if resp else 0
            html = browser_page.content()
            if status == 200 and "Access Denied" not in html:
                return status, html, "live"
        except Exception:
            pass
    wayback_url = f"https://web.archive.org/web/{WAYBACK_TIMESTAMP}/{url}"
    try:
        resp = requests.get(wayback_url, headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            return resp.status_code, resp.text, f"wayback:{WAYBACK_TIMESTAMP}"
    except requests.RequestException:
        pass
    return 0, "", "failed"


def download_pdf_content(url: str, browser_page=None) -> tuple[bytes | None, str]:
    """Download raw PDF bytes. Returns (bytes_or_None, reason)."""
    if browser_page is not None:
        try:
            result = browser_page.evaluate("""
                async (url) => {
                    const resp = await fetch(url);
                    if (!resp.ok) return { ok: false, status: resp.status };
                    const buf = await resp.arrayBuffer();
                    const bytes = new Uint8Array(buf);
                    let binary = '';
                    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
                    return { ok: true, status: resp.status,
                             contentType: resp.headers.get('content-type'), b64: btoa(binary) };
                }
            """, url)
            if result.get("ok") and result.get("b64"):
                import base64
                data = base64.b64decode(result["b64"])
                if data[:4] == b"%PDF":
                    return data, "live"
        except Exception:
            pass

    wayback_url = f"https://web.archive.org/web/{WAYBACK_TIMESTAMP}id_/{url}"
    pdf_headers = {"User-Agent": HEADERS["User-Agent"], "Accept": "*/*"}
    last_reason = "unknown"
    for attempt in range(4):
        if attempt > 0:
            time.sleep(2 ** attempt)
        try:
            resp = requests.get(wayback_url, headers=pdf_headers, timeout=60, stream=True)
            ct = resp.headers.get("content-type", "")
            if resp.status_code == 200 and "pdf" in ct.lower():
                return resp.content, "wayback"
            if resp.status_code in (429, 503):
                last_reason = f"rate-limited ({resp.status_code})"
                continue
            last_reason = f"HTTP {resp.status_code}"
            return None, last_reason
        except requests.RequestException as e:
            last_reason = f"error: {type(e).__name__}"
    return None, last_reason


# ── Content extraction ─────────────────────────────────────────────────────────

def get_main_soup(soup: BeautifulSoup):
    """Return main content element with nav/sidebar/footer stripped."""
    import copy
    s = copy.copy(soup)
    for tag in s.select("nav, footer, header, .sidebar, .nav, .footer, .header, "
                        ".breadcrumb, .menu, script, style, noscript, iframe"):
        tag.decompose()
    return (s.select_one("main, #main-content, .main-content, article, "
                         ".content-area, #content, .page-content, [role='main']")
            or s.find("body") or s)


def extract_main_content(soup: BeautifulSoup) -> str:
    """Extract main content as markdown-like text."""
    main = get_main_soup(soup)
    lines = []
    for el in main.find_all(["h1","h2","h3","h4","h5","h6","p","li","td","th","div","a"]):
        text = el.get_text(strip=True)
        if not text:
            continue
        tag = el.name
        if tag in ("h1","h2"):     lines.append(f"\n# {text}\n")
        elif tag in ("h3","h4"):   lines.append(f"\n## {text}\n")
        elif tag in ("h5","h6"):   lines.append(f"\n### {text}\n")
        elif tag == "li":          lines.append(f"- {text}")
        elif tag == "a":
            href = el.get("href", "")
            if href and not href.startswith(("#", "javascript:")):
                href = strip_wayback(href)
                if href.startswith("/web/") or href.startswith("/screenshot/"):
                    continue
                abs_url = urljoin(config.BASE_URL, href)
                if urlparse(abs_url).netloc not in SKIP_DOMAINS:
                    lines.append(f"[{text}]({abs_url})")
        elif tag in ("p","div") and len(text) > 10:
            lines.append(text)
    result: list[str] = []
    for line in lines:
        if not result or line != result[-1]:
            result.append(line)
    return "\n".join(result)


def extract_links_from_main(soup: BeautifulSoup, base_url: str) -> list[dict]:
    """Extract classified links from main content area only."""
    main = get_main_soup(soup)
    links = []
    seen: set[str] = set()
    for a in main.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        if not href or href.startswith(("javascript:", "mailto:", "tel:")):
            continue
        href = strip_wayback(href)
        if href.startswith("/web/") or href.startswith("/screenshot/"):
            continue
        abs_url = urljoin(base_url, href)
        if urlparse(abs_url).netloc in SKIP_DOMAINS:
            continue
        norm = normalize_url(abs_url)
        if norm in seen:
            continue
        seen.add(norm)
        link_type = classify_link(norm)
        if link_type == "anchor":
            continue
        links.append({"text": text or "(no text)", "href": norm, "type": link_type})
    return links


def extract_pdf_text(pdf_path: Path) -> str:
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        parts = [page.get_text() for page in doc]
        doc.close()
        return "\n".join(parts)
    except Exception as e:
        return f"[PDF text extraction failed: {e}]"


def extract_page_title(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip=True)
    title = soup.find("title")
    if title:
        return title.get_text(strip=True).split("|")[0].strip()
    return ""


def load_index() -> list[dict]:
    path = config.INDEX_DIR / "permits_index.json"
    if not path.exists():
        console.print("[red]permits_index.json not found. Run 01_crawl_index.py first.[/red]")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


# ── PDF processing ─────────────────────────────────────────────────────────────

def process_pdf(
    link: dict,
    permit_slug: str,
    depth: int,
    browser_page,
    dry_run: bool,
    line_prefix: str,
) -> tuple[dict | None, dict]:
    """Handle one PDF link. Prints progress. Returns (reference, tree_node)."""
    url = link["href"]
    norm_url = normalize_url(url)
    name = link.get("text", "(unnamed)")
    tree_node: dict = {"type": "pdf", "url": norm_url, "name": name, "depth": depth}

    if dry_run:
        exists = norm_url in _pdf_registry
        status = "already exists" if exists else "would download"
        console.print(f"{line_prefix}[cyan][PDF][/cyan] {name} — [dim]{status}[/dim]")
        tree_node["action"] = "already_exists" if exists else "would_download"
        return None, tree_node

    # Already in registry?
    if norm_url in _pdf_registry:
        entry = _pdf_registry[norm_url]
        if permit_slug not in entry["referenced_by_permits"]:
            entry["referenced_by_permits"].append(permit_slug)
            save_pdf_registry()
        n = len(entry["referenced_by_permits"])
        share = f", {n} permits" if n > 1 else ""
        h8 = entry["hash"][:8]
        console.print(f"{line_prefix}[cyan][PDF][/cyan] {name} — [dim]already exists (dedup{share}), hash: {h8}[/dim]")
        _crawl_log.append({"action": "already_exists", "type": "pdf", "url": norm_url,
                           "hash": entry["hash"], "permit_slug": permit_slug, "depth": depth,
                           "timestamp": datetime.now(timezone.utc).isoformat()})
        tree_node.update({"action": "already_exists", "hash": entry["hash"]})
        return {"type": "pdf", "name": name, "url": norm_url, "hash": entry["hash"],
                "anchor_text": link.get("text", ""), "depth": depth}, tree_node

    # Download
    pdf_bytes, reason = download_pdf_content(url, browser_page=browser_page)
    if not pdf_bytes:
        console.print(f"{line_prefix}[cyan][PDF][/cyan] {name} — [red]failed: {reason}[/red]")
        _crawl_log.append({"action": "failed", "type": "pdf", "url": norm_url,
                           "reason": reason, "permit_slug": permit_slug, "depth": depth,
                           "timestamp": datetime.now(timezone.utc).isoformat()})
        tree_node.update({"action": "failed", "error": reason})
        return None, tree_node

    content_hash = hashlib.sha256(pdf_bytes).hexdigest()
    h8 = content_hash[:8]

    pdf_path = PDF_DIR / f"{content_hash}.pdf"
    txt_path = PDF_DIR / f"{content_hash}.txt"
    if not pdf_path.exists():
        pdf_path.write_bytes(pdf_bytes)
    if not txt_path.exists():
        txt_path.write_text(extract_pdf_text(pdf_path), encoding="utf-8")

    url_path = Path(urlparse(url).path)
    original_filename = (url_path.name if url_path.suffix.lower() == ".pdf"
                         else slugify_filename(name) + ".pdf")

    _pdf_registry[norm_url] = {
        "hash": content_hash,
        "original_filename": original_filename,
        "size_bytes": len(pdf_bytes),
        "text_extracted": True,
        "first_seen_on_permit": permit_slug,
        "download_timestamp": datetime.now(timezone.utc).isoformat(),
        "referenced_by_permits": [permit_slug],
    }
    save_pdf_registry()

    console.print(f"{line_prefix}[cyan][PDF][/cyan] {name} — [green]downloaded (new)[/green], hash: {h8}")
    _crawl_log.append({"action": "downloaded", "type": "pdf", "url": norm_url,
                       "hash": content_hash, "size_bytes": len(pdf_bytes),
                       "permit_slug": permit_slug, "depth": depth,
                       "timestamp": datetime.now(timezone.utc).isoformat()})
    tree_node.update({"action": "downloaded", "hash": content_hash})
    return {"type": "pdf", "name": name, "url": norm_url, "hash": content_hash,
            "anchor_text": link.get("text", ""), "depth": depth}, tree_node


# ── Subpage processing ─────────────────────────────────────────────────────────

def process_subpage(
    link: dict,
    permit_slug: str,
    subpage_depth: int,
    max_depth: int,
    browser_page,
    dry_run: bool,
    line_prefix: str,
    child_indent: str,
) -> tuple[dict | None, dict]:
    """Handle one internal subpage link recursively. Returns (reference, tree_node)."""
    url = normalize_url(link["href"])
    name = link.get("text", slugify_path(url))
    tree_node: dict = {"type": "subpage", "url": url, "name": name, "depth": subpage_depth}

    # Already in registry (crawled or placeholder)?
    if url in _subpage_registry:
        entry = _subpage_registry[url]
        if permit_slug not in entry["referenced_by_permits"]:
            entry["referenced_by_permits"].append(permit_slug)
            save_subpage_registry()
        n = len(entry["referenced_by_permits"])
        share = f", {n} permits reference this" if n > 1 else ""
        console.print(f"{line_prefix}[yellow][depth {subpage_depth}][/yellow] {name} — "
                      f"[dim]already exists (dedup{share})[/dim]")
        _crawl_log.append({"action": "already_exists", "type": "subpage", "url": url,
                           "slug": entry["slug"], "permit_slug": permit_slug,
                           "depth": subpage_depth,
                           "timestamp": datetime.now(timezone.utc).isoformat()})
        tree_node.update({"action": "already_exists", "slug": entry["slug"]})
        return {"type": "subpage", "name": name, "url": url, "slug": entry["slug"],
                "anchor_text": link.get("text", ""), "depth": subpage_depth,
                "crawled": entry.get("crawled", False)}, tree_node

    # Depth limit
    if subpage_depth > max_depth:
        console.print(f"{line_prefix}[yellow][depth {subpage_depth}][/yellow] {name} — "
                      f"[dim]depth limit[/dim]")
        _crawl_log.append({"action": "depth_limit_reached", "type": "subpage", "url": url,
                           "would_be_depth": subpage_depth, "permit_slug": permit_slug,
                           "timestamp": datetime.now(timezone.utc).isoformat()})
        slug = slugify_path(url)
        tree_node.update({"action": "depth_limit", "slug": slug})
        return {"type": "subpage", "name": name, "url": url, "slug": slug,
                "anchor_text": link.get("text", ""), "depth": subpage_depth,
                "crawled": False}, tree_node

    slug = slugify_path(url)

    # Register placeholder before fetching to prevent cycles
    _subpage_registry[url] = {
        "slug": slug, "crawled": False,
        "first_seen_on_permit": permit_slug,
        "referenced_by_permits": [permit_slug],
    }
    save_subpage_registry()

    if dry_run:
        console.print(f"{line_prefix}[yellow][depth {subpage_depth}][/yellow] {name} — "
                      f"[dim]would crawl[/dim]")
        tree_node.update({"action": "would_crawl", "slug": slug})
        return {"type": "subpage", "name": name, "url": url, "slug": slug,
                "anchor_text": link.get("text", ""), "depth": subpage_depth,
                "crawled": False}, tree_node

    # Fetch
    start = time.time()
    status, html, source = fetch_page(url, browser_page=browser_page)
    elapsed_ms = int((time.time() - start) * 1000)

    if not html or source == "failed":
        console.print(f"{line_prefix}[yellow][depth {subpage_depth}][/yellow] {name} — "
                      f"[red]fetch failed (status={status})[/red]")
        _crawl_log.append({"action": "failed", "type": "subpage", "url": url,
                           "status_code": status, "permit_slug": permit_slug,
                           "depth": subpage_depth,
                           "timestamp": datetime.now(timezone.utc).isoformat()})
        tree_node.update({"action": "failed", "status_code": status})
        return None, tree_node

    soup = BeautifulSoup(html, "html.parser")
    clean_text = extract_main_content(soup)
    title = extract_page_title(soup) or name
    links_found = extract_links_from_main(soup, url)

    sub_dir = SUBPAGE_DIR / slug
    sub_dir.mkdir(parents=True, exist_ok=True)
    (sub_dir / "page.html").write_text(html, encoding="utf-8")
    (sub_dir / "page_text.md").write_text(clean_text, encoding="utf-8")
    (sub_dir / "links_found.json").write_text(json.dumps(links_found, indent=2))

    _subpage_registry[url].update({
        "title": title, "size_chars": len(clean_text),
        "crawled": True, "source": source,
        "crawled_at": datetime.now(timezone.utc).isoformat(),
    })
    save_subpage_registry()

    pdf_count = sum(1 for l in links_found if l["type"] == "pdf")
    int_count = sum(1 for l in links_found if l["type"] == "internal_subpage")
    pdf_str = f", {pdf_count} PDFs" if pdf_count else ""
    int_str = f", {int_count} internal links" if int_count else ""
    console.print(f"{line_prefix}[yellow][depth {subpage_depth}][/yellow] {name} — "
                  f"[green]crawled (new)[/green]{pdf_str}{int_str} ({elapsed_ms/1000:.1f}s)")
    _crawl_log.append({"action": "crawled", "type": "subpage", "url": url, "slug": slug,
                       "status_code": status, "page_size": len(clean_text),
                       "fetch_time_ms": elapsed_ms, "permit_slug": permit_slug,
                       "depth": subpage_depth,
                       "timestamp": datetime.now(timezone.utc).isoformat()})

    # Recurse into this subpage's links
    sub_refs, sub_children = process_links(
        links_found, permit_slug,
        page_depth=subpage_depth, max_depth=max_depth,
        browser_page=browser_page, dry_run=dry_run, indent=child_indent,
    )
    (sub_dir / "references.json").write_text(json.dumps(
        {"url": url, "slug": slug, "items": sub_refs}, indent=2
    ))
    tree_node.update({"action": "crawled", "slug": slug, "status_code": status,
                      "fetch_time_ms": elapsed_ms, "children": sub_children})

    time.sleep(config.CRAWL_DELAY)
    return {"type": "subpage", "name": name, "url": url, "slug": slug,
            "anchor_text": link.get("text", ""), "depth": subpage_depth,
            "crawled": True}, tree_node


def process_non_extractable(
    link: dict,
    permit_slug: str,
    depth: int,
    line_prefix: str,
) -> tuple[dict, dict]:
    """Record a non-extractable file link without downloading. Returns (reference, tree_node)."""
    url = link["href"]
    name = link.get("text", "(unnamed)")
    ext = get_url_extension(url)
    console.print(f"{line_prefix}[dim][{ext}][/dim] {name} — non-extractable, recorded only")
    ref = {"type": "non_extractable", "name": name, "url": url,
           "file_type": ext, "depth": depth}
    node = {"type": "non_extractable", "url": url, "name": name,
            "file_type": ext, "depth": depth, "action": "recorded"}
    return ref, node


def process_links(
    links: list[dict],
    permit_slug: str,
    page_depth: int,
    max_depth: int,
    browser_page,
    dry_run: bool,
    indent: str,
) -> tuple[list, list]:
    """Process all PDF, non-extractable, and subpage links. Returns (references, tree_children)."""
    items = ([(l, "pdf") for l in links if l["type"] == "pdf"] +
             [(l, "non_extractable") for l in links if l["type"] == "non_extractable"] +
             [(l, "sub") for l in links if l["type"] == "internal_subpage"])

    references = []
    tree_children = []

    for i, (link, kind) in enumerate(items):
        is_last = (i == len(items) - 1)
        connector = "└─" if is_last else "├─"
        child_indent = indent + ("   " if is_last else "│  ")
        prefix = f"{indent}{connector} "

        if kind == "pdf":
            ref, node = process_pdf(link, permit_slug, depth=page_depth,
                                    browser_page=browser_page, dry_run=dry_run,
                                    line_prefix=prefix)
        elif kind == "non_extractable":
            ref, node = process_non_extractable(link, permit_slug,
                                                depth=page_depth, line_prefix=prefix)
        else:
            ref, node = process_subpage(link, permit_slug, subpage_depth=page_depth + 1,
                                        max_depth=max_depth, browser_page=browser_page,
                                        dry_run=dry_run, line_prefix=prefix,
                                        child_indent=child_indent)
        if ref is not None:
            references.append(ref)
        tree_children.append(node)

    return references, tree_children


# ── Permit crawling ────────────────────────────────────────────────────────────

def crawl_permit(
    permit: dict,
    resume: bool,
    browser_page,
    max_depth: int,
    dry_run: bool,
) -> dict:
    """Crawl one permit. Returns crawl tree node."""
    slug = permit["slug"]
    url = permit["permit_url"]
    permit_dir = config.RAW_DIR / slug
    permit_dir.mkdir(parents=True, exist_ok=True)
    refs_path = permit_dir / "references.json"

    # Resume: skip if already done
    if resume and refs_path.exists() and not dry_run:
        return {"url": url, "slug": slug, "action": "skipped"}

    # Non-extractable permit URL (.xlsx, .dwg, etc.)
    if is_non_extractable_url(url):
        ext = get_url_extension(url)
        console.print(f"  [dim]Non-extractable permit ({ext}) — recording URL only[/dim]")
        ref = {"type": "non_extractable", "name": permit["permit_name"],
               "url": url, "file_type": ext, "depth": 0}
        if not dry_run:
            refs_path.write_text(json.dumps({
                "permit_slug": slug, "permit_name": permit["permit_name"],
                "permit_type": "non_extractable", "file_type": ext,
                "items": [ref],
            }, indent=2))
            _crawl_log.append({"action": "non_extractable_permit", "slug": slug,
                               "url": url, "file_type": ext,
                               "timestamp": datetime.now(timezone.utc).isoformat()})
        return {"url": url, "slug": slug, "permit_name": permit["permit_name"],
                "type": "non_extractable", "file_type": ext, "children": []}

    # PDF-only permit
    if is_pdf_url(url):
        console.print(f"  [dim]PDF-only permit[/dim]")
        link = {"href": url, "text": permit["permit_name"]}
        ref, tree_node = process_pdf(link, slug, depth=0,
                                     browser_page=browser_page, dry_run=dry_run,
                                     line_prefix="  ")
        if not dry_run:
            refs_path.write_text(json.dumps({
                "permit_slug": slug, "permit_name": permit["permit_name"],
                "permit_type": "pdf_only",
                "items": [ref] if ref else [],
            }, indent=2))
            _crawl_log.append({"action": "pdf_only_permit", "slug": slug, "url": url,
                               "timestamp": datetime.now(timezone.utc).isoformat()})
        return {"url": url, "slug": slug, "permit_name": permit["permit_name"],
                "type": "pdf_only", "children": [tree_node]}

    # HTML permit: fetch main page
    start = time.time()
    status, html, source = fetch_page(url, browser_page=browser_page)
    elapsed_ms = int((time.time() - start) * 1000)

    if not html or source == "failed":
        console.print(f"  [red]FAILED: could not fetch (status={status})[/red]")
        _crawl_log.append({"action": "failed", "type": "permit", "slug": slug, "url": url,
                           "status_code": status,
                           "timestamp": datetime.now(timezone.utc).isoformat()})
        return {"url": url, "slug": slug, "type": "html", "action": "failed",
                "status_code": status, "children": []}

    soup = BeautifulSoup(html, "html.parser")
    clean_text = extract_main_content(soup)
    links = extract_links_from_main(soup, url)

    if not dry_run:
        (permit_dir / "page.html").write_text(html, encoding="utf-8")
        (permit_dir / "page_text.md").write_text(clean_text, encoding="utf-8")
        (permit_dir / "links_found.json").write_text(json.dumps(links, indent=2))

    src_tag = " [dim](wayback)[/dim]" if "wayback" in source else ""
    n_pdf = sum(1 for l in links if l["type"] == "pdf")
    n_int = sum(1 for l in links if l["type"] == "internal_subpage")
    pdf_str = f", {n_pdf} PDFs" if n_pdf else ""
    int_str = f", {n_int} internal links" if n_int else ""
    console.print(f"  Main page... {status} OK{src_tag}, "
                  f"{len(clean_text):,} chars{pdf_str}{int_str} ({elapsed_ms/1000:.1f}s)")

    _crawl_log.append({"action": "crawled", "type": "permit", "slug": slug, "url": url,
                       "status_code": status, "page_size": len(clean_text),
                       "fetch_time_ms": elapsed_ms, "source": source,
                       "timestamp": datetime.now(timezone.utc).isoformat()})

    references, tree_children = process_links(
        links, slug, page_depth=0, max_depth=max_depth,
        browser_page=browser_page, dry_run=dry_run, indent="  ",
    )

    if not dry_run:
        refs_path.write_text(json.dumps({
            "permit_slug": slug, "permit_name": permit["permit_name"],
            "permit_type": "html", "permit_url": url,
            "items": references,
        }, indent=2))

    return {"url": url, "slug": slug, "permit_name": permit["permit_name"],
            "type": "html", "action": "crawled",
            "status_code": status, "page_size_chars": len(clean_text),
            "fetch_time_ms": elapsed_ms, "children": tree_children}


# ── Reclassify existing crawl data ────────────────────────────────────────────

def _reclassify_existing(permits: list[dict]):
    """Re-read existing links_found.json, apply updated link classification,
    and rewrite references.json — no network requests needed."""
    changed = 0
    for permit in permits:
        slug = permit["slug"]
        url = permit["permit_url"]
        permit_dir = config.RAW_DIR / slug
        refs_path = permit_dir / "references.json"

        # Handle permits whose own URL is non-extractable
        if is_non_extractable_url(url):
            ext = get_url_extension(url)
            refs_path.write_text(json.dumps({
                "permit_slug": slug, "permit_name": permit["permit_name"],
                "permit_type": "non_extractable", "file_type": ext,
                "items": [{"type": "non_extractable", "name": permit["permit_name"],
                           "url": url, "file_type": ext, "depth": 0}],
            }, indent=2))
            console.print(f"  {slug}: [yellow]reclassified as non_extractable ({ext})[/yellow]")
            changed += 1
            continue

        links_path = permit_dir / "links_found.json"
        if not links_path.exists():
            continue

        with open(links_path) as f:
            old_links = json.load(f)

        # Re-classify each link and track changes
        new_links = []
        n_changed = 0
        for link in old_links:
            new_type = classify_link(link["href"])
            if new_type != link.get("type"):
                n_changed += 1
            new_links.append({**link, "type": new_type})

        if n_changed == 0:
            continue  # nothing to update for this permit

        # Rewrite links_found.json with corrected classifications
        links_path.write_text(json.dumps(new_links, indent=2))

        # Rebuild references.json from reclassified links
        references = []
        for link in new_links:
            norm = normalize_url(link["href"])
            if link["type"] == "pdf":
                if norm in _pdf_registry:
                    entry = _pdf_registry[norm]
                    references.append({"type": "pdf", "name": link["text"], "url": norm,
                                       "hash": entry["hash"], "anchor_text": link["text"],
                                       "depth": 0})
                # if not in registry: was never downloaded, skip
            elif link["type"] == "non_extractable":
                ext = get_url_extension(link["href"])
                references.append({"type": "non_extractable", "name": link["text"],
                                   "url": norm, "file_type": ext, "depth": 0})
            elif link["type"] == "internal_subpage":
                if norm in _subpage_registry:
                    entry = _subpage_registry[norm]
                    references.append({"type": "subpage", "name": link["text"], "url": norm,
                                       "slug": entry["slug"], "anchor_text": link["text"],
                                       "depth": 0, "crawled": entry.get("crawled", False)})

        # Preserve existing permit_type / permit_name
        existing = {"permit_type": "html"}
        if refs_path.exists():
            with open(refs_path) as f:
                existing = json.load(f)
        existing["items"] = references
        refs_path.write_text(json.dumps(existing, indent=2))

        console.print(f"  {slug}: {n_changed} link(s) reclassified")
        changed += 1

    console.print(f"\n[green]Reclassified {changed} permit(s).[/green]")
    if changed:
        console.print("[dim]Run 'python scripts/03_extract_structured.py --resume' "
                      "to re-extract any permits whose references changed.[/dim]")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Recursively crawl permit pages with shared resource deduplication",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Run modes:
  First run:   python scripts/02_crawl_permit_pages.py
  Resume:      python scripts/02_crawl_permit_pages.py --resume
  Dry run:     python scripts/02_crawl_permit_pages.py --dry-run
  One permit:  python scripts/02_crawl_permit_pages.py --permit <slug>
        """,
    )
    parser.add_argument("--permit", type=str, help="Only crawl this permit slug")
    parser.add_argument("--resume", action="store_true",
                        help="Skip permits that already have references.json")
    parser.add_argument("--max-depth", type=int, default=config.MAX_CRAWL_DEPTH,
                        help=f"Max subpage recursion depth (default {config.MAX_CRAWL_DEPTH})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be crawled/deduped without fetching")
    parser.add_argument("--reclassify-only", action="store_true",
                        help="Re-read existing links_found.json, apply updated classification, "
                             "rewrite references.json — no network requests")
    args = parser.parse_args()

    load_registries()
    permits = load_index()

    if args.permit:
        permits = [p for p in permits if p["slug"] == args.permit]
        if not permits:
            console.print(f"[red]Permit '{args.permit}' not found in index[/red]")
            sys.exit(1)

    if args.reclassify_only:
        _reclassify_existing(permits)
        return

    total = len(permits)
    crawl_trees: dict = {}

    if args.dry_run:
        console.print(f"[bold yellow]DRY RUN — no pages will be fetched[/bold yellow]\n")
        browser_page = None
        pw = None
        browser = None
    else:
        from playwright.sync_api import sync_playwright
        console.print("[dim]Opening headed Chrome browser...[/dim]")
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=False, channel="chrome")
        browser_ctx = browser.new_context()
        browser_page = browser_ctx.new_page()
        try:
            browser_page.goto(config.BASE_URL, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            pass

    try:
        for i, permit in enumerate(permits, 1):
            slug = permit["slug"]
            name = permit["permit_name"]
            console.print(f"\n[{i}/{total}] [bold]{name}[/bold] ({slug})")

            node = crawl_permit(permit, resume=args.resume, browser_page=browser_page,
                                max_depth=args.max_depth, dry_run=args.dry_run)
            crawl_trees[slug] = node

            if node.get("action") == "skipped":
                console.print("  [dim]Skipped (already crawled)[/dim]")
                continue

            if not args.dry_run and i < total:
                time.sleep(config.CRAWL_DELAY)

    finally:
        if browser:
            browser.close()
        if pw:
            pw.stop()

    # Persist outputs
    tree_path = config.RAW_DIR / "_crawl_tree.json"
    with open(tree_path, "w") as f:
        json.dump(crawl_trees, f, indent=2)

    if not args.dry_run:
        log_path = config.RAW_DIR / "_crawl_log.json"
        if args.resume and log_path.exists():
            with open(log_path) as f:
                prev = json.load(f)
            new_slugs = {e.get("slug") for e in _crawl_log}
            final_log = [e for e in prev if e.get("slug") not in new_slugs] + _crawl_log
        else:
            final_log = _crawl_log
        with open(log_path, "w") as f:
            json.dump(final_log, f, indent=2)

    # Summary
    if not args.dry_run:
        n_html = sum(1 for n in crawl_trees.values()
                     if n.get("type") == "html" and n.get("action") == "crawled")
        n_pdf_only = sum(1 for n in crawl_trees.values() if n.get("type") == "pdf_only")
        n_skipped = sum(1 for n in crawl_trees.values() if n.get("action") == "skipped")
        n_failed = sum(1 for n in crawl_trees.values() if n.get("action") == "failed")

        unique_pdfs = len(_pdf_registry)
        unique_subs = sum(1 for e in _subpage_registry.values() if e.get("crawled"))
        total_pdf_refs = sum(len(e.get("referenced_by_permits", [])) for e in _pdf_registry.values())
        total_sub_refs = sum(len(e.get("referenced_by_permits", [])) for e in _subpage_registry.values())

        # Storage saved by dedup
        unique_bytes = sum(e.get("size_bytes", 0) for e in _pdf_registry.values())
        if_no_dedup = sum(
            e.get("size_bytes", 0) * len(e.get("referenced_by_permits", []))
            for e in _pdf_registry.values()
        )
        saved_mb = (if_no_dedup - unique_bytes) / (1024 * 1024)

        top_pdfs = sorted(_pdf_registry.items(),
                          key=lambda kv: len(kv[1].get("referenced_by_permits", [])),
                          reverse=True)[:5]
        top_subs = sorted(_subpage_registry.items(),
                          key=lambda kv: len(kv[1].get("referenced_by_permits", [])),
                          reverse=True)[:5]
        failures = [e for e in _crawl_log if e.get("action") == "failed"]

        console.print(f"\n[bold]Crawl complete.[/bold]")
        console.print(f"  Permits processed: {n_html + n_pdf_only + n_skipped} "
                      f"({n_html} HTML, {n_pdf_only} PDF-only"
                      + (f", {n_skipped} skipped" if n_skipped else "")
                      + (f", [red]{n_failed} failed[/red]" if n_failed else "") + ")")
        console.print(f"  Unique PDFs: {unique_pdfs} downloaded ({total_pdf_refs} total refs)")
        console.print(f"  Unique subpages: {unique_subs} crawled ({total_sub_refs} total refs)")
        if saved_mb > 0.1:
            console.print(f"  Storage saved by dedup: ~{saved_mb:.0f} MB")

        if any(len(e.get("referenced_by_permits", [])) > 1 for _, e in top_pdfs):
            console.print(f"\n  Most shared PDFs:")
            for _, e in top_pdfs:
                n = len(e.get("referenced_by_permits", []))
                if n > 1:
                    console.print(f"    {e.get('original_filename', '?')} — {n} permits")

        if any(len(e.get("referenced_by_permits", [])) > 1 for _, e in top_subs):
            console.print(f"\n  Most shared subpages:")
            for _, e in top_subs:
                n = len(e.get("referenced_by_permits", []))
                if n > 1:
                    console.print(f"    {e.get('title', e['slug'])} — {n} permits")

        if failures:
            console.print(f"\n  [red]Failures: {len(failures)}[/red]")
            for fe in failures[:5]:
                console.print(f"    - {fe.get('slug', fe.get('url', '?'))}: "
                               f"{fe.get('reason', 'unknown')}")


if __name__ == "__main__":
    main()
