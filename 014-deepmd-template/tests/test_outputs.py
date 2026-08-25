import re
from pathlib import Path

OUT_PATH = Path("/app/out.txt")
GRAPH_PATH = Path("/app/graph.pb")


def test_out_file_exists():
    assert OUT_PATH.is_file(), "out.txt not found"


def test_graph_exists():
    assert GRAPH_PATH.is_file(), "graph.pb (frozen model) not found"
    assert GRAPH_PATH.stat().st_size > 1000, "graph.pb is too small, training may have failed"


def test_output_format():
    content = OUT_PATH.read_text().strip()
    assert re.match(r"[\d.eE+-]+$", content), f"Output is not a valid number: {content!r}"


def test_rmse_reasonable():
    got = float(OUT_PATH.read_text().strip())
    assert got < 10.0, f"Energy RMSE {got} is unreasonably large"
    assert got > 0.0, f"Energy RMSE {got} should be positive"
