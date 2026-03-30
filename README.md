# Encinitas Permit Pipeline

Data ingestion pipeline for municipal permit applications from the City of Encinitas, CA. Scrapes permit info, structures it with LLM calls, and builds a searchable knowledge graph.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your ANTHROPIC_API_KEY
```

## Pipeline

Run the full pipeline:
```bash
python scripts/run_all.py
```

Or run individual steps:

| Script | Description |
|--------|-------------|
| `scripts/01_crawl_index.py` | Crawl the permit index page |
| `scripts/02_crawl_permit_pages.py` | Fetch individual permit detail pages and PDFs |
| `scripts/03_extract_structured.py` | Extract structured data via Claude API |
| `scripts/04_build_graph.py` | Build knowledge graph + embeddings |

Scripts 02 and 03 support `--permit <slug>` (single permit) and `--resume` (skip done).

Script 02 also supports `--dry-run` (discover link tree without fetching) and `--max-depth N`.

## Viewers

```bash
python viewers/view_index.py                          # Full index table
python viewers/view_index.py --stats                  # Department counts
python viewers/view_raw.py --permit adu-permit         # Raw page content
python viewers/view_structured.py --permit adu-permit  # Structured data
python viewers/view_structured.py --costs              # LLM costs
python viewers/view_graph.py                           # Graph stats
python viewers/view_graph.py --node adu-permit         # Node connections
python viewers/view_shared.py                          # Shared PDFs + subpages summary
python viewers/view_shared.py --pdf a3f2b8c1           # PDF detail by hash prefix
python viewers/view_shared.py --subpage green-building # Subpage detail
python viewers/view_shared.py --matrix                 # Permit × shared resource matrix
python viewers/dashboard.py                            # Web dashboard at :8050
```

## Search

```bash
python query/search.py "I want to build an ADU in Encinitas"
```

## Tests

```bash
python -m pytest tests/
```
