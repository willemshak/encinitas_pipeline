"""Tests for structured data extraction."""

import json
import sys

sys.path.insert(0, ".")
import config

REQUIRED_FIELDS = [
    "permit_name",
    "slug",
    "department",
    "description",
    "always_required",
    "conditionally_required",
    "related_permits",
    "plain_english_summary",
]

OPTIONAL_FIELDS = [
    "review_routing",
    "fees",
    "process_steps",
    "form_fields",
    "references",
]


def get_structured_files():
    """Get all structured JSON files."""
    return [p for p in sorted(config.STRUCTURED_DIR.glob("*.json")) if not p.name.startswith("_")]


def test_structured_files_exist():
    """Verify at least one structured file exists."""
    files = get_structured_files()
    assert len(files) > 0, "No structured JSON files found — run 03_extract_structured.py first"


def test_required_fields():
    """Verify all structured files have required fields."""
    files = get_structured_files()
    if not files:
        return

    for path in files:
        with open(path) as f:
            data = json.load(f)
        for field in REQUIRED_FIELDS:
            assert field in data, f"Missing '{field}' in {path.name}"


def test_always_required_schema():
    """Verify always_required items have correct structure."""
    files = get_structured_files()
    if not files:
        return

    for path in files:
        with open(path) as f:
            data = json.load(f)
        for item in data.get("always_required", []):
            assert "name" in item, f"Missing 'name' in always_required item in {path.name}"
            assert "document_type" in item, f"Missing 'document_type' in {path.name}"


def test_conditionally_required_schema():
    """Verify conditionally_required items have conditions."""
    files = get_structured_files()
    if not files:
        return

    for path in files:
        with open(path) as f:
            data = json.load(f)
        for item in data.get("conditionally_required", []):
            assert "name" in item, f"Missing 'name' in conditionally_required in {path.name}"
            assert "condition_text" in item, f"Missing 'condition_text' in {path.name}"


def test_extraction_log():
    """Verify extraction log exists and has expected structure."""
    log_path = config.STRUCTURED_DIR / "_extraction_log.json"
    if not log_path.exists():
        return

    with open(log_path) as f:
        log = json.load(f)

    assert isinstance(log, list)
    for entry in log:
        assert "slug" in entry
        assert "success" in entry or "skipped" in entry


if __name__ == "__main__":
    tests = [
        test_structured_files_exist,
        test_required_fields,
        test_always_required_schema,
        test_conditionally_required_schema,
        test_extraction_log,
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
