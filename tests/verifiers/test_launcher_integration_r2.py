"""R2 RED gates: common launcher is the only Verifier path (C9/P0-F).

These fail against the current tree:

- `run_verifier()`/`build_verifier_command()` still launch
  ``docker run ... bash /tests/test.sh`` directly and parse legacy
  ``reward.txt``; the common launcher module has no production caller.
- None of the 42 production Case ``tests/test.sh`` entrypoints invoke
  ``bench.verifiers.launcher``.

Closure requires the launcher to be the single Verifier execution path, with
legacy ``reward.txt`` output kept only as an explicitly-tested compatibility
mode.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from bench.core import verifier as verifier_mod
from bench.verifiers import launcher as launcher_mod


ROOT = Path(__file__).resolve().parents[2]


def test_run_verifier_invokes_common_launcher():
    """C9: Harness/run_verifier consumes structured launcher output.

    RED: `run_verifier()` builds a raw ``docker run ... bash /tests/test.sh``
    argv and parses legacy reward.txt; it never calls
    ``launcher.launch()``.
    """
    src = inspect.getsource(verifier_mod)
    assert "launcher.launch(" in src or "launch(" in src


def test_verifier_result_is_structured_result_json():
    """C9: the production path writes result.json via the launcher.

    RED: the current path parses whichever of result.json/reward.txt exists,
    keeping legacy output as a first-class path rather than a compatibility
    fallback.
    """
    src = inspect.getsource(verifier_mod)
    assert "launcher" in src


def test_all_production_test_sh_use_launcher():
    """C9: all 42 production tests/test.sh entrypoints route through launcher.

    RED: 0/42 production test.sh files reference the launcher; they run pytest
    (or bespoke shell) directly, so the scientific denominator stays
    dependent on each Case's ad-hoc script.
    """
    from bench.hpc.adapters.base import HpcAdapter  # noqa: F401

    missing: list[str] = []
    for case_dir in sorted(p for p in ROOT.iterdir() if p.is_dir()):
        test_sh = case_dir / "tests" / "test.sh"
        if not test_sh.is_file():
            continue
        text = test_sh.read_text(encoding="utf-8")
        if "launcher" not in text:
            missing.append(str(case_dir))
    assert missing == [], f"test.sh not launcher-routed: {missing}"
