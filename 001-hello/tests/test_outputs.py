from pathlib import Path

EXPECTED_PATH = Path("/app/hello.txt")
EXPECTED_CONTENT = "hello world"


def test_file_exists():
    assert EXPECTED_PATH.is_file(), f"{EXPECTED_PATH} does not exist"


def test_file_content():
    assert EXPECTED_PATH.read_text() == EXPECTED_CONTENT, (
        f"Expected {EXPECTED_CONTENT!r}, got {EXPECTED_PATH.read_text()!r}"
    )
