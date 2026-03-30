"""Shared configuration for the Encinitas permit pipeline."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# URLs
BASE_URL = "https://www.encinitasca.gov"
INDEX_URL = "https://www.encinitasca.gov/government/departments/applications-and-information"

# Paths
DATA_DIR = Path("data")
INDEX_DIR = DATA_DIR / "01_index"
RAW_DIR = DATA_DIR / "02_raw"
STRUCTURED_DIR = DATA_DIR / "03_structured"
GRAPH_DIR = DATA_DIR / "04_graph"

# Crawl settings
CRAWL_DELAY = 1.5  # seconds between requests
MAX_CRAWL_DEPTH = 2  # max depth for recursive subpage crawling

# LLM settings
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ANTHROPIC_MODEL = "claude-sonnet-4-20250514"

# Auto-create data directories on import
for d in [INDEX_DIR, RAW_DIR, STRUCTURED_DIR, GRAPH_DIR]:
    d.mkdir(parents=True, exist_ok=True)
