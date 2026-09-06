"""Shared runtime sources never dispatch on a literal case id.

The executor registry keys only on ``execution_class``.  Any branch of the
form ``case_id == "NNN"`` / ``task.name == "NNN"`` inside the shared runtime
would re-introduce the per-case coupling Task 11 removes.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# The case-agnostic runtime: everything under ccbench plus the eval
# entrypoint.  Case directories, per-case scripts, and tests are excluded —
# they are the per-case layer by definition.
SHARED_RUNTIME_SOURCES = tuple(
    sorted(p for p in ROOT.glob("ccbench/**/*.py"))
) + (ROOT / "eval.py",)


def test_shared_runtime_sources_have_no_case_id_dispatch() -> None:
    forbidden = re.compile(r"(?:case(?:_id)?|task\.name)\s*==\s*['\"]\d{3}")
    for path in SHARED_RUNTIME_SOURCES:
        assert not forbidden.search(path.read_text()), path
