import json
import os
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import verifier  # noqa: E402
from verifier import verify  # noqa: E402

# ---------------------------------------------------------------------------
# Environment / paths
# ---------------------------------------------------------------------------

# The agent workspace. In the harness /app is the real submission; a dev smoke
# run points AI2KIT_034_SUBMISSION at a fixture dir.
APP = Path(os.environ.get("AI2KIT_034_SUBMISSION", "/app"))
EXPECTED_PROFILE = os.environ.get("AI2KIT_PROFILE", "paper")
HIDDEN = Path(__file__).resolve().parent / "hidden"
HIDDEN_VAL = Path(os.environ.get("AI2KIT_HIDDEN_VALIDATION", str(HIDDEN / "dft-validation.extxyz")))
HIDDEN_RDF = Path(os.environ.get("AI2KIT_HIDDEN_RDF_REFERENCE", str(HIDDEN / "rdf-reference.json")))


def _set_hidden_env():
    """Point the verifier's hidden paths at the staged hidden set. In the
    container task.toml already injects them; this makes local runs work."""
    os.environ.setdefault("AI2KIT_HIDDEN_VALIDATION", str(HIDDEN_VAL))
    os.environ.setdefault("AI2KIT_HIDDEN_RDF_REFERENCE", str(HIDDEN_RDF))
    os.environ.setdefault("AI2KIT_THRESHOLDS", str(HIDDEN / "thresholds.json"))


_set_hidden_env()


@pytest.fixture()
def submission(tmp_path: Path) -> Path:
    if not APP.is_dir():
        pytest.skip(f"no submission at {APP}; run the workflow before the oracle")
    target = tmp_path / "submission"
    shutil.copytree(APP, target)
    return target


def _find_round_dirs(sub: Path):
    rounds = []
    for p in sub.rglob("input.json"):
        rounds.append(p.parent)
    return sorted(rounds)


def _training_frame_sources(sub: Path):
    """Yield (kind, path) for every source the verifier reads as training
    frames: DeepMD set npy dirs AND labeled extxyz files. A fixture that wants
    to make training frames lie must touch BOTH, or the untouched source still
    supplies honest frames."""
    for s in verifier.find_training_sets(sub):
        yield ("set", Path(s["root"]) / "coord.npy")
    for e in verifier.find_labeled_extxyz(sub):
        yield ("extxyz", Path(e["path"]))


def _transform_extxyz_positions(path: Path, transform) -> int:
    """Apply `transform(pos) -> pos` (same natoms) to every frame's positions
    in a labeled extxyz, preserving species, forces and the comment line
    (energy/lattice) so the file still parses as a labeled extxyz."""
    lines = path.read_text().splitlines()
    out = []
    i = 0
    n = 0
    while i < len(lines):
        natoms = int(lines[i])
        comment = lines[i + 1]
        atom_lines = lines[i + 2: i + 2 + natoms]
        i += 2 + natoms
        pos = np.array([[float(c) for c in ln.split()[1:4]] for ln in atom_lines])
        new_pos = np.asarray(transform(pos), dtype=float)
        out.append(str(natoms))
        out.append(comment)
        for a, ln in enumerate(atom_lines):
            cols = ln.split()
            cols[1:4] = [f"{new_pos[a, k]:.8f}" for k in range(3)]
            out.append("  ".join(cols))
        n += 1
    path.write_text("\n".join(out) + "\n")
    return n


def _forge_training_frames(sub: Path, noise_sigma: float = 5.0) -> int:
    """Overwrite every training-frame source with large positional noise so no
    frame can trace to a real CP2K output (fabricated labels)."""
    rng = np.random.RandomState(3)
    touched = 0
    for kind, path in _training_frame_sources(sub):
        if kind == "set":
            arr = np.load(str(path))
            np.save(path, arr + rng.normal(0, noise_sigma, arr.shape))
        else:
            touched += _transform_extxyz_positions(path,
                                                   lambda p: p + rng.normal(0, noise_sigma, p.shape))
        touched += 1
    return touched


def _force_duplicate_training_frames(sub: Path) -> int:
    """Overwrite every training-frame source with the primary AIMD's frame-0
    positions, so every training frame equals an existing AIMD frame (the AL
    loop added no genuinely new configurations)."""
    aimd = verifier.find_cp2k_aimd(sub)
    if aimd is None or not aimd["pos_frames"]:
        return 0
    base = verifier._frame_positions(aimd["pos_frames"][0])
    touched = 0
    for kind, path in _training_frame_sources(sub):
        if kind == "set":
            n = int(np.load(str(path)).shape[0])
            np.save(path, np.tile(base.reshape(1, -1, 3), (n, 1, 1)))
        else:
            touched += _transform_extxyz_positions(path, lambda p: base)
        touched += 1
    return touched


# ---------------------------------------------------------------------------
# Positive integration tests (need the real oracle at APP)
# ---------------------------------------------------------------------------

def _has_deepmd() -> bool:
    try:
        import deepmd  # noqa: F401
        return True
    except Exception:
        return False


needs_deepmd = pytest.mark.skipif(not _has_deepmd(),
                                  reason="deepmd not installed; full L0-L9 pass requires the container")


@needs_deepmd
def test_oracle_workspace_passes_all_levels(submission: Path) -> None:
    report = verify(submission, EXPECTED_PROFILE)
    assert report["valid"], report["errors"]


@needs_deepmd
def test_alt_valid_renamed_rounds_still_passes(submission: Path) -> None:
    """The verifier must not depend on ai2-kit iteration naming. Repackage the
    rounds under arbitrary names and it must still pass."""
    rounds = _find_round_dirs(submission)
    if len(rounds) < 2:
        pytest.skip("need >=2 training rounds to repackage")
    renamed = []
    for i, r in enumerate(rounds):
        new = r.parent / f"my_round_{i:03d}"
        if new.exists():
            shutil.rmtree(new)
        shutil.move(str(r), str(new))
        renamed.append(new)
    report = verify(submission, EXPECTED_PROFILE)
    assert report["valid"], report["errors"]


# ---------------------------------------------------------------------------
# Structural negatives (tamper a copy of the real submission)
# ---------------------------------------------------------------------------

def test_manifest_only_no_model_fails(submission: Path) -> None:
    manifest = json.loads((submission / "final" / "manifest.json").read_text())
    # find_model_files resolves root/rel AND root/final/rel — delete every
    # path the verifier could resolve so no model survives the tamper.
    for rel in manifest.get("model_files", []):
        for cand in (submission / rel, submission / "final" / rel):
            if cand.is_file():
                cand.unlink()
    # belt-and-suspenders: also nuke any committee copies under final/models.
    for cand in (submission / "models/final").rglob("*.pb"):
        cand.unlink()
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert not report["levels"]["L5"]["ok"]


def test_corrupt_model_fails(submission: Path) -> None:
    manifest = json.loads((submission / "final" / "manifest.json").read_text())
    models = [submission / rel for rel in manifest.get("model_files", []) if (submission / rel).is_file()]
    if not models:
        pytest.skip("no model files to corrupt")
    models[0].write_bytes(models[0].read_bytes() + b"TAMPERED")
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert not report["levels"]["L5"]["ok"]


def test_forged_labels_fail(submission: Path) -> None:
    """Training frames that trace to NO real CP2K output are fabricated.
    Forge EVERY training-frame source (set npy AND labeled extxyz) with large
    positional noise so L3's sampled trace-to-CP2K check (>=90% must match)
    drops well below threshold."""
    touched = _forge_training_frames(submission)
    if touched == 0:
        pytest.skip("no training-frame sources to forge")
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert not report["levels"]["L3"]["ok"]


def test_no_aimd_reference_data_fails(submission: Path) -> None:
    """Only GEO_OPT, no CP2K reference-data trajectory -> L3 fails. Deletes
    EVERY raw CP2K output (AIMD pos/frc/ener/cell AND any AL label runs), so
    no coherent reference trajectory remains."""
    raw = list(submission.rglob("*.xyz")) + list(submission.rglob("*.ener")) + list(submission.rglob("*.cell"))
    removed = 0
    for f in raw:
        name = f.name.lower()
        if name.endswith(".ener") or name.endswith(".cell") or "pos-1." in name or "frc-1." in name:
            f.unlink()
            removed += 1
    if removed == 0:
        pytest.skip("no raw CP2K outputs to remove")
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert not report["levels"]["L3"]["ok"]


def test_nonphysical_energies_fail(submission: Path) -> None:
    """SCF-garbage / absurd energies in the AIMD -> L3 fails."""
    aimd = verifier.find_cp2k_aimd(submission)
    if aimd is None or aimd["pos_path"] is None:
        pytest.skip("no AIMD pos file to tamper")
    path = aimd["pos_path"]
    lines = path.read_text().splitlines(keepends=True)
    for idx, line in enumerate(lines):
        if " E = " in line:
            import re
            lines[idx] = re.sub(r"E\s*=\s*[-+0-9.eE]+", "E = 1.0e9", line)
    path.write_text("".join(lines))
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert not report["levels"]["L3"]["ok"]


def test_no_iterative_improvement_fails(submission: Path) -> None:
    """Trained model but no AL loop: remove round artifacts -> L6 fails."""
    rounds = _find_round_dirs(submission)
    if len(rounds) < 2:
        pytest.skip("need >=2 rounds to demonstrate a missing loop")
    for r in rounds:
        for f in r.iterdir():
            if f.name in ("input.json", "lcurve.out", "checkpoint") or f.name.endswith(".pb"):
                f.unlink()
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert not report["levels"]["L6"]["ok"]


def test_dataset_does_not_grow_fails(submission: Path) -> None:
    """The 'new' labels are actually duplicates of the initial training data:
    overwrite EVERY training-frame source (set npy AND labeled extxyz) with the
    primary AIMD's frame-0 positions -> every training frame then equals an
    existing AIMD frame, so L6 finds ZERO newly-labeled configurations (no real
    active learning)."""
    touched = _force_duplicate_training_frames(submission)
    if touched == 0:
        pytest.skip("no training-frame sources to overwrite")
    report = verify(submission, EXPECTED_PROFILE)
    assert not report["valid"]
    assert not report["levels"]["L6"]["ok"]


# ---------------------------------------------------------------------------
# Unit tests of L7/L8/L9 threshold enforcement (no deepmd / no real NVT needed;
# these validate that a genuinely-bad model / unstable NVT / shifted RDF FAIL)
# ---------------------------------------------------------------------------

def _minimal_ctx() -> dict:
    """A ctx carrying a fake primary model, the real hidden frames + thresholds,
    enough for _check_L7/_check_L8/_check_L9."""
    if not HIDDEN_VAL.is_file():
        pytest.skip("hidden validation not staged")
    from ase.io import read as ase_read
    atoms = ase_read(str(HIDDEN_VAL), index=":")
    hidden = [{
        "symbols": np.array(a.get_chemical_symbols()),
        "positions": a.get_positions(),
        "cell": a.get_cell()[:].copy(),
        "energy": verifier._frame_energy_eV(a),
        "forces": verifier._frame_forces(a),
    } for a in atoms]
    thr = verifier.load_thresholds()
    rdf = json.loads(HIDDEN_RDF.read_text()) if HIDDEN_RDF.is_file() else {}
    return {
        "hidden_frames": hidden,
        "thresholds": thr,
        "rdf_reference": rdf,
        "cell": round(float(np.linalg.norm(hidden[0]["cell"][0])), 6),
        "model_files": [Path("/fake/model.pb")],
        "expected_natoms": 192,
    }


class _FakeDP:
    def __init__(self):
        self._tm = ["O", "H"]

    def get_type_map(self):
        return self._tm


def test_l7_hidden_energy_force_bad_fails(monkeypatch) -> None:
    """A model whose hidden-set E/F are garbage must FAIL L7."""
    ctx = _minimal_ctx()
    ctx["primary_model"] = (Path("/fake/model.pb"), _FakeDP(), ["O", "H"])

    def bad_infer(dp, symbols, pos, cell):
        # energy 50 eV/atom off, forces ~5 eV/A random
        e = 50.0 * len(symbols)
        f = np.full((len(symbols), 3), 5.0)
        return float(e), f

    monkeypatch.setattr(verifier, "dp_infer_frame", bad_infer)
    ok, errs, diag = verifier._check_L7(ctx)
    assert not ok
    assert any("hidden" in e and ("RMSE" in e or "energy" in e.lower() or "force" in e.lower()) for e in errs)


def test_l8_unstable_nvt_fails(monkeypatch) -> None:
    """NVT that crashes / loses atoms / blows up temperature must FAIL L8."""
    ctx = _minimal_ctx()
    ctx["primary_model"] = (Path("/fake/model.pb"), _FakeDP(), ["O", "H"])
    f0 = ctx["hidden_frames"][0]
    pos = f0["positions"]

    def unstable_nvt(ctx, dp, tm, steps, temp):
        frames = []
        for i in range(200):
            frames.append({"step": i * 25, "natoms": 192, "positions": pos + i * 0.01,
                           "symbols": f0["symbols"], "types": np.arange(192) % 2 + 1})
        # temperature explodes (NaN / far outside band)
        return {"frames": frames, "temps": [1e6] * 200, "diag": {"nvt_exit": 0}, "error": None}

    monkeypatch.setattr(verifier, "_run_hidden_nvt", unstable_nvt)
    ok, errs, diag = verifier._check_L8(ctx)
    assert not ok
    assert any("temperature" in e.lower() for e in errs)


def test_l9_rdf_shifted_fails(monkeypatch) -> None:
    """RDF first peaks far outside the physical windows must FAIL L9."""
    ctx = _minimal_ctx()
    ctx["primary_model"] = (Path("/fake/model.pb"), _FakeDP(), ["O", "H"])
    f0 = ctx["hidden_frames"][0]
    pos = f0["positions"]
    ctx["nvt_result"] = {
        "frames": [{"step": i, "natoms": 192, "positions": pos, "symbols": f0["symbols"]}
                   for i in range(20)],
        "temps": [300.0] * 20,
        "diag": {},
        "error": None,
    }

    def shifted_peak(r, g, lo, hi):
        # gas-like: no short-range structure; O-O "peak" sits at 5.0 A
        return 5.0

    monkeypatch.setattr(verifier, "first_peak", shifted_peak)
    ok, errs, diag = verifier._check_L9(ctx)
    assert not ok
    assert any("first peak" in e.lower() for e in errs)


def test_rdf_reference_is_self_consistent() -> None:
    """The hidden RDF reference must reproduce the hidden extxyz: recompute the
    RDF from dft-validation.extxyz and check the first peaks agree with
    rdf-reference.json. Guards against a mismatched hidden set."""
    if not HIDDEN_VAL.is_file() or not HIDDEN_RDF.is_file():
        pytest.skip("hidden set not staged")
    from ase.io import read as ase_read
    atoms = ase_read(str(HIDDEN_VAL), index=":")
    poslist = [a.get_positions() for a in atoms]
    symlist = [np.array(a.get_chemical_symbols()) for a in atoms]
    cell = float(np.linalg.norm(atoms[0].get_cell()[0]))
    ref = json.loads(HIDDEN_RDF.read_text())
    peaks = {}
    for pair, label in ((("O", "O"), "OO"), (("O", "H"), "OH"), (("H", "H"), "HH")):
        r, g = verifier.compute_rdf(poslist, symlist, pair, cell)
        peaks[label] = verifier.first_peak(r, g, 0.5, 6.0)
    for label, peak in peaks.items():
        ref_peak = (ref.get(label) or {}).get("first_peak_angstrom")
        assert ref_peak is not None, f"reference lacks {label} peak"
        assert abs(peak - ref_peak) < 0.06, (
            f"hidden RDF reference {label} peak {ref_peak} does not reproduce the "
            f"extxyz recomputation {peak}"
        )
