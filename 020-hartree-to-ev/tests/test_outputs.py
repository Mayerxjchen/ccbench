import re
from pathlib import Path

OUT_PATH = Path("/app/out.txt")
HA = -17.219495884120
FACTOR = 27.211386245988
EXPECTED = round(HA * FACTOR, 8)


def test_file_exists():
    assert OUT_PATH.is_file(), "out.txt not found"


def test_format():
    content = OUT_PATH.read_text().strip()
    assert re.match(r"-?\d+\.\d{8}$", content), f"Expected 8 decimal places, got {content!r}"


def test_value():
    got = float(OUT_PATH.read_text().strip())
    assert abs(got - EXPECTED) < 1e-7, f"Expected {EXPECTED}, got {got}"
