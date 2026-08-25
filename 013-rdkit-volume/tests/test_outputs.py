import re
from pathlib import Path

OUT_PATH = Path("/app/out.txt")


def test_file_exists():
    assert OUT_PATH.is_file(), "out.txt not found"


def test_output_format():
    content = OUT_PATH.read_text().strip()
    assert re.match(r"-?\d+\.\d+", content), f"Output is not a valid number: {content!r}"


def test_volume_range():
    got = float(OUT_PATH.read_text().strip())
    assert 160.0 < got < 170.0, f"Volume {got} is out of reasonable range"


def test_volume_correct():
    expected = 164.62
    got = float(OUT_PATH.read_text().strip())
    assert abs(got - expected) < 1.0, f"Expected ~{expected}, got {got}"
