from pathlib import Path

OUT_PATH = Path("/app/out.txt")


def test_file_exists():
    assert OUT_PATH.is_file(), "out.txt not found"


def test_functional():
    got = OUT_PATH.read_text().strip()
    assert got == "PBE", f"GTH-PBE potentials pair with PBE, got {got!r}"
