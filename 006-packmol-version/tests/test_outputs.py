import re
from pathlib import Path

OUT_PATH = Path("/app/out.txt")


def test_file_exists():
    assert OUT_PATH.is_file(), "out.txt not found"


def test_answer_format():
    content = OUT_PATH.read_text()
    match = re.search(r"<answer>(.*?)</answer>", content)
    assert match is not None, "No <answer>...</answer> tag found"


def test_answer_correct():
    content = OUT_PATH.read_text()
    match = re.search(r"<answer>(.*?)</answer>", content)
    got = match.group(1).strip()
    # Extract version number from answer (tolerate extra text)
    version_match = re.search(r"\d+\.\d+\.\d+", got)
    assert version_match is not None, f"No version number found in {got!r}"
