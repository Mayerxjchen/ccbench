import subprocess
from pathlib import Path

OUT_PATH = Path("/app/out.txt")


def test_file_exists():
    assert OUT_PATH.is_file(), f"{OUT_PATH} does not exist"


def test_version_correct():
    expected = subprocess.check_output(["uv", "--version"]).decode().strip()
    got = OUT_PATH.read_text().strip()
    assert got == expected, f"Expected {expected!r}, got {got!r}"
