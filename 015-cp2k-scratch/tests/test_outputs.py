import re
from pathlib import Path

OUT_PATH = Path("/app/out.txt")


def test_file_exists():
    assert OUT_PATH.is_file(), f"{OUT_PATH} does not exist"


def test_energy_format():
    content = OUT_PATH.read_text().strip()
    assert re.match(r"-?\d+\.\d+", content), f"Output is not a valid number: {content!r}"


def test_energy_reasonable():
    got = float(OUT_PATH.read_text().strip())
    assert abs(got - (-17.219495884120)) < 1.0, f"Energy {got} is too far from expected"


def test_input_poisson():
    inp_files = list(Path("/app").glob("*.inp"))
    assert len(inp_files) > 0, "No .inp file found"
    content = inp_files[0].read_text().lower()
    assert "poisson" in content, "Input file missing &POISSON section"
    assert "periodic none" in content, "Input file missing PERIODIC NONE in &POISSON"


def test_input_cell():
    inp_files = list(Path("/app").glob("*.inp"))
    content = inp_files[0].read_text().lower()
    assert "cell" in content, "Input file missing &CELL section"
