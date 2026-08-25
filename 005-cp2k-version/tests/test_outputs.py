import re
import subprocess
from pathlib import Path

OUT_PATH = Path("/app/out.txt")


def test_file_exists():
    assert OUT_PATH.is_file(), f"{OUT_PATH} does not exist"


def test_version_correct():
    got = OUT_PATH.read_text().strip()
    assert re.search(r"2025\.\d+", got), f"Version number not found in {got!r}"
