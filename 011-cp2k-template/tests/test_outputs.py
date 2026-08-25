import re
from pathlib import Path

OUT_PATH = Path("/app/out.txt")


def test_file_exists():
    assert OUT_PATH.is_file(), f"{OUT_PATH} does not exist"


def test_energy_format():
    content = OUT_PATH.read_text().strip()
    assert re.match(r"-?\d+\.\d+", content), f"Output is not a valid number: {content!r}"


def test_energy_correct():
    expected = -17.219495884120
    got = float(OUT_PATH.read_text().strip())
    assert abs(got - expected) < 1e-6, f"Expected {expected}, got {got}"
