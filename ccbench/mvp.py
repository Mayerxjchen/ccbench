"""Thin maintainer workflow for direct-Claude-Code MVP benchmark runs.

The Candidate is intentionally not launched here.  This module creates a
public, repo-external workspace, verifies that its immutable inputs have not
drifted, seals only ``final/``, and invokes the existing isolated verifier.
Cloud instance and Slurm lifecycle remain operator responsibilities.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from ccbench.contracts.case import CaseSpec
from ccbench.core.digests import sha256_file
from ccbench.core.packager import MANIFEST_NAME, package_candidate
from ccbench.core.quarantine import (
    QuarantineLimits,
    SubmissionSeal,
    collect_raw_submission,
    quarantine_submission,
)
from ccbench.core.verifier import VerifierSpec, run_verifier
from ccbench.paths import ROOT
from ccbench.config.profiles import load_infra_profiles, resolve_profile

MVP_MANIFEST = "mvp-run.json"
POLICY_NAME = "CLAUDE.md"
MUTABLE_DIRS = frozenset({"work", "final", "compute-requests", "compute-results"})
RUN_STATES = frozenset({"CASE_DEV", "MVP_EVALUATED", "FORMAL_AUTONOMOUS"})
COMPUTE_CLASSES = frozenset({"cpu", "gpu"})


class MvpError(ValueError):
    """An MVP runner precondition failed."""


def _outside_repo(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if resolved == ROOT or ROOT in resolved.parents:
        raise MvpError(f"run artifacts must live outside the repository: {resolved}")
    return resolved


def resolve_case(value: str | Path) -> Path:
    candidate = Path(value).expanduser()
    if candidate.is_dir():
        return candidate.resolve()
    cases_root = ROOT / "cases"
    exact = cases_root / str(value)
    if exact.is_dir():
        return exact.resolve()
    matches = sorted(p for p in cases_root.glob(f"{value}*") if p.is_dir())
    if len(matches) != 1:
        raise MvpError(
            f"case {value!s} resolved to {len(matches)} directories; use an exact path"
        )
    return matches[0].resolve()


def _manifest_payload(bundle: Path, spec: CaseSpec) -> dict[str, Any]:
    public = json.loads((bundle / MANIFEST_NAME).read_text(encoding="utf-8"))
    immutable = {
        entry["path"]: {"size": entry["size"], "sha256": entry["sha256"]}
        for entry in public["files"]
    }
    immutable[POLICY_NAME] = {
        "size": (bundle / POLICY_NAME).stat().st_size,
        "sha256": sha256_file(bundle / POLICY_NAME),
    }
    immutable[MANIFEST_NAME] = {
        "size": (bundle / MANIFEST_NAME).stat().st_size,
        "sha256": sha256_file(bundle / MANIFEST_NAME),
    }
    skills_root = bundle / ".claude" / "skills"
    if skills_root.is_dir():
        for path in sorted(skills_root.rglob("*")):
            if path.is_dir():
                continue
            if path.is_symlink() or not path.is_file():
                raise MvpError(f"Candidate skill is not a regular file: {path}")
            rel = path.relative_to(bundle).as_posix()
            immutable[rel] = {"size": path.stat().st_size, "sha256": sha256_file(path)}
    return {
        "schema_version": "1.0",
        "state": "CASE_DEV",
        "case_id": spec.case_id,
        "case_version": spec.case_version,
        "submission_root": spec.submission_root,
        "public_digest": public["public_digest"],
        "immutable_files": immutable,
        "candidate_skills": {
            path.relative_to(skills_root).parts[0]: sha256_file(path)
            for path in sorted(skills_root.glob("*/SKILL.md"))
        } if skills_root.is_dir() else {},
        "mutable_directories": sorted(MUTABLE_DIRS),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def default_lock_path(bundle: Path) -> Path:
    bundle = Path(bundle).expanduser().resolve()
    return bundle.parent / f"{bundle.name}.lock.json"


def export_case(
    case: str | Path, destination: Path, *, lock_path: Path | None = None
) -> dict[str, Any]:
    """Create a fresh repo-external Candidate workspace from public files only."""
    destination = _outside_repo(destination)
    lock_path = _outside_repo(lock_path or default_lock_path(destination))
    if lock_path == destination or destination in lock_path.parents:
        raise MvpError("operator lock must not be stored inside the Candidate workspace")
    if destination.exists():
        raise MvpError(f"refusing to overwrite existing run workspace: {destination}")
    if lock_path.exists():
        raise MvpError(f"refusing to overwrite operator lock: {lock_path}")
    case_dir = resolve_case(case)
    spec = CaseSpec.load(case_dir)
    try:
        package_candidate(spec, destination)
        policy = ROOT / POLICY_NAME
        if not policy.is_file() or policy.is_symlink():
            raise MvpError(f"trusted Candidate policy missing: {policy}")
        shutil.copyfile(policy, destination / POLICY_NAME)
        # Only the case-declared subset of the trusted, static skill registry
        # is exported.  There is no directory discovery or alias expansion.
        declared = []
        if isinstance(spec.case_dir, Path):
            import tomllib
            raw = tomllib.loads((spec.case_dir / "case.toml").read_text(encoding="utf-8"))
            case_agent = raw.get("agent") or {}
            declared = case_agent.get("skills") or []
        registry = load_infra_profiles()
        profile = resolve_profile(registry, "agents", spec.agent_profile or "claude-mvp")
        allowed = profile.get("allowed_skills", [])
        if not isinstance(declared, list) or any(not isinstance(x, str) for x in declared):
            raise MvpError("[agent].skills must be a static string allowlist")
        if any(x not in allowed for x in declared):
            raise MvpError("case requests a Candidate skill outside its agent profile")
        for name in declared:
            if name != "bench-compute-request":
                raise MvpError(f"unknown Candidate skill: {name}")
            source = ROOT / "runtimes" / "recipes" / "skills" / name / "SKILL.md"
            if not source.is_file() or source.is_symlink():
                raise MvpError(f"declared Candidate skill missing: {name}")
            target = destination / ".claude" / "skills" / name / "SKILL.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        for name in MUTABLE_DIRS:
            (destination / name).mkdir(mode=0o755)
        payload = _manifest_payload(destination, spec)
        (destination / MVP_MANIFEST).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return payload
    except Exception:
        if destination.exists():
            shutil.rmtree(destination)
        raise


def _safe_relative(path: str) -> None:
    posix = PurePosixPath(path)
    if not path or posix.is_absolute() or ".." in posix.parts:
        raise MvpError(f"unsafe manifest path: {path!r}")


def _reject_path_escape_or_symlink(root: Path, path: Path, *, label: str) -> None:
    root = root.resolve()
    try:
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise MvpError(f"{label} path escapes its workspace: {path}") from exc
    cursor = path
    while cursor != root:
        if cursor.is_symlink():
            raise MvpError(f"{label} path contains a symlink: {path}")
        cursor = cursor.parent


def check_bundle(bundle: Path, *, lock_path: Path | None = None) -> dict[str, Any]:
    """Verify immutable input bytes and reject unexpected Candidate-root nodes."""
    bundle = _outside_repo(bundle)
    manifest_path = _outside_repo(lock_path or default_lock_path(bundle))
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise MvpError(f"cannot read {MVP_MANIFEST}: {exc}") from exc
    if payload.get("state") not in RUN_STATES:
        raise MvpError("invalid MVP run state")
    immutable = payload.get("immutable_files")
    if not isinstance(immutable, dict) or not immutable:
        raise MvpError("immutable_files is missing or empty")
    for rel, expected in immutable.items():
        _safe_relative(rel)
        if not isinstance(expected, dict):
            raise MvpError(f"invalid immutable file record: {rel}")
        path = bundle / rel
        _reject_path_escape_or_symlink(bundle, path, label="immutable")
        if not path.is_file():
            raise MvpError(f"immutable file missing or unsafe: {rel}")
        if path.stat().st_size != expected.get("size") or sha256_file(path) != expected.get("sha256"):
            raise MvpError(f"immutable file drift detected: {rel}")

    allowed = {PurePosixPath(p).parts[0] for p in immutable}
    allowed |= {MANIFEST_NAME, MVP_MANIFEST} | MUTABLE_DIRS
    unexpected = sorted(p.name for p in bundle.iterdir() if p.name not in allowed)
    if unexpected:
        raise MvpError(f"unexpected Candidate-root entries: {unexpected}")
    for name in MUTABLE_DIRS:
        path = bundle / name
        if path.is_symlink() or not path.is_dir():
            raise MvpError(f"mutable workspace directory missing or unsafe: {name}")
    return payload


def freeze_submission(
    bundle: Path, destination: Path, *, lock_path: Path | None = None
) -> SubmissionSeal:
    """Verify the public bundle and atomically seal only its declared final tree."""
    bundle = _outside_repo(bundle)
    destination = _outside_repo(destination)
    payload = check_bundle(bundle, lock_path=lock_path)
    if destination.exists():
        raise MvpError(f"refusing to overwrite sealed submission: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = destination.parent / f".{destination.name}.raw-{os.getpid()}"
    try:
        collect_raw_submission(
            bundle,
            str(payload["submission_root"]),
            raw,
            legacy_layout=False,
            max_size_mb=20 * 1024,
        )
        return quarantine_submission(
            raw,
            destination,
            QuarantineLimits(
                max_files=50_000,
                max_single_bytes=2 * 1024**3,
                max_total_bytes=20 * 1024**3,
                allow_archives=False,
            ),
        )
    finally:
        if raw.exists():
            shutil.rmtree(raw)


def _relative_file(bundle: Path, value: Any, *, label: str) -> Path:
    if not isinstance(value, str):
        raise MvpError(f"{label} path must be a string")
    _safe_relative(value)
    path = bundle / value
    _reject_path_escape_or_symlink(bundle, path, label=label)
    return path


def validate_compute_request(
    bundle: Path, request: Path, *, lock_path: Path | None = None
) -> dict[str, Any]:
    """Validate a Candidate compute handoff before an operator submits it.

    This validates shape, resource consistency, input bytes, and output paths.
    It deliberately does not execute the command or select credentials.
    """
    bundle = _outside_repo(bundle)
    check_bundle(bundle, lock_path=lock_path)
    request = Path(request).expanduser().resolve()
    try:
        request.relative_to((bundle / "compute-requests").resolve())
    except ValueError as exc:
        raise MvpError("compute request must be under compute-requests/") from exc
    try:
        payload = json.loads(request.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise MvpError(f"cannot parse compute request: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "1.0":
        raise MvpError("compute request requires schema_version='1.0'")
    unknown_request = sorted(set(payload) - {
        "schema_version", "compute_class", "command", "resources", "inputs", "outputs", "validation"
    })
    if unknown_request:
        raise MvpError(f"compute request contains unknown keys: {unknown_request}")
    compute_class = payload.get("compute_class")
    if compute_class not in COMPUTE_CLASSES:
        raise MvpError(f"compute_class must be one of {sorted(COMPUTE_CLASSES)}")
    command = payload.get("command")
    if (
        not isinstance(command, list)
        or not command
        or any(not isinstance(item, str) or not item for item in command)
    ):
        raise MvpError("command must be a non-empty string array")
    forbidden_tokens = {"sh", "bash", "zsh", "fish", "powershell", "cmd", "ssh", "sshd", "sbatch", "scancel", "srun", "scp", "rsync", "curl", "wget", "docker", "podman", "singularity", "apptainer", "bench-hpc", "compshare", "submit", "cancel"}
    shell_chars = set(";|&$`()><\n\r\x00")
    for token in command:
        lower = token.lower()
        if lower in forbidden_tokens or any(part in lower for part in ("bench-hpc", "compshare")) or any(marker in lower for marker in ("submit", "cancel")):
            raise MvpError("command requests an Operator or cloud operation")
        if any(char in token for char in shell_chars):
            raise MvpError("command contains shell/path injection characters")
        token_path = PurePosixPath(token)
        if token_path.is_absolute() or ".." in token_path.parts:
            raise MvpError("command paths must be relative and non-traversing")
    resources = payload.get("resources")
    if not isinstance(resources, dict):
        raise MvpError("resources must be an object")
    # Per-node resource contract.  ``memory_gb_per_node`` is the memory each
    # compute node receives (the Candidate sizes the whole request from this,
    # not a flat total); ``minimum_gpu_memory_gb`` applies only to GPU classes.
    _RESOURCE_KEYS = frozenset(
        {
            "nodes",
            "ntasks",
            "cpus_per_task",
            "memory_gb_per_node",
            "gpus",
            "walltime_min",
            "minimum_gpu_memory_gb",
        }
    )
    unknown = sorted(set(resources) - _RESOURCE_KEYS)
    if unknown:
        raise MvpError(f"resources contains unknown keys: {unknown}")
    for key in ("nodes", "ntasks", "cpus_per_task", "memory_gb_per_node", "walltime_min", "gpus"):
        value = resources.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < (0 if key == "gpus" else 1):
            raise MvpError(f"resources.{key} must be an integer in range")
    if resources["ntasks"] < resources["nodes"]:
        raise MvpError("resources.ntasks must be >= resources.nodes")
    minimum_gpu_memory = resources.get("minimum_gpu_memory_gb")
    if compute_class == "cpu":
        if resources["gpus"] != 0:
            raise MvpError("CPU requests must set resources.gpus=0")
        if minimum_gpu_memory is not None:
            raise MvpError("CPU requests must not set resources.minimum_gpu_memory_gb")
    else:  # gpu
        if resources["gpus"] < 1:
            raise MvpError("GPU requests must set resources.gpus>=1")
        if not isinstance(minimum_gpu_memory, int) or isinstance(minimum_gpu_memory, bool) or minimum_gpu_memory < 1:
            raise MvpError("GPU requests must set resources.minimum_gpu_memory_gb >= 1")

    validation = payload.get("validation")
    if validation is not None:
        if not isinstance(validation, dict):
            raise MvpError("validation must be an object")
        for marker_key in ("success_markers", "reject_if"):
            markers = validation.get(marker_key)
            if markers is not None and (
                not isinstance(markers, list)
                or any(not isinstance(m, str) or not m for m in markers)
            ):
                raise MvpError(f"validation.{marker_key} must be a non-empty string array")

    inputs = payload.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise MvpError("inputs must be a non-empty array")
    normalized_inputs: list[dict[str, Any]] = []
    for entry in inputs:
        if not isinstance(entry, dict):
            raise MvpError("each input must be an object")
        unknown_input = sorted(set(entry) - {"path", "sha256", "size"})
        if unknown_input:
            raise MvpError(f"compute input contains unknown keys: {unknown_input}")
        path = _relative_file(bundle, entry.get("path"), label="input")
        if PurePosixPath(entry["path"]).parts[0] in MUTABLE_DIRS:
            raise MvpError("compute inputs must come from immutable Candidate files")
        if path.is_symlink() or not path.is_file():
            raise MvpError(f"compute input missing or unsafe: {entry.get('path')}")
        actual = sha256_file(path)
        if entry.get("sha256") != actual:
            raise MvpError(f"compute input digest mismatch: {entry.get('path')}")
        if "size" in entry and entry["size"] != path.stat().st_size:
            raise MvpError(f"compute input size mismatch: {entry.get('path')}")
        normalized_inputs.append(
            {"path": entry["path"], "size": path.stat().st_size, "sha256": actual}
        )

    outputs = payload.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        raise MvpError("outputs must be a non-empty path array")
    for value in outputs:
        if not isinstance(value, str) or len(PurePosixPath(value).parts) < 2:
            raise MvpError("compute output must name a file under compute-results/")
        path = _relative_file(bundle, value, label="output")
        try:
            path.resolve().relative_to((bundle / "compute-results").resolve())
        except ValueError as exc:
            raise MvpError("compute outputs must be under compute-results/") from exc

    return {
        "schema_version": "1.0",
        "compute_class": compute_class,
        "command": command,
        "resources": resources,
        "inputs": normalized_inputs,
        "outputs": outputs,
        "request_sha256": sha256_file(request),
        "validated_at": datetime.now(timezone.utc).isoformat(),
    }


def verify_sealed_submission(sealed: Path) -> dict[str, Any]:
    sealed = Path(sealed).expanduser().resolve()
    manifest_path = sealed / "manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise MvpError(f"invalid sealed submission manifest: {exc}") from exc
    entries = payload.get("files")
    if not isinstance(entries, list):
        raise MvpError("sealed submission manifest has no files array")
    declared = {"manifest.json"}
    for entry in entries:
        if not isinstance(entry, dict):
            raise MvpError("sealed submission manifest contains a non-object")
        path = _relative_file(sealed, entry.get("path"), label="sealed")
        if path.is_symlink() or not path.is_file():
            raise MvpError(f"sealed file missing or unsafe: {entry.get('path')}")
        if path.stat().st_size != entry.get("size") or sha256_file(path) != entry.get("sha256"):
            raise MvpError(f"sealed submission drift detected: {entry.get('path')}")
        declared.add(entry["path"])
    actual = {
        path.relative_to(sealed).as_posix()
        for path in sealed.rglob("*")
        if path.is_file()
    }
    if actual != declared:
        raise MvpError(f"sealed submission contains undeclared files: {sorted(actual - declared)}")
    return payload


def _maintainer_case(case_dir: Path) -> Path:
    prefix = case_dir.name.split("-", 1)[0]
    path = ROOT / "maintainer" / "cases" / prefix
    if not path.is_dir():
        raise MvpError(f"maintainer material not found for case {case_dir.name}")
    return path


def evaluate_submission(
    case: str | Path,
    sealed_submission: Path,
    logs: Path,
    *,
    image: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Evaluate a sealed submission with the existing networkless verifier."""
    case_dir = resolve_case(case)
    verify_sealed_submission(Path(sealed_submission))
    spec = CaseSpec.load(case_dir)
    maintainer = _maintainer_case(case_dir)
    verifier_dir = case_dir / "verifier"
    if not verifier_dir.is_dir():
        raise MvpError(f"verifier directory missing: {verifier_dir}")
    if spec.verifier_profile:
        profiles = load_infra_profiles()
        verifier = resolve_profile(profiles, "verifiers", spec.verifier_profile)
        locked_image = verifier.get("image")
        if not isinstance(locked_image, str) or not locked_image:
            raise MvpError(f"verifier profile {spec.verifier_profile!r} has no image")
        if image is not None and image != locked_image:
            raise MvpError("verifier image override conflicts with locked verifier profile")
        resolved_image = locked_image
    else:
        resolved_image = image or spec.candidate_image
    if not resolved_image:
        raise MvpError("no verifier image declared; pass --image")
    reference = maintainer / "reference"
    if not reference.is_dir():
        reference = maintainer / "baseline"
    solution = maintainer / "solution"
    result = run_verifier(
        VerifierSpec(
            image=resolved_image,
            timeout_sec=spec.verifier_timeout_sec,
            env=spec.verifier_env,
            tests_dir=verifier_dir,
            reference_dir=reference if reference.is_dir() else None,
            solution_dir=solution if solution.is_dir() else None,
        ),
        Path(sealed_submission).expanduser().resolve(),
        _outside_repo(logs),
        run_id=run_id or f"mvp-{spec.case_id}",
    )
    payload = result.to_dict()
    payload["mvp_state"] = "MVP_EVALUATED"
    return payload


def seal_to_dict(seal: SubmissionSeal) -> dict[str, Any]:
    payload = asdict(seal)
    payload["sealed_at"] = seal.sealed_at.isoformat()
    return payload
