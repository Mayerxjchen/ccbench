"""Host unit tests for Case 031's pure active-learning helpers.

These exercise ``solution/active_utils.py`` — selection-band semantics, atomic
checkpointing, and resume identity/hash verification — with numpy and the
standard library only, so they run on the host without deepmd or ase. (The
container's ``test.sh`` runs only ``test_outputs.py``; this file is dev-grade.)
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

SOLUTION = Path(__file__).resolve().parents[1] / "solution"
sys.path.insert(0, str(SOLUTION))
from active_utils import (  # noqa: E402
    checkpoint,
    configuration_hash,
    load_checkpoint,
    record_artifact,
    record_dir,
    select_informative,
    sha256,
    stage_complete,
)


class FakeAtoms:
    def __init__(self, cell, positions, symbols):
        self.cell = SimpleNamespace(array=np.asarray(cell, dtype=float))
        self.positions = np.asarray(positions, dtype=float)
        self._symbols = list(symbols)

    def get_chemical_symbols(self):
        return list(self._symbols)


BAND = (0.05, 0.15)


def test_select_informative_band_is_inclusive_on_both_ends() -> None:
    deviation = np.asarray([0.04, 0.05, 0.10, 0.15, 0.16])
    selected = select_informative(deviation, *BAND, cap=10)
    assert selected.tolist() == [1, 2, 3]
    assert deviation[selected].min() >= BAND[0]
    assert deviation[selected].max() <= BAND[1]


def test_select_informative_excludes_out_of_band_without_fallback() -> None:
    deviation = np.asarray([0.01, 0.02, 0.30, 0.99])
    selected = select_informative(deviation, *BAND, cap=10)
    assert selected.size == 0  # no out-of-band fallback: empty, not a bad frame


def test_select_informative_respects_cap() -> None:
    deviation = np.full(20, 0.10)
    selected = select_informative(deviation, *BAND, cap=8)
    assert selected.size == 8


def test_select_informative_cap_larger_than_pool_returns_all() -> None:
    deviation = np.full(3, 0.10)
    selected = select_informative(deviation, *BAND, cap=8)
    assert selected.size == 3


def test_checkpoint_writes_atomically_and_leaves_no_tmp() -> None:
    out = Path(f"/tmp/matclaw-031-checkpoint-{np.random.randint(0, 2**31)}")
    out.mkdir(exist_ok=True)
    try:
        checkpoint(out, {"a": 1, "artifacts": {}})
        assert (out / "checkpoint.json").is_file()
        assert not (out / "checkpoint.json.tmp").exists()
        assert load_checkpoint(out, {}) == {"a": 1, "artifacts": {}}
    finally:
        for path in out.rglob("*"):
            path.unlink()
        out.rmdir()


def test_load_checkpoint_absent_returns_none() -> None:
    out = Path(f"/tmp/matclaw-031-none-{np.random.randint(0, 2**31)}")
    out.mkdir(exist_ok=True)
    try:
        assert load_checkpoint(out, {"profile": "smoke"}) is None
    finally:
        out.rmdir()


def test_load_checkpoint_identity_mismatch_raises() -> None:
    out = Path(f"/tmp/matclaw-031-ident-{np.random.randint(0, 2**31)}")
    out.mkdir(exist_ok=True)
    try:
        checkpoint(out, {"profile": "paper", "seed": 1, "artifacts": {}})
        with pytest.raises(RuntimeError, match="identity mismatch"):
            load_checkpoint(out, {"profile": "paper", "seed": 2})
    finally:
        for path in out.rglob("*"):
            path.unlink()
        out.rmdir()


def test_load_checkpoint_artifact_mismatch_raises() -> None:
    out = Path(f"/tmp/matclaw-031-art-{np.random.randint(0, 2**31)}")
    out.mkdir(exist_ok=True)
    try:
        artifact = out / "result.json"
        artifact.write_text("original")
        state = {"profile": "paper", "seed": 1, "artifacts": {}}
        record_artifact(state, out, artifact)
        checkpoint(out, state)
        artifact.write_text("tampered")  # tamper after commit
        with pytest.raises(RuntimeError, match="artifact mismatch"):
            load_checkpoint(out, {"profile": "paper", "seed": 1})
    finally:
        for path in out.rglob("*"):
            path.unlink()
        out.rmdir()


def test_stage_complete_checks_directories_recursively() -> None:
    out = Path(f"/tmp/matclaw-031-stage-dir-{np.random.randint(0, 2**31)}")
    data = out / "data" / "iteration_0" / "set.000"
    data.mkdir(parents=True, exist_ok=True)
    try:
        (data / "box.npy").write_text("b")
        (data / "coord.npy").write_text("c")
        state = {"artifacts": {}}
        record_dir(state, out, out / "data")
        assert stage_complete(state, out, [out / "data" / "iteration_0"])
        # An unrecorded file inside the directory means the stage is incomplete.
        (out / "data" / "iteration_0" / "extra.txt").write_text("x")
        assert not stage_complete(state, out, [out / "data" / "iteration_0"])
        # A modified recorded file means the stage is incomplete.
        (data / "box.npy").write_text("BB")
        assert not stage_complete(state, out, [out / "data" / "iteration_0"])
    finally:
        import shutil

        shutil.rmtree(out, ignore_errors=True)


def test_stage_complete_requires_every_path_recorded_and_unchanged() -> None:
    out = Path(f"/tmp/matclaw-031-stage-{np.random.randint(0, 2**31)}")
    out.mkdir(exist_ok=True)
    try:
        a = out / "a.txt"
        b = out / "b.txt"
        a.write_text("x")
        b.write_text("y")
        state = {"artifacts": {}}
        record_artifact(state, out, a)
        # Missing second path -> not complete.
        assert not stage_complete(state, out, [a, b])
        record_artifact(state, out, b)
        assert stage_complete(state, out, [a, b])
        a.write_text("z")  # modified after commit
        assert not stage_complete(state, out, [a, b])
    finally:
        for path in out.rglob("*"):
            path.unlink()
        out.rmdir()


def test_record_dir_hashes_every_file() -> None:
    out = Path(f"/tmp/matclaw-031-dir-{np.random.randint(0, 2**31)}")
    (out / "sub").mkdir(parents=True, exist_ok=True)
    try:
        (out / "one.txt").write_text("1")
        (out / "sub" / "two.txt").write_text("22")
        state = {"artifacts": {}}
        record_dir(state, out, out)
        assert len(state["artifacts"]) == 2
        assert state["artifacts"]["one.txt"] == sha256(out / "one.txt")
        assert state["artifacts"]["sub/two.txt"] == sha256(out / "sub" / "two.txt")
    finally:
        import shutil

        shutil.rmtree(out, ignore_errors=True)


def test_configuration_hash_stable_and_order_sensitive() -> None:
    cell = np.eye(3)
    positions = np.asarray([[0.0, 0.0, 0.0], [0.5, 0.5, 0.5]])
    a = FakeAtoms(cell, positions, ["Cu", "S"])
    assert configuration_hash(a) == configuration_hash(a)
    b = FakeAtoms(cell, positions[::-1], ["Cu", "S"])  # reversed atom order
    assert configuration_hash(b) != configuration_hash(a)
    c = FakeAtoms(cell * 1.5, positions, ["Cu", "S"])  # changed cell
    assert configuration_hash(c) != configuration_hash(a)
    d = FakeAtoms(cell, positions, ["Cu", "P"])  # changed species
    assert configuration_hash(d) != configuration_hash(a)
