"""Dependency-light TDD coverage for Case 031's pure active-selection contract.

``solution/active_contract.py`` deliberately depends only on numpy and the
standard library, so this suite runs on the host and inside the pinned CPU
image without deepmd or ase. It locks the three deterministic rules the
reference workflow and the hidden verifier both rely on:

* inclusive, unique, deterministic selection from the ``[low, high]`` band,
* batch planning that advances without ever relaxing the band,
* exact (order-preserving) dataset growth equal to the unique selection.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

CASE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CASE_ROOT))
from solution.active_contract import (  # noqa: E402
    assert_exact_growth,
    next_batch,
    select_informative,
    select_informative_by_batch,
)
try:  # noqa: E402
    from solution.active_contract import (
        teacher_label_evidence_valid,
        teacher_label_sha256 as contract_teacher_label_sha256,
    )
except ImportError:  # RED: added by the cross-backend evidence contract
    teacher_label_evidence_valid = None  # type: ignore[assignment]
    contract_teacher_label_sha256 = None  # type: ignore[assignment]
from solution.active_utils import sha256  # noqa: E402

# ``solution/run_distillation`` is the reference orchestrator: it needs ase and
# deepmd at import time, so the Task 2 identity/seed helpers can only be imported
# inside the pinned container. Guard the import so the pure contract tests above
# still run on a host without ase/deepmd.
try:  # noqa: E402
    sys.path.insert(0, str(CASE_ROOT / "solution"))
    from solution.run_distillation import (
        resolve_protocol_seed,
        resolve_run_identity,
        resolve_run_seed,
    )
except Exception:  # pragma: no cover - container-only imports
    resolve_protocol_seed = None  # type: ignore[assignment]
    resolve_run_identity = None  # type: ignore[assignment]
    resolve_run_seed = None  # type: ignore[assignment]

# Task 3 adds a bounded, cumulative exploration controller. It is imported in a
# second guarded block so a missing controller fails only the new tests, never
# the Task 2 identity/seed tests, during the RED phase.
try:  # noqa: E402
    from solution.run_distillation import (
        EXHAUSTED_STOP_REASON,
        formal_result_for,
        run_exploration,
    )
except Exception:  # pragma: no cover - container-only imports
    EXHAUSTED_STOP_REASON = None  # type: ignore[assignment]
    formal_result_for = None  # type: ignore[assignment]
    run_exploration = None  # type: ignore[assignment]

# Task 4 adds the convergence gate and the auditable retrain helpers. They are
# imported in a third guarded block so a missing helper fails only the new
# tests, never the Task 1-3 tests, during the RED phase.
try:  # noqa: E402
    from solution.run_distillation import (
        commit_active_retrain,
        committee_seed,
        may_accept_convergence,
        teacher_label_sha256,
        training_steps_for_iteration,
    )
except Exception:  # pragma: no cover - container-only imports
    commit_active_retrain = None  # type: ignore[assignment]
    committee_seed = None  # type: ignore[assignment]
    may_accept_convergence = None  # type: ignore[assignment]
    teacher_label_sha256 = None  # type: ignore[assignment]
    training_steps_for_iteration = None  # type: ignore[assignment]

# Task 5 adds the hash-locked atomic checkpoint and strict resume interfaces.
# They are imported in a fourth guarded block so a missing checkpoint module
# fails only the new tests, never the Task 1-4 tests, during the RED phase.
try:  # noqa: E402
    from solution.run_distillation import (
        CHECKPOINT_SCHEMA_VERSION,
        load_checkpoint,
        write_checkpoint,
    )
except Exception:  # pragma: no cover - container-only imports
    CHECKPOINT_SCHEMA_VERSION = None  # type: ignore[assignment]
    load_checkpoint = None  # type: ignore[assignment]
    write_checkpoint = None  # type: ignore[assignment]


def _require_orchestrator() -> None:
    if resolve_run_identity is None:
        raise AssertionError(
            "resolve_run_identity is missing from solution/run_distillation"
        )


def _require_exploration() -> None:
    if run_exploration is None:
        raise AssertionError(
            "run_exploration is missing from solution/run_distillation"
        )


def _require_task4() -> None:
    if may_accept_convergence is None or commit_active_retrain is None:
        raise AssertionError(
            "Task 4 retrain helpers are missing from solution/run_distillation"
        )


def _require_checkpoint() -> None:
    if write_checkpoint is None or load_checkpoint is None:
        raise AssertionError(
            "Task 5 checkpoint helpers are missing from solution/run_distillation"
        )


def test_selection_is_inclusive_unique_and_deterministic() -> None:
    indices = select_informative(
        np.array([0.049, 0.05, 0.10, 0.15, 0.151, 0.10]),
        ["a", "b", "c", "d", "e", "c"],
        excluded={"d"}, low=0.05, high=0.15, cap=8,
    )
    assert indices == [2, 1]


def test_empty_batch_advances_without_relaxing_band() -> None:
    batches = [{"temperature_K": 200}, {"temperature_K": 600}]
    assert next_batch(batches, 1) == {"temperature_K": 600}
    assert next_batch(batches, 2) is None


def test_dataset_growth_must_equal_unique_selection() -> None:
    with pytest.raises(ValueError, match="exactly equal"):
        assert_exact_growth(["a", "b"], ["c"], ["a", "b", "d"])


# ---- Task 2: explicit seed and run identity ----


def test_run_identity_is_reproducible_and_seed_sensitive() -> None:
    _require_orchestrator()
    profile = {"formal_result": False, "seed": 20260401, "supercell": [2, 2, 1]}
    first = resolve_run_identity("smoke", profile, 7, "struct-a", "teacher-a")
    again = resolve_run_identity("smoke", profile, 7, "struct-a", "teacher-a")
    assert first == again
    other_seed = resolve_run_identity("smoke", profile, 8, "struct-a", "teacher-a")
    assert other_seed["run_identity_sha256"] != first["run_identity_sha256"]
    other_structure = resolve_run_identity("smoke", profile, 7, "struct-b", "teacher-a")
    assert other_structure["run_identity_sha256"] != first["run_identity_sha256"]


def test_run_identity_is_canonical_sha256_of_sorted_compact_json() -> None:
    _require_orchestrator()
    profile = {"formal_result": False, "seed": 20260401, "supercell": [2, 2, 1]}
    identity = resolve_run_identity("smoke", profile, 7, "struct-a", "teacher-a")
    payload = {
        "profile": "smoke",
        "seed": 7,
        "structure_sha256": "struct-a",
        "teacher_model_sha256": "teacher-a",
        "profile_sha256": hashlib.sha256(
            json.dumps(profile, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }
    expected = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert identity["run_identity_sha256"] == expected


def test_seed_resolution_smoke_fallback_paper_requires_explicit() -> None:
    _require_orchestrator()
    profile = {"seed": 20260401}
    assert resolve_run_seed("smoke", None, profile) == 20260401
    assert resolve_run_seed("smoke", 99, profile) == 99
    assert resolve_run_seed("paper", 99, profile) == 99
    with pytest.raises(ValueError, match="explicit --seed"):
        resolve_run_seed("paper", None, profile)


def test_paper_protocol_seed_is_locked_independently_of_run_identity_seed() -> None:
    _require_orchestrator()
    profile = {"seed": 20260401, "protocol_seed": 2026081208}
    assert resolve_protocol_seed("paper", profile, 101) == 2026081208
    assert resolve_protocol_seed("paper", profile, 102) == 2026081208
    # Smoke remains seed-sensitive so cheap negative/alternative fixtures keep
    # exercising independent paths unless they explicitly lock a protocol seed.
    assert resolve_protocol_seed("smoke", {"seed": 1}, 101) == 101


def test_cli_paper_without_seed_and_resume_without_checkpoint_fail(tmp_path) -> None:
    _require_orchestrator()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "run_profiles.json").write_text(
        json.dumps({"smoke": {"seed": 1}, "paper": {"seed": 1}})
    )
    out = tmp_path / "out"
    env = dict(os.environ, MATCLAW_OUTPUT=str(workspace))
    python = sys.executable
    script = str(CASE_ROOT / "solution" / "run_distillation.py")
    paper = subprocess.run(
        [python, script, "--profile", "paper", "--output", str(out)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert paper.returncode != 0
    resume = subprocess.run(
        [python, script, "--profile", "smoke", "--seed", "1",
         "--output", str(out), "--resume"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert resume.returncode != 0


# ---- Task 3: bounded cumulative exploration ----

class _FakeCell:
    """Tiny ase Atoms.cell stand-in (configuration_hash only reads ``.array``)."""

    def __init__(self) -> None:
        self.array = np.eye(3) * 10.0


class _FakeFrame:
    """Minimal duck-typed ase.Atoms stand-in accepted by ``configuration_hash``."""

    def __init__(self, label: str, seed_value: float) -> None:
        self.label = label
        self.positions = np.asarray([seed_value, 0.0, 0.0])
        self.cell = _FakeCell()
        self._symbols = ["Cu", "In", "P", "S"]

    def get_chemical_symbols(self) -> list[str]:
        return self._symbols


def test_paper_profile_declares_bounded_exploration_batches() -> None:
    """The first two active rounds reproduce the source exploration sequence."""
    paper = json.loads(
        (CASE_ROOT / "solution" / "run_profiles.json").read_text()
    )["paper"]
    assert paper["selection_band_eV_A"] == [0.05, 0.15]
    assert paper["min_active_iterations"] >= 1
    rounds = paper["exploration_rounds"]
    assert [[batch["temperature_K"] for batch in round_] for round_ in rounds[:2]] == [
        [200], [600, 1000],
    ]
    batches = [batch for round_ in rounds for batch in round_]
    for batch in batches:
        assert set(batch) == {
            "temperature_K", "md_steps", "trajectory_interval",
            "driver_member", "seed_offset",
        }
        assert batch["md_steps"] == 5000
        assert batch["trajectory_interval"] == 10
    assert len({batch["seed_offset"] for batch in batches}) == len(batches)


def test_paper_profile_reproduces_source_sampling_and_training_schedule() -> None:
    """Lock parameters demonstrated by the recovered source execution."""
    paper = json.loads(
        (CASE_ROOT / "public" / "run_profiles.json").read_text()
    )["paper"]
    assert paper["initial_frames_per_temperature"] == 200
    assert paper["heldout_md_steps"] == 5000
    assert paper["heldout_trajectory_interval"] == 50
    # The recovered source fixed only training seeds (42/123), not an external
    # formal-run id.  A protocol seed makes the undeclared MD random state
    # reproducible without coupling it to evidence-run identity.
    assert paper["protocol_seed"] == 2026081208
    assert paper["training_steps_by_iteration"] == [2000, 2000, 3000]
    assert paper["committee_seeds"] == [42, 123]
    assert paper["student_training"] == {
        "descriptor_neuron": [25, 50, 100],
        "axis_neuron": 16,
        "fitting_net": [128, 128, 128],
        "model_seeds": [1, 1],
    }


def test_source_training_schedule_and_committee_seeds_are_iteration_stable() -> None:
    _require_task4()
    profile = {
        "training_steps_by_iteration": [2000, 2000, 3000],
        "committee_seeds": [42, 123],
    }
    assert [training_steps_for_iteration(profile, i) for i in range(5)] == [
        2000, 2000, 3000, 3000, 3000,
    ]
    assert [[committee_seed(profile, i, m) for m in range(2)] for i in range(3)] == [
        [42, 123], [42, 123], [42, 123],
    ]


def test_selection_cap_is_applied_independently_to_each_exploration_batch() -> None:
    """Source round 2 may retain 200 frames at 600 K plus 26 at 1000 K."""
    deviations = np.asarray([0.10] * 250 + [0.10] * 26)
    hashes = [f"frame-{index:03d}" for index in range(len(deviations))]
    selected = select_informative_by_batch(
        deviations,
        hashes,
        batch_stops=[250, 276],
        excluded=set(),
        low=0.05,
        high=0.15,
        cap=200,
    )
    assert len(selected) == 226
    assert sum(index < 250 for index in selected) == 200
    assert sum(index >= 250 for index in selected) == 26


def test_primary_and_alternative_training_configs_separate_model_and_train_seeds() -> None:
    """Training overrides must not silently replace descriptor/fitting seeds."""
    for filename in ("run_distillation.py", "alt_distillation.py"):
        source = (CASE_ROOT / "solution" / filename).read_text()
        module = ast.parse(source)
        function = next(
            node
            for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == "train_config"
        )
        isolated = ast.Module(
            body=[
                ast.ImportFrom(
                    module="__future__",
                    names=[ast.alias(name="annotations")],
                    level=0,
                ),
                function,
            ],
            type_ignores=[],
        )
        ast.fix_missing_locations(isolated)
        namespace = {"TYPE_MAP": ["Cu", "In", "P", "S"]}
        exec(compile(isolated, filename, "exec"), namespace)
        config = namespace["train_config"](
            Path("train"),
            Path("heldout"),
            2000,
            train_seed=42,
            model_seeds=[1, 1],
            fitting_net=[128, 128, 128],
            descriptor_neuron=[25, 50, 100],
            axis_neuron=16,
        )
        assert config["training"]["seed"] == 42
        assert config["model"]["descriptor"]["seed"] == 1
        assert config["model"]["descriptor"]["neuron"] == [25, 50, 100]
        assert config["model"]["descriptor"]["axis_neuron"] == 16
        assert config["model"]["fitting_net"]["seed"] == 1
        assert config["model"]["fitting_net"]["neuron"] == [128, 128, 128]


def test_exploration_runs_both_batches_and_selects_only_inband_batch2() -> None:
    _require_exploration()
    band = (0.05, 0.15)
    batches = [
        {"temperature_K": 200, "md_steps": 20, "driver_member": 0, "seed_offset": 1},
        {"temperature_K": 600, "md_steps": 20, "driver_member": 1, "seed_offset": 2},
    ]
    deviation_by_label = {"b1-0": 0.03, "b1-1": 0.04, "b2-0": 0.08, "b2-1": 0.09}
    seen_temperatures: list[int] = []

    def md_callback(batch: dict) -> tuple[list, dict]:
        prefix = "b1" if batch["temperature_K"] == 200 else "b2"
        frames = [_FakeFrame(f"{prefix}-{i}", float(batch["temperature_K"]) + i)
                  for i in range(2)]
        seen_temperatures.append(batch["temperature_K"])
        return frames, {"temperature_K": batch["temperature_K"],
                        "candidate_frames": len(frames)}

    def deviation_callback(frames: list) -> np.ndarray:
        return np.asarray([deviation_by_label[frame.label] for frame in frames],
                          dtype=float)

    result = run_exploration(
        batches,
        band=band,
        cap=8,
        excluded=set(),
        md_callback=md_callback,
        deviation_callback=deviation_callback,
    )
    assert seen_temperatures == [200, 600]       # every declared batch ran
    assert band == (0.05, 0.15)                  # band was never modified
    assert result["stop_reason"] is None
    assert len(result["selected"]) == 2
    assert sorted(result["selected"]) == [2, 3]  # only batch-2 (in-band) indices
    last_summary = result["exploration_records"][-1]["deviation_summary"]
    assert set(last_summary) == {"min", "p25", "median", "p75", "max"}


def test_exhaustion_after_all_declared_batches_is_not_false_success() -> None:
    _require_exploration()
    batches = [
        {"temperature_K": 200, "md_steps": 20, "driver_member": 0, "seed_offset": 1},
        {"temperature_K": 600, "md_steps": 20, "driver_member": 1, "seed_offset": 2},
        {"temperature_K": 1000, "md_steps": 20, "driver_member": 0, "seed_offset": 3},
    ]

    def md_callback(batch: dict) -> tuple[list, dict]:
        frames = [_FakeFrame(f"e-{batch['temperature_K']}-{i}",
                             float(batch["temperature_K"]) + i)
                  for i in range(1)]
        return frames, {"temperature_K": batch["temperature_K"],
                        "candidate_frames": len(frames)}

    def deviation_callback(frames: list) -> np.ndarray:
        return np.full(len(frames), 0.02)  # every batch stays below the band

    result = run_exploration(
        batches,
        band=(0.05, 0.15),
        cap=8,
        excluded=set(),
        md_callback=md_callback,
        deviation_callback=deviation_callback,
    )
    assert result["selected"] == []
    assert result["stop_reason"] == EXHAUSTED_STOP_REASON
    # A formal (paper) run that exhausts every declared batch is not a success.
    assert formal_result_for(result["stop_reason"], declared_formal=True) is False
    assert formal_result_for(None, declared_formal=True) is True


# ---- Task 4: genuine selection-label-retrain step ----

def test_iteration_zero_cannot_satisfy_paper_active_workflow() -> None:
    """A low MAE with zero completed active cycles must never stop a paper run.

    Iteration-zero MAE is ordinary supervised training on the teacher-labelled
    initial set; only a retrained committee may claim convergence.
    """
    _require_task4()
    assert not may_accept_convergence(0.079, completed_active_iterations=0, minimum=1)
    assert may_accept_convergence(0.099, completed_active_iterations=1, minimum=1)


def test_convergence_requires_mae_strictly_below_threshold() -> None:
    """The MAE gate stays strict: at-or-above threshold never converges."""
    _require_task4()
    assert not may_accept_convergence(0.10, completed_active_iterations=3, minimum=1)
    assert not may_accept_convergence(0.11, completed_active_iterations=3, minimum=1)
    assert may_accept_convergence(0.0999, completed_active_iterations=3, minimum=1)


def test_retrain_growth_is_exactly_before_plus_unique_selection() -> None:
    """The next iteration's training hashes equal before + selected, exactly.

    A selected hash is appended exactly once: re-appending a training hash or
    a duplicated selection is rejected, and the growth rule is the same
    ``assert_exact_growth`` contract the verifier enforces.
    """
    _require_task4()
    before = ["t-0", "t-1", "t-2"]
    selected = ["s-0", "s-1"]
    assert commit_active_retrain(before, selected) == before + selected
    # a frame already in the training set cannot be appended a second time
    with pytest.raises(ValueError, match="disjoint"):
        commit_active_retrain(before, ["t-1"])
    # a duplicated selection cannot be appended exactly once
    with pytest.raises(ValueError, match="unique"):
        commit_active_retrain(before, ["s-0", "s-0"])
    # the exact-growth rule is the one active_contract enforces
    with pytest.raises(ValueError, match="exactly equal"):
        assert_exact_growth(["a", "b"], ["c"], ["a", "b", "d"])


def test_teacher_label_sha256_is_deterministic_and_label_sensitive() -> None:
    """Teacher labels are pinned by a canonical digest over frames + labels."""
    _require_task4()
    hashes = ["c-0", "c-1"]
    energies = np.asarray([-1.0, -2.0])
    forces = np.asarray([
        [[0.1, 0.2, 0.3], [-0.1, -0.2, -0.3], [0.0, 0.0, 0.0], [0.4, 0.5, 0.6]],
        [[0.2, 0.1, 0.0], [0.0, 0.0, 0.1], [0.3, 0.2, 0.1], [0.5, 0.4, 0.3]],
    ])
    digest = teacher_label_sha256(hashes, energies, forces)
    assert teacher_label_sha256(hashes, energies, forces) == digest
    # a different label assignment (different energy or force) changes the digest
    assert teacher_label_sha256(hashes, np.asarray(energies + 0.1), forces) != digest
    assert teacher_label_sha256(hashes, energies, np.asarray(forces + 0.01)) != digest
    # a different frame set changes the digest
    assert teacher_label_sha256(hashes[:1], energies[:1], forces[:1]) != digest


def test_teacher_label_evidence_accepts_backend_roundoff_but_rejects_forgery() -> None:
    """Hash stored labels exactly, then compare teacher recomputation numerically."""
    assert teacher_label_evidence_valid is not None
    assert contract_teacher_label_sha256 is not None
    hashes = ["c-0"]
    energies = np.asarray([-1.23456721])
    forces = np.asarray([[[0.12345621, -0.23456721, 0.34567821]]])
    digest = contract_teacher_label_sha256(hashes, energies, forces)

    assert teacher_label_evidence_valid(
        digest, hashes, energies, forces, energies + 1.0e-8, forces - 1.0e-8
    )
    assert not teacher_label_evidence_valid(
        digest, hashes, energies, forces, energies + 1.0e-3, forces
    )
    assert not teacher_label_evidence_valid(
        "0" * 64, hashes, energies, forces, energies, forces
    )


# ---- Task 5: hash-locked atomic checkpoint and resume ----


def _identity(seed: int = 7, profile: str = "smoke", teacher: str = "teacher-a") -> dict:
    """A minimal canonical run identity for checkpoint tests."""
    return {
        "profile": profile,
        "seed": int(seed),
        "structure_sha256": "struct-a",
        "teacher_model_sha256": teacher,
        "profile_sha256": "prof-a",
        "run_identity_sha256": f"id-{profile}-{seed}-{teacher}",
    }


def _checkpoint_state(identity: dict | None = None) -> dict:
    """A representative Task 5 checkpoint with the full schema keys."""
    identity = dict(identity if identity is not None else _identity())
    return {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "run_identity": identity,
        "completed_stage": "iteration_1_retrain",
        "artifact_hashes": {},
        "history": [{"iteration": 0, "selected_configuration_hashes": ["sel-a"]}],
        "selected_hashes": ["sel-a"],
    }


def test_write_checkpoint_is_atomic_and_leaves_no_tmp(tmp_path) -> None:
    """write_checkpoint persists via tmp + fsync + Path.replace."""
    _require_checkpoint()
    target = tmp_path / "checkpoint.json"
    state = _checkpoint_state()
    write_checkpoint(target, state)
    assert json.loads(target.read_text(encoding="utf-8")) == state
    # the atomic temp file is gone after the replace
    assert not (tmp_path / "checkpoint.json.tmp").exists()


def test_valid_resume_loads_identical_state(tmp_path) -> None:
    """A written checkpoint loads back with identical state."""
    _require_checkpoint()
    target = tmp_path / "checkpoint.json"
    state = _checkpoint_state()
    write_checkpoint(target, state)
    assert load_checkpoint(target, _identity()) == state


def test_load_checkpoint_rejects_wrong_seed_profile_teacher(tmp_path) -> None:
    """Any run-identity change (seed, profile, teacher) fails closed."""
    _require_checkpoint()
    target = tmp_path / "checkpoint.json"
    write_checkpoint(target, _checkpoint_state(_identity()))
    for wrong in (
        _identity(seed=8),
        _identity(profile="paper"),
        _identity(teacher="teacher-b"),
    ):
        with pytest.raises(RuntimeError, match="identity"):
            load_checkpoint(target, wrong)
    # the exact matching identity still resumes
    assert load_checkpoint(target, _identity()) is not None


def test_load_checkpoint_rejects_missing_artifact(tmp_path) -> None:
    """A declared artifact that is absent on disk fails closed, naming it."""
    _require_checkpoint()
    target = tmp_path / "checkpoint.json"
    state = _checkpoint_state()
    state["artifact_hashes"] = {
        "models/iteration_0/member_0/student.pb": "a" * 64,
    }
    write_checkpoint(target, state)
    with pytest.raises(RuntimeError, match="missing"):
        load_checkpoint(target, _identity())


def test_load_checkpoint_rejects_hash_mismatch(tmp_path) -> None:
    """A modified artifact is never silently reused under resume."""
    _require_checkpoint()
    artifact = tmp_path / "student.pb"
    artifact.write_bytes(b"original model bytes")
    target = tmp_path / "checkpoint.json"
    state = _checkpoint_state()
    state["artifact_hashes"] = {"student.pb": sha256(artifact)}
    write_checkpoint(target, state)
    artifact.write_bytes(b"tampered model bytes")
    with pytest.raises(RuntimeError, match="mismatch"):
        load_checkpoint(target, _identity())


def test_cli_refuses_nonempty_output_without_resume(tmp_path) -> None:
    """A stale, nonempty output root is fail-closed without --resume."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "run_profiles.json").write_text(
        json.dumps({"smoke": {"seed": 1}, "paper": {"seed": 1}})
    )
    out = tmp_path / "out"
    (out / "teacher_md").mkdir(parents=True)
    (out / "teacher_md" / "stale.traj").write_text("stale trajectory")
    env = dict(os.environ, MATCLAW_OUTPUT=str(workspace))
    proc = subprocess.run(
        [sys.executable, str(CASE_ROOT / "solution" / "run_distillation.py"),
         "--profile", "smoke", "--seed", "1", "--output", str(out)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert proc.returncode != 0
    assert "--resume" in proc.stderr


class _CheckpointRunner:
    """An injectable stage executor that mirrors the workflow's commit points.

    Each stage materializes one deterministic artifact, appends one history
    record, and persists a hash-locked checkpoint through the *real*
    ``write_checkpoint``/``load_checkpoint``. ``run`` is the resume path: it
    loads the existing checkpoint and skips every stage already committed in
    ``completed_stage``. Passing ``interrupt_before`` raises ``KeyboardInterrupt``
    right before a named stage, simulating a crash between committed stages.
    """

    def __init__(self, checkpoint_path: Path, identity: dict, stages: list) -> None:
        self.checkpoint_path = Path(checkpoint_path)
        self.identity = dict(identity)
        self.stages = list(stages)
        self.root = self.checkpoint_path.parent

    def run(self, interrupt_before: str | None = None) -> dict:
        state = load_checkpoint(self.checkpoint_path, self.identity)
        if state is None:
            state = {
                "schema_version": CHECKPOINT_SCHEMA_VERSION,
                "run_identity": dict(self.identity),
                "completed_stage": None,
                "artifact_hashes": {},
                "history": [],
                "selected_hashes": [],
            }
        completed = state["completed_stage"]
        past_completed = completed is None
        for name, artifact_rel, history_hash in self.stages:
            if not past_completed:
                if name == completed:
                    past_completed = True
                continue
            if name == interrupt_before:
                raise KeyboardInterrupt()
            artifact = self.root / artifact_rel
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_bytes(f"{name}:{history_hash}".encode("utf-8"))
            state["artifact_hashes"][artifact_rel] = sha256(artifact)
            state["history"].append({"stage": name, "selected": history_hash})
            state["selected_hashes"].append(history_hash)
            state["completed_stage"] = name
            write_checkpoint(self.checkpoint_path, state)
        return state


def test_interruption_resume_matches_uninterrupted_history(tmp_path) -> None:
    """A synthetic KeyboardInterrupt resumes from the last complete stage and
    reproduces the exact final history of an uninterrupted run."""
    _require_checkpoint()
    identity = _identity()
    stages = [
        ("teacher_md", "teacher_md/initial_100K.traj", "sel-teacher"),
        ("heldout_label", "data/heldout/set.000/box.npy", "sel-heldout"),
        ("iteration_0_committee_0", "models/iteration_0/member_0/student.pb", "sel-m0"),
        ("iteration_0_committee_1", "models/iteration_0/member_1/student.pb", "sel-m1"),
        ("iteration_0_exploration_traj_0", "student_md/iteration_1_200K_m0.traj", "sel-traj0"),
        ("iteration_0_selected_labels", "data/iteration_1/set.000/box.npy", "sel-lab0"),
        ("iteration_1_retrain", "models/iteration_1/member_0/student.pb", "sel-r1"),
        ("iteration_1_committee_1", "models/iteration_1/member_1/student.pb", "sel-m3"),
        ("iteration_1_selected_labels", "data/iteration_2/set.000/box.npy", "sel-lab1"),
    ]
    interrupted_at = "iteration_0_exploration_traj_0"
    uninterrupted_dir = tmp_path / "uninterrupted"
    interrupted_dir = tmp_path / "interrupted"
    uninterrupted_dir.mkdir()
    interrupted_dir.mkdir()

    uninterrupted = _CheckpointRunner(
        uninterrupted_dir / "checkpoint.json", identity, stages
    ).run()

    runner = _CheckpointRunner(interrupted_dir / "checkpoint.json", identity, stages)
    with pytest.raises(KeyboardInterrupt):
        runner.run(interrupt_before=interrupted_at)
    # the interrupted run resumes from the last *committed* stage
    interrupted_state = load_checkpoint(interrupted_dir / "checkpoint.json", identity)
    assert interrupted_state["completed_stage"] == "iteration_0_committee_1"
    resumed_state = runner.run()
    assert resumed_state["history"] == uninterrupted["history"]
    assert resumed_state["selected_hashes"] == uninterrupted["selected_hashes"]
