from pathlib import Path

OUT_PATH = Path("/app/packmol_version.txt")


def test_file_exists():
    assert OUT_PATH.is_file(), "packmol_version.txt not found"


def test_file_not_empty():
    content = OUT_PATH.read_text().strip()
    assert len(content) > 0, "packmol_version.txt is empty"


def test_contains_packmol_output():
    content = OUT_PATH.read_text().lower()
    assert "packmol" in content, f"Output does not look like packmol output: {content!r}"
