#!/usr/bin/env python3
"""Script 3: Extract structured data from permit pages using Claude API.

Reads page text and PDF content for each permit, sends to Claude for
structured extraction, and saves the results as JSON.
"""

import argparse
import json
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console

sys.path.insert(0, ".")
import config

console = Console()

SYSTEM_PROMPT = """You are a municipal permit data extraction assistant. Given a permit application page from a city website, extract structured information. Return ONLY valid JSON, no markdown fences, no preamble."""

EXTRACTION_SCHEMA = """{
  "permit_name": "string",
  "slug": "string",
  "department": "string",
  "description": "string - what this permit is for, in plain English a homeowner would understand",
  "review_routing": {
    "divisions": ["string - which city divisions review this"],
    "timeline": "string or null",
    "submission_method": "string - e.g. CSS portal"
  },
  "always_required": [
    {
      "name": "string",
      "description": "string",
      "url": "string or null",
      "document_type": "form | checklist | calculation | plan | study | certification | other"
    }
  ],
  "conditionally_required": [
    {
      "name": "string",
      "description": "string",
      "url": "string or null",
      "condition_text": "string - the human-readable condition",
      "condition_structured": {
        "field": "string - what property/fact triggers this",
        "operator": "string - gt, lt, eq, in, boolean",
        "value": "the threshold or value",
        "source": "where this condition comes from"
      }
    }
  ],
  "related_permits": [
    {
      "name": "string",
      "relationship": "may_also_need | triggers | bundled_with | superseded_by",
      "condition_text": "string or null"
    }
  ],
  "references": [
    {
      "name": "string",
      "url": "string or null",
      "context": "string - why this is referenced"
    }
  ],
  "fees": {
    "description": "string or null",
    "fee_schedule_url": "string or null"
  },
  "process_steps": [
    {
      "step_number": 1,
      "description": "string"
    }
  ],
  "form_fields": [
    {
      "field_name": "string",
      "field_type": "text | number | date | checkbox | signature | file_upload",
      "required": true,
      "shared_fact_type": "string or null - e.g. 'property_address', 'apn', 'owner_name' if this field appears on multiple permits"
    }
  ],
  "plain_english_summary": "string - 2-3 sentence summary a homeowner would understand"
}"""

REQUIRED_FIELDS = [
    "permit_name", "slug", "department", "description",
    "always_required", "conditionally_required", "related_permits",
    "plain_english_summary",
]


MAX_CONTEXT_CHARS = 320_000  # ~80k tokens at 4 chars/token

SHARED_DIR = config.RAW_DIR / "_shared"
PDF_DIR = SHARED_DIR / "pdfs"
SUBPAGE_DIR = SHARED_DIR / "subpages"


def _load_registries() -> tuple[dict, dict]:
    pdf_reg: dict = {}
    sub_reg: dict = {}
    pdf_path = SHARED_DIR / "pdf_registry.json"
    sub_path = SHARED_DIR / "subpage_registry.json"
    if pdf_path.exists():
        with open(pdf_path) as f:
            pdf_reg = json.load(f)
    if sub_path.exists():
        with open(sub_path) as f:
            sub_reg = json.load(f)
    return pdf_reg, sub_reg


def build_prompt(permit: dict) -> tuple[str, dict]:
    """Assemble full context from permit + shared resources. Returns (prompt, stats)."""
    slug = permit["slug"]
    raw_dir = config.RAW_DIR / slug
    pdf_reg, sub_reg = _load_registries()

    sections: list[str] = []
    stats = {"chars": 0, "estimated_tokens": 0, "subpages": 0, "pdfs": 0}

    # Load references.json
    refs_path = raw_dir / "references.json"
    refs: dict = {"permit_type": "html", "items": []}
    if refs_path.exists():
        with open(refs_path) as f:
            refs = json.load(f)

    is_pdf_only = refs.get("permit_type") == "pdf_only"

    # ── Main permit page ──
    if not is_pdf_only:
        page_text = ""
        page_text_path = raw_dir / "page_text.md"
        if page_text_path.exists():
            page_text = page_text_path.read_text(encoding="utf-8")
        else:
            console.print(f"[yellow]Warning: No page_text.md for {slug}[/yellow]")
        sections.append(
            f"=== MAIN PERMIT PAGE ===\n"
            f"Source: {permit.get('permit_url', '')}\n"
            f"{page_text}"
        )

    included_pdfs: set[str] = set()       # hashes already added
    included_subs: set[str] = set()       # URLs already added

    def _add_pdf(item: dict, source_label: str):
        h = item.get("hash")
        if not h or h in included_pdfs:
            return
        txt_path = PDF_DIR / f"{h}.txt"
        if not txt_path.exists():
            return
        text = txt_path.read_text(encoding="utf-8")
        if not text.strip():
            return
        included_pdfs.add(h)
        url = item.get("url", "")
        n = len(pdf_reg.get(url, {}).get("referenced_by_permits", []))
        shared = f", shared by {n} permits" if n > 1 else ""
        sections.append(
            f"=== PDF: {item.get('name', h[:8])} (from {source_label}{shared}) ===\n"
            f"Source: {url}\n{text}"
        )
        stats["pdfs"] += 1

    def _add_subpage(item: dict, from_label: str, header_type: str = "LINKED SUBPAGE"):
        url = item.get("url", "")
        sub_slug = item.get("slug", "")
        if not sub_slug or url in included_subs or not item.get("crawled", True):
            if url:
                included_subs.add(url)
            return
        included_subs.add(url)
        sub_dir = SUBPAGE_DIR / sub_slug
        txt_path = sub_dir / "page_text.md"
        if not txt_path.exists():
            return
        sub_text = txt_path.read_text(encoding="utf-8")
        depth = item.get("depth", 1)
        n = len(sub_reg.get(url, {}).get("referenced_by_permits", []))
        shared = f", shared by {n} permits" if n > 1 else ""
        sections.append(
            f"=== {header_type}: {item.get('name', sub_slug)} "
            f"(depth {depth}{shared}) ===\n"
            f"Source: {url}\n"
            f"Linked from: {from_label} with anchor text: \"{item.get('anchor_text', '')}\"\n"
            f"{sub_text}"
        )
        stats["subpages"] += 1

        # Also include this subpage's own PDFs and sub-subpages
        sub_refs_path = sub_dir / "references.json"
        if sub_refs_path.exists():
            with open(sub_refs_path) as f:
                sub_refs = json.load(f)
            for sub_item in sub_refs.get("items", []):
                if sub_item.get("type") == "pdf":
                    _add_pdf(sub_item, f"subpage: {item.get('name', sub_slug)}")
                elif sub_item.get("type") == "subpage" and sub_item.get("crawled"):
                    _add_subpage(sub_item,
                                 from_label=item.get("name", sub_slug),
                                 header_type="LINKED SUB-SUBPAGE")

    # ── Process all references ──
    for item in refs.get("items", []):
        kind = item.get("type")
        if kind == "pdf":
            depth = item.get("depth", 0)
            source = "main page" if depth == 0 else f"subpage depth {depth}"
            _add_pdf(item, source)
        elif kind == "subpage":
            _add_subpage(item, from_label="main page")

    # ── PDF-only permit: just the PDF text as main content ──
    if is_pdf_only and not sections:
        for item in refs.get("items", []):
            if item.get("type") == "pdf" and item.get("hash"):
                h = item["hash"]
                txt_path = PDF_DIR / f"{h}.txt"
                if txt_path.exists():
                    sections.append(
                        f"=== PERMIT APPLICATION PDF ===\n"
                        f"Source: {item.get('url', '')}\n"
                        f"{txt_path.read_text(encoding='utf-8')}"
                    )
                    stats["pdfs"] += 1

    context = "\n\n".join(sections)
    stats["chars"] = len(context)
    stats["estimated_tokens"] = len(context) // 4

    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n\n[...context truncated due to size...]"
        console.print(f"    [yellow]Context truncated to {MAX_CONTEXT_CHARS:,} chars[/yellow]")

    intro = ("This permit application is a PDF document. Extract structured information from it."
             if is_pdf_only
             else "Extract structured data from this Encinitas, CA permit page.")

    user_prompt = f"""{intro}

{context}

Return a JSON object with this exact schema:
{EXTRACTION_SCHEMA}"""

    return user_prompt, stats


def validate_response(data: dict, slug: str) -> list[str]:
    """Validate extracted data against expected schema. Returns list of warnings."""
    warnings = []
    for field in REQUIRED_FIELDS:
        if field not in data:
            warnings.append(f"Missing field: {field}")
    if not isinstance(data.get("always_required"), list):
        warnings.append("always_required should be a list")
    if not isinstance(data.get("conditionally_required"), list):
        warnings.append("conditionally_required should be a list")
    return warnings


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    """Estimate API cost for Claude Sonnet."""
    # Claude Sonnet pricing: $3/M input, $15/M output
    return (input_tokens * 3 + output_tokens * 15) / 1_000_000


def extract_permit(permit: dict, client, resume: bool = False) -> dict:
    """Extract structured data for a single permit. Returns log entry."""
    slug = permit["slug"]
    output_path = config.STRUCTURED_DIR / f"{slug}.json"
    prompts_dir = config.STRUCTURED_DIR / "_prompts"
    prompts_dir.mkdir(exist_ok=True)

    log_entry = {
        "slug": slug,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_estimate": 0.0,
        "model": config.ANTHROPIC_MODEL,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "success": False,
        "error": None,
        "warnings": [],
        "skipped": False,
        "context_chars": 0,
        "context_tokens_est": 0,
        "subpages_included": 0,
        "pdfs_included": 0,
    }

    if resume and output_path.exists():
        log_entry["skipped"] = True
        return log_entry

    user_prompt, ctx_stats = build_prompt(permit)
    log_entry["context_chars"] = ctx_stats["chars"]
    log_entry["context_tokens_est"] = ctx_stats["estimated_tokens"]
    log_entry["subpages_included"] = ctx_stats["subpages"]
    log_entry["pdfs_included"] = ctx_stats["pdfs"]

    # Save prompt for debugging
    with open(prompts_dir / f"{slug}_prompt.txt", "w", encoding="utf-8") as f:
        f.write(f"SYSTEM:\n{SYSTEM_PROMPT}\n\nUSER:\n{user_prompt}")

    max_retries = 6
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=config.ANTHROPIC_MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            break  # success
        except Exception as e:
            err_str = str(e)
            is_rate_limit = "429" in err_str or "rate_limit" in err_str
            if is_rate_limit and attempt < max_retries - 1:
                wait = 60 * (2 ** attempt)  # 60, 120, 240, 480 ...
                console.print(f"    [yellow]Rate limited — waiting {wait}s before retry "
                               f"({attempt + 1}/{max_retries - 1})...[/yellow]")
                time.sleep(wait)
                continue
            log_entry["error"] = err_str
            return log_entry

    try:
        raw_text = response.content[0].text
        log_entry["input_tokens"] = response.usage.input_tokens
        log_entry["output_tokens"] = response.usage.output_tokens
        log_entry["cost_estimate"] = round(
            estimate_cost(response.usage.input_tokens, response.usage.output_tokens), 6
        )

        # Save raw response
        with open(prompts_dir / f"{slug}_response.txt", "w", encoding="utf-8") as f:
            f.write(raw_text)

        # Parse JSON — handle markdown fences if Claude wraps it
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines)

        data = json.loads(text)

        # Validate
        warnings = validate_response(data, slug)
        log_entry["warnings"] = warnings
        if warnings:
            for w in warnings:
                console.print(f"    [yellow]Warning: {w}[/yellow]")

        data.setdefault("slug", slug)
        data.setdefault("department", permit.get("department", ""))

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        log_entry["success"] = True

    except json.JSONDecodeError as e:
        log_entry["error"] = f"JSON parse error: {e}"
    except Exception as e:
        log_entry["error"] = str(e)

    return log_entry


def main():
    parser = argparse.ArgumentParser(description="Extract structured data from permit pages")
    parser.add_argument("--permit", type=str, help="Only extract this permit slug")
    parser.add_argument("--resume", action="store_true", help="Skip already-extracted permits")
    args = parser.parse_args()

    # Check for API key
    if not config.ANTHROPIC_API_KEY:
        console.print("[red bold]ANTHROPIC_API_KEY not set![/red bold]")
        console.print("Set it in your .env file or environment:")
        console.print("  export ANTHROPIC_API_KEY=your_key_here")
        sys.exit(1)

    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    # Load index
    index_path = config.INDEX_DIR / "permits_index.json"
    if not index_path.exists():
        console.print("[red]permits_index.json not found. Run 01_crawl_index.py first.[/red]")
        sys.exit(1)

    with open(index_path) as f:
        permits = json.load(f)

    if args.permit:
        permits = [p for p in permits if p["slug"] == args.permit]
        if not permits:
            console.print(f"[red]Permit '{args.permit}' not found in index[/red]")
            sys.exit(1)

    total = len(permits)
    extraction_log = []
    total_input = 0
    total_output = 0
    total_cost = 0.0
    successes = 0
    failures = []

    # Rolling window rate limiter: stay under 30k input tokens/minute
    TPM_LIMIT = 30_000
    token_window: deque = deque()  # [(sent_at_timestamp, input_tokens), ...]

    def wait_for_capacity(estimated_tokens: int):
        """Block until there is room in the rolling 60s window for estimated_tokens."""
        while True:
            now = time.time()
            # Drop entries older than 60s
            while token_window and now - token_window[0][0] >= 60:
                token_window.popleft()
            used = sum(t for _, t in token_window)
            available = TPM_LIMIT - used
            if estimated_tokens <= available:
                return
            # Need to wait until the oldest entry expires
            oldest_ts = token_window[0][0]
            sleep_for = (oldest_ts + 60) - now + 0.5  # +0.5s buffer
            console.print(f"    [dim]Rate limit: {used:,}/{TPM_LIMIT:,} tokens used this minute, "
                          f"waiting {sleep_for:.0f}s...[/dim]")
            time.sleep(max(sleep_for, 1))

    for i, permit in enumerate(permits, 1):
        slug = permit["slug"]
        name = permit["permit_name"]

        # Estimate tokens for this permit before calling (4 chars ≈ 1 token)
        raw_dir = config.RAW_DIR / slug
        refs_path = raw_dir / "references.json"
        if refs_path.exists():
            est_tokens = max(len(refs_path.read_text()) // 4, 1000)
        else:
            est_tokens = 5000  # conservative default

        # Block if needed to stay under rate limit
        if token_window:  # skip on first request
            wait_for_capacity(est_tokens)

        entry = extract_permit(permit, client, resume=args.resume)
        extraction_log.append(entry)

        # Record actual tokens sent
        if not entry.get("skipped") and entry.get("input_tokens"):
            token_window.append((time.time(), entry["input_tokens"]))

        if entry["skipped"]:
            console.print(f"  [{i}/{total}] [dim]Skipping (already extracted): {name}[/dim]")
            continue

        if entry["success"]:
            successes += 1
            inp = entry["input_tokens"]
            out = entry["output_tokens"]
            cost = entry["cost_estimate"]
            total_input += inp
            total_output += out
            total_cost += cost
            ctx_tok = entry.get("context_tokens_est", 0)
            subs = entry.get("subpages_included", 0)
            pdfs = entry.get("pdfs_included", 0)
            extra = f", {subs} subpages, {pdfs} PDFs" if subs or pdfs else ""
            console.print(
                f"  [{i}/{total}] Extracting: {name}... "
                f"[green]OK[/green] ({inp:,} in / {out:,} out, ~${cost:.4f}"
                f", ~{ctx_tok:,} ctx tokens{extra})"
            )
        else:
            failures.append(slug)
            console.print(
                f"  [{i}/{total}] Extracting: {name}... "
                f"[red]FAILED: {entry['error']}[/red]"
            )

    # Save extraction log
    log_path = config.STRUCTURED_DIR / "_extraction_log.json"
    with open(log_path, "w") as f:
        json.dump(extraction_log, f, indent=2)

    # Summary
    console.print(f"\n[bold]Summary:[/bold]")
    console.print(f"  Permits extracted: {successes}/{total}")
    console.print(f"  Total tokens: {total_input:,} input, {total_output:,} output")
    console.print(f"  Estimated cost: ${total_cost:.4f}")
    if failures:
        console.print(f"  [red]Failures: {', '.join(failures)}[/red]")
    else:
        console.print("  [green]No failures[/green]")


if __name__ == "__main__":
    main()
