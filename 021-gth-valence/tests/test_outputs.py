from pathlib import Path

OUT_PATH = Path("/app/out.txt")


def test_file_exists():
    assert OUT_PATH.is_file(), "out.txt not found"


def test_valence_electrons():
    got = OUT_PATH.read_text().strip()
    assert got == "6", f"GTH-PBE-q6 on O treats 6 valence electrons, got {got!r}"
