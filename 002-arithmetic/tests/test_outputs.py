from pathlib import Path

ANSWERS_PATH = Path("/app/answers.txt")
EXPECTED = ["46", "53", "48", "12", "22"]


def test_file_exists():
    assert ANSWERS_PATH.is_file(), f"{ANSWERS_PATH} does not exist"


def test_line_count():
    lines = ANSWERS_PATH.read_text().splitlines()
    assert len(lines) == len(EXPECTED), (
        f"Expected {len(EXPECTED)} lines, got {len(lines)}"
    )


def test_answers():
    lines = ANSWERS_PATH.read_text().splitlines()
    for i, (got, want) in enumerate(zip(lines, EXPECTED), 1):
        assert got == want, f"Line {i}: expected {want!r}, got {got!r}"
