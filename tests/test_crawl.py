"""Tests for the index crawler."""

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
import config


def test_index_exists():
    """Verify the index file was created."""
    path = config.INDEX_DIR / "permits_index.json"
    assert path.exists(), "permits_index.json not found — run 01_crawl_index.py first"


def test_index_has_permits():
    """Verify we found a reasonable number of permits."""
    path = config.INDEX_DIR / "permits_index.json"
    if not path.exists():
        return  # Skip if not yet crawled

    with open(path) as f:
        permits = json.load(f)

    assert len(permits) > 40, f"Expected > 40 permits, found {len(permits)}"


def test_known_permits_present():
    """Verify that known permits are in the index."""
    path = config.INDEX_DIR / "permits_index.json"
    if not path.exists():
        return

    with open(path) as f:
        permits = json.load(f)

    names_lower = [p["permit_name"].lower() for p in permits]
    all_text = " ".join(names_lower)

    known = ["adu", "pool", "re-roof"]
    for term in known:
        assert term.lower() in all_text, f"Expected permit containing '{term}' in index"


def test_permit_schema():
    """Verify each permit has required fields."""
    path = config.INDEX_DIR / "permits_index.json"
    if not path.exists():
        return

    with open(path) as f:
        permits = json.load(f)

    required_fields = ["permit_name", "permit_url", "department", "slug"]
    for permit in permits:
        for field in required_fields:
            assert field in permit, f"Missing field '{field}' in permit: {permit.get('permit_name', '?')}"
        assert permit["permit_url"].startswith("http"), f"Bad URL: {permit['permit_url']}"
        assert permit["slug"], f"Empty slug for {permit['permit_name']}"


def test_crawl_log():
    """Verify crawl log was created with expected fields."""
    path = config.INDEX_DIR / "crawl_log.json"
    if not path.exists():
        return

    with open(path) as f:
        log = json.load(f)

    assert "status_code" in log
    assert "total_permits" in log
    assert log["status_code"] == 200
    assert log["total_permits"] > 0


if __name__ == "__main__":
    tests = [
        test_index_exists,
        test_index_has_permits,
        test_known_permits_present,
        test_permit_schema,
        test_crawl_log,
    ]
    passed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS: {test.__name__}")
            passed += 1
        except (AssertionError, Exception) as e:
            print(f"  FAIL: {test.__name__} — {e}")

    print(f"\n{passed}/{len(tests)} tests passed")
