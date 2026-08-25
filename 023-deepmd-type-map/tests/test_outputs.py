import json
from pathlib import Path

OUT_PATH = Path("/app/out.txt")
EXPECTED = ["H", "C"]


def test_file_exists():
    assert OUT_PATH.is_file(), "out.txt not found"


def test_type_map():
    raw = OUT_PATH.read_text().strip()
    parsed = json.loads(raw)
    assert parsed == EXPECTED, f"Expected {EXPECTED}, got {parsed}"
