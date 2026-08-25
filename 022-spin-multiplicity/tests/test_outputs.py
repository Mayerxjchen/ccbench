from pathlib import Path

OUT_PATH = Path("/app/out.txt")


def test_file_exists():
    assert OUT_PATH.is_file(), "out.txt not found"


def test_multiplicity():
    got = OUT_PATH.read_text().strip()
    assert got == "1", f"Ground-state CH4 is a closed-shell singlet (multiplicity 1), got {got!r}"
