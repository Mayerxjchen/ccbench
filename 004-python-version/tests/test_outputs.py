import re
import subprocess
from pathlib import Path

OUT_PATH = Path("/app/out.txt")


def test_file_exists():
    assert OUT_PATH.is_file(), f"{OUT_PATH} does not exist"


def test_answer_format():
    content = OUT_PATH.read_text()
    match = re.search(r"<answer>(.*?)</answer>", content)
    assert match is not None, "No <answer>...</answer> tag found"


def test_answer_correct():
    expected = subprocess.check_output(
        ["python3", "--version"]
    ).decode().strip().split()[-1]
    content = OUT_PATH.read_text()
    match = re.search(r"<answer>(.*?)</answer>", content)
    got = match.group(1).strip()
    # Extract version number from answer (tolerate "Python 3.12.3" etc.)
    version_match = re.search(r"\d+\.\d+\.\d+", got)
    assert version_match is not None, f"No version number found in {got!r}"
    assert version_match.group(0) == expected, f"Expected {expected!r}, got {version_match.group(0)!r}"
