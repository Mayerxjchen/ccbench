import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CASES = (
    "001-matclaw-cips-active-distillation",
    "002-matclaw-cips-curie-temperature",
    "003-matclaw-cips-domain-wall-search",
)


def _case_dir(name: str) -> Path:
    p = ROOT / "cases" / name
    return p if p.is_dir() else (ROOT / name)


def _maintainer_dir(name: str) -> Path:
    p = ROOT / "maintainer" / "cases" / name[:3]
    return p if p.is_dir() else _case_dir(name)


def test_exact_matclaw_case_directories_exist() -> None:
    for name in CASES:
        case = _case_dir(name)
        assert case.is_dir(), name
        if (case / "case.toml").is_file():
            assert {"case.toml", "task.md", "input", "verifier"} <= {path.name for path in case.iterdir()}, name
        else:
            assert {"Dockerfile", "instruction.md", "task.toml", "public"} <= {path.name for path in case.iterdir()}, name


def test_case_images_expose_only_public_inputs() -> None:
    for name in CASES:
        df_path = _case_dir(name) / "Dockerfile"
        if not df_path.is_file():
            df_path = _maintainer_dir(name) / "Dockerfile"
        if not df_path.is_file():
            continue
        dockerfile = df_path.read_text(encoding="utf-8")
        active = [
            line.strip()
            for line in dockerfile.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        assert active[0].startswith("FROM dftworld-base-matclaw-cips:"), name
        copies = [line for line in active if line.upper().startswith("COPY ")]
        assert copies == ["COPY public/ /app/"] or copies == ["COPY input/ /app/"], name
        assert not any(token in dockerfile for token in ("reference/", "solution/", "tests/"))


def test_task_metadata_is_offline_and_has_scientific_resources() -> None:
    for name in CASES:
        manifest_path = _case_dir(name) / "case.toml" if (_case_dir(name) / "case.toml").is_file() else _case_dir(name) / "task.toml"
        task = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        assert task["task"]["name"] == f"benchmark/{name}"
        assert task["environment"]["allow_internet"] is False
        assert task["environment"]["cpus"] >= 2
        assert task["environment"]["memory_mb"] >= 4096
        # Agent timeout lives in infra profile post-migration; HPC tasks
        # carry [verifier].timeout_sec instead of [agent].timeout_sec.
        assert task["verifier"]["timeout_sec"] >= 1500


def test_profiles_and_source_locks_are_explicit() -> None:
    common = json.loads(
        (ROOT / "benchmark" / "sources" / "matclaw" / "source.lock.json").read_text()
    )
    for name in CASES:
        case = _case_dir(name)
        m_dir = _maintainer_dir(name)
        contract_path = case / "input" / "run_profiles.json" if (case / "input" / "run_profiles.json").is_file() else case / "public" / "run_profiles.json"
        contract = json.loads(contract_path.read_text())
        assert set(contract) == {"smoke", "paper"}
        assert contract["smoke"]["formal_result"] is False
        assert contract["paper"]["formal_result"] is True

        lock_path = m_dir / "reference" / "source.lock.json" if (m_dir / "reference" / "source.lock.json").is_file() else case / "reference" / "source.lock.json"
        lock = json.loads(lock_path.read_text())
        assert lock["paper"]["sha256"] == common["paper"]["sha256"]
        assert lock["repository"]["release_commit"] == common["repository"]["release_commit"]
        assert len(lock["structure"]["sha256"]) == 64
        assert len(lock["teacher_model"]["sha256"]) == 64


def test_case_003_public_profile_does_not_reveal_answer_or_future_path() -> None:
    """The adaptive-search answer and precomputed path must stay hidden in solution/.

    The paper protocol is a *sequential* search: each round's proposal may depend only on
    measurements from completed earlier rounds. Publishing the full 14-job path (which
    contains the answer values -0.16 V/angstrom and 50 K) would let an agent replay it,
    so it must not appear anywhere in public/run_profiles.json. Only the locked search
    domain and the fixed start point are public.
    """
    case_003 = _case_dir("003-matclaw-cips-domain-wall-search")
    profiles_path = case_003 / "input" / "run_profiles.json" if (case_003 / "input" / "run_profiles.json").is_file() else case_003 / "public" / "run_profiles.json"
    public = json.loads(profiles_path.read_text())
    serialized = json.dumps(public)
    assert "search_path" not in serialized
    assert "source-path-replay" not in serialized
    assert "-0.16" not in serialized
    assert public["paper"]["bounds"] == {"Ez_V_A": [-0.3, 0.0], "temperature_K": [0, 250]}


def test_validation_cannot_claim_unrun_science() -> None:
    for name in CASES:
        m_dir = _maintainer_dir(name)
        val_path = m_dir / "VALIDATION.json" if (m_dir / "VALIDATION.json").is_file() else _case_dir(name) / "VALIDATION.json"
        bench_path = m_dir / "benchmark_valid.json" if (m_dir / "benchmark_valid.json").is_file() else _case_dir(name) / "benchmark_valid.json"
        validation = json.loads(val_path.read_text())
        summary = json.loads(bench_path.read_text())
        gates = validation["gates"]
        assert set(gates) == {f"G{i}" for i in range(13)}
        assert validation["benchmark_valid"] is all(gate["passed"] for gate in gates.values())
        assert summary["benchmark_valid"] == validation["benchmark_valid"]
        if not validation["paper_profile"]["completed"]:
            assert not gates["G7"]["passed"]
            assert not gates["G8"]["passed"]
            assert validation["benchmark_valid"] is False


def test_source_recovery_package_contains_no_case_image() -> None:
    source_root = ROOT / "benchmark" / "sources" / "matclaw"
    assert not list(source_root.rglob("Dockerfile*"))


def test_reference_runner_stages_public_and_mounts_solution_readonly() -> None:
    runner = (ROOT / "scripts" / "run_matclaw_reference.sh").read_text()
    assert 'cp -a "$case_dir/public/." "$output/workspace/"' in runner
    assert "dst=/solution,readonly" in runner
    assert "output must be absolute" in runner


def test_formal_reference_runner_checks_fresh_workspace_before_staging_public() -> None:
    """The formal freshness gate must not reject the public inputs it just copied."""
    runner = (ROOT / "scripts" / "run_matclaw_reference.sh").read_text()
    freshness_gate = 'find "$output/workspace" -mindepth 1 -maxdepth 1'
    stage_public = 'cp -a "$case_dir/public/." "$output/workspace/"'
    assert runner.index(freshness_gate) < runner.index(stage_public)


def test_reference_manifest_does_not_scan_full_workspace() -> None:
    """The v1 preliminary manifest carries identity only; no full-workspace scan.

    Durable artifacts with roles are authored by finalize_run.py after the
    policy-resolved bundle is stored, so the reference runner must declare an
    empty artifact list and leave curation to the finalizer.
    """
    runner = (ROOT / "scripts" / "run_matclaw_reference.sh").read_text()
    assert "artifacts = []" in runner
    assert "workspace.rglob" not in runner
    assert "finalize_run.py" in runner


def test_001_formal_harness_keeps_hidden_verifier_out_of_solver_stage() -> None:
    harness = (ROOT / "evidence/matclaw/formal/031-formal-run-template.slurm").read_text()
    solve_call = '"$GPU_SIF" /solution/solve.sh'
    verifier_mount = '--bind "$WORKSPACE:/app:ro" --bind "$TESTS:/tests:ro"'
    assert harness.index(solve_call) < harness.index(verifier_mount)
    assert '--bind "$TESTS:/tests:ro"' not in harness[: harness.index(solve_call)]


def test_001_formal_harness_manifests_only_hidden_verifier_success() -> None:
    harness = (ROOT / "evidence/matclaw/formal/031-formal-run-template.slurm").read_text()
    assert 'if test "$VERIFY_EXIT" -ne 0' in harness
    assert 'report.get("valid") is not True' in harness
    assert '"evidence_class": "formal"' in harness
    assert 'run / "private" / "solution"' in harness
    assert 'run / "private" / "tests"' in harness


def test_001_formal_harness_uses_the_site_accepted_qualified_gpu_request() -> None:
    harness = (ROOT / "evidence/matclaw/formal/031-formal-run-template.slurm").read_text()
    assert "#SBATCH --partition=gpu" in harness
    assert "#SBATCH --gres=gpu:1" in harness
    assert "#SBATCH --gres=gpu:tesla:1" not in harness
    assert '"gres": "gpu:1"' in harness


def test_001_verifier_recomputes_every_solver_exploration_frame() -> None:
    """Solver and verifier must agree that ASE's saved initial frame is a candidate."""
    c1 = _case_dir("001-matclaw-cips-active-distillation")
    v_path = c1 / "verifier" / "verifier.py" if (c1 / "verifier" / "verifier.py").is_file() else c1 / "tests" / "verifier.py"
    verifier = v_path.read_text()
    assert "candidates.extend(list(Trajectory(str(path))))" in verifier
    assert "list(Trajectory(str(path)))[1:]" not in verifier


def test_001_alternative_scores_every_exploration_frame() -> None:
    m1 = _maintainer_dir("001-matclaw-cips-active-distillation")
    alt_path = m1 / "solution" / "alt_distillation.py" if (m1 / "solution" / "alt_distillation.py").is_file() else _case_dir("001-matclaw-cips-active-distillation") / "solution" / "alt_distillation.py"
    alternative = alt_path.read_text()
    # Initial teacher sampling intentionally uses dynamic frames only; active
    # exploration, like the primary workflow and source trace, scores frame 0.
    assert "dynamic = frames[1:]" in alternative
    assert "trajectory)[1:]" not in alternative


def test_001_alternative_heldout_excludes_shared_pre_dynamics_frame() -> None:
    """Held-out data must not contain the identical frame zero shared by all MD runs."""
    m1 = _maintainer_dir("001-matclaw-cips-active-distillation")
    alt_path = m1 / "solution" / "alt_distillation.py" if (m1 / "solution" / "alt_distillation.py").is_file() else _case_dir("001-matclaw-cips-active-distillation") / "solution" / "alt_distillation.py"
    alternative = alt_path.read_text()
    heldout_start = alternative.index("test_frames = md_frames(")
    heldout_end = alternative.index("train_energy, train_forces", heldout_start)
    assert ")[1:]" in alternative[heldout_start:heldout_end]


def test_reference_runner_requires_digest_for_formal() -> None:
    runner = (ROOT / "scripts" / "run_matclaw_reference.sh").read_text()
    assert "@sha256:" in runner


def test_reference_runner_requires_clean_tree_for_formal() -> None:
    runner = (ROOT / "scripts" / "run_matclaw_reference.sh").read_text()
    assert "status --porcelain" in runner


def test_solve_entry_points_are_deterministic() -> None:
    for name in CASES:
        m_dir = _maintainer_dir(name)
        solve_path = m_dir / "solution" / "solve.sh" if (m_dir / "solution" / "solve.sh").is_file() else _case_dir(name) / "solution" / "solve.sh"
        solve = solve_path.read_text()
        assert "MATCLAW_SEED" in solve
        assert "MATCLAW_PROFILE" in solve
        assert "MATCLAW_OUTPUT" in solve
        assert "/solution/run_" in solve
        assert "reference/" not in solve


def test_shared_image_is_version_pinned_and_has_a_scientific_smoke_gate() -> None:
    dockerfile_path = ROOT / "runtimes" / "recipes" / "matclaw-cips" / "Dockerfile"
    dockerfile = dockerfile_path.read_text()
    assert "deepmd-kit[lmp]==2.2.11" in dockerfile
    assert "tensorflow==2.16.2" in dockerfile
    assert "frozen_model.pb" in dockerfile
    assert "smoke_test.py" in dockerfile
    assert "RUN /opt/matclaw/bin/python /opt/matclaw/smoke_test.py" in dockerfile

    build_path = ROOT / "runtimes" / "recipes" / "build.sh"
    build = build_path.read_text()
    assert "matclaw-cips" in build
    assert "dftworld-base-matclaw-cips" in build


def test_task_files_declare_gpu_and_pinned_profile() -> None:
    """All three cases declare Candidate/HPC resources and timeout floors."""
    for name in CASES:
        manifest_path = _case_dir(name) / "case.toml" if (_case_dir(name) / "case.toml").is_file() else _case_dir(name) / "task.toml"
        task = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        gpus = task.get("environment", {}).get("gpus", 0)
        assert gpus in (0, 1), name
        assert task["verifier"]["timeout_sec"] >= 7200, name


def test_eval_task_spec_exposes_gpus_and_explicit_docker_args() -> None:
    """eval.load_task() surfaces [environment].gpus and the docker run is explicit."""
    eval_src = (ROOT / "eval.py").read_text(encoding="utf-8")
    assert "gpus: int = 0" in eval_src  # TaskSpec field
    assert "meta.get(\"environment\", {}).get(\"gpus\", 0)" in eval_src  # load_task
    assert "gpus must be >= 0" in eval_src  # negative values rejected
    assert "requested_gpus" in eval_src  # recorded in TaskResult -> summary.json
    # GPU 显式分配逻辑由 agents.py 中的 docker_gpu_args 统一实现
    agents_src = (ROOT / "ccbench" / "agents.py").read_text(encoding="utf-8")
    assert "def docker_gpu_args" in agents_src
    assert '"--gpus", "device=0"' in agents_src  # explicit device, never default runtime


def test_gpu_runner_has_immutable_identity_and_no_cpu_retag() -> None:
    """The GPU orchestrator builds only :2.2.11-gpu and never touches a CPU tag."""
    runner = (ROOT / "scripts" / "run_gpu_paper.sh").read_text(encoding="utf-8")
    assert "dftworld-base-matclaw-cips:2.2.11-gpu" in runner
    assert "{{index .RepoDigests 0}}" in runner  # resolve digest
    assert "@sha256:" in runner  # fail unless immutable
    assert "--build" in runner
    assert "qualify_gpu.py" in runner
    assert "ceil(1.5 * measured_seconds)" in runner or "ceil(1.5" in runner
    assert "run_matclaw_reference.sh" in runner
    # The GPU path must never retag or rebuild a CPU identity.
    for forbidden in ("docker tag", "PINNED_TAG", "--force-retag", ":2.2.11-cpu"):
        assert forbidden not in runner, forbidden


def test_reference_runner_honors_gpus_for_any_evidence_class() -> None:
    """A named GPU device must be passed even for diagnostic runs."""
    runner = (ROOT / "scripts" / "run_matclaw_reference.sh").read_text(encoding="utf-8")
    assert 'if [[ -n "$gpu_device" ]]; then' in runner
    assert '--gpus "device=$gpu_device"' in runner


def test_gpu_image_is_gpu_only_and_keeps_build_gate_device_independent() -> None:
    dockerfile_path = ROOT / "runtimes" / "recipes" / "matclaw-cips-gpu" / "Dockerfile"
    dockerfile = dockerfile_path.read_text()
    # The base is the CPU-verified image; it is parameterized (ARG BASE_IMAGE)
    # so a cross-arch build can pin the amd64 CPU image without touching the
    # default arm64 tags. The default MUST stay the CPU-verified identity.
    assert "ARG BASE_IMAGE=dftworld-base-matclaw-cips:2.2.11-cpu" in dockerfile
    assert "FROM ${BASE_IMAGE} AS matclaw-gpu-runtime" in dockerfile
    assert "tensorflow[and-cuda]==2.16.2" in dockerfile
    assert "qualify_gpu.py" in dockerfile
    # The build-time gate must not require a GPU (docker build has none).
    assert 'CUDA_VISIBLE_DEVICES="" /opt/matclaw/bin/python /opt/matclaw/smoke_test.py' in dockerfile

    probe_path = ROOT / "runtimes" / "recipes" / "matclaw-cips-gpu" / "qualify_gpu.py"
    probe = probe_path.read_text()
    assert "gpu_visible" in probe
    assert "energy_abs_diff_eV" in probe
    assert "max_force_component_abs_diff_eV_A" in probe
    assert "md_finite" in probe
    assert "refusing to run silently on CPU" in probe  # fails closed without GPU
    # ASE >= 3.25 Atoms.copy() does NOT carry the calculator: each MD frame
    # snapshot must re-attach it or get_potential_energy() raises "Atoms object
    # has no calculator" (A100 qualify 3536355 FAILED on exactly this).
    assert "frame.calc = atoms.calc" in probe
