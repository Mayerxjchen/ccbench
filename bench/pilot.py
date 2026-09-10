"""Candidate-Docker Claude Code pilot orchestration.

The pilot owns only the Candidate/sidecar Docker boundary and durable run
metadata. It never imports or invokes an Operator, scheduler, cloud client,
or scientific compute runtime.
"""

from __future__ import annotations

import json
import hashlib
import asyncio
import math
import os
import stat
import shutil
import time
import uuid
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from bench.config.profiles import load_infra_profiles, resolve_profile
from bench.config.candidate import CandidateConfigError, candidate_config_digest, load_candidate_config
from bench.mvp import (
    MvpError,
    _require_qualified_verifier,
    check_bundle,
    evaluate_submission,
    export_case,
    freeze_submission,
    _has_suite_manifest,
    materialize_compute_request,
    validate_compute_request,
)
from bench.core.digests import sha256_file
from bench.paths import ROOT

LOCAL_DEV = "LOCAL_DEV"
EXTERNAL_SUBMISSION = "EXTERNAL_SUBMISSION"
FORMAL = "FORMAL"
_LOADED_DOTENV_VALUES: dict[str, str] = {}


def _suite_digest_for_case(case_dir: Path) -> str | None:
    """Return the composite digest for an external suite containing a case."""
    try:
        from bench.suite import inspect_suite
        case_dir = Path(case_dir).expanduser().resolve()
        for parent in (case_dir, *case_dir.parents):
            if not (parent / "suite.toml").is_file():
                continue
            report = inspect_suite(parent)
            if any(row.get("directory") == case_dir.name for row in report.get("cases", [])):
                return report.get("manifest_digest")
    except Exception:
        # Formal callers turn a missing/invalid external binding into a hard
        # error below; local runs remain usable for case development.
        return None
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_verifier_key_pair(signing_key_hex: str, trusted_public_hex: str) -> None:
    """Validate the verifier signing key before committing the evaluation."""
    try:
        private = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(signing_key_hex))
        public = ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(trusted_public_hex))
        derived = private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        ).hex()
        if derived.lower() != trusted_public_hex.lower():
            raise ValueError("signing key does not match trusted public key")
        # Exercise the keypair so malformed providers cannot pass validation
        # merely by supplying correctly-sized byte strings.
        signature = private.sign(b"bench-verifier-key-preflight-v1")
        public.verify(signature, b"bench-verifier-key-preflight-v1")
    except Exception as exc:
        raise MvpError(f"invalid verifier signing/trust key pair: {exc}") from exc


def _load_candidate_dotenv() -> None:
    """Load supported values from the paper directory or engine ``.env``.

    This mirrors the existing eval entry point but keeps pilot self-contained;
    shell values always win and secrets are never copied into the container.
    """
    for path in (Path.cwd() / ".env", ROOT / ".env"):
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("\"'")
            if key.startswith("BENCH_") or key.startswith("ANTHROPIC_"):
                if key not in os.environ:
                    os.environ[key] = value
                    _LOADED_DOTENV_VALUES[key] = value


def _explicit_env(name: str) -> str | None:
    """Return a shell-provided value, excluding the repository .env default."""
    value = os.environ.get(name)
    if _LOADED_DOTENV_VALUES.get(name) == value:
        return None
    return value


def _effective_limits(profile: dict[str, Any], config: dict[str, Any]) -> tuple[dict[str, int | float], str]:
    """Resolve one immutable budget snapshot without mutating the profile."""
    configured = config.get("limits") or {}
    values: dict[str, int | float] = {
        "max_turns": int(configured.get("max_turns", profile.get("max_turns", 64))),
        "max_total_tokens": int(configured.get("max_total_tokens", profile.get("max_total_tokens", 50_000_000))),
        "agent_timeout_sec": float(configured.get("agent_timeout_sec", profile.get("agent_timeout_sec", 7200))),
        "max_budget_usd": float(configured.get("max_budget_usd", profile.get("max_budget_usd", 0.0))),
    }
    # Profile values are trusted, while config values have already passed the
    # strict CandidateConfig validator.  Keep this guard for programmatic
    # callers that construct a config mapping directly.
    if values["max_turns"] < 1 or values["max_total_tokens"] < 1 or values["agent_timeout_sec"] <= 0 or values["max_budget_usd"] < 0:
        raise MvpError("effective candidate limits must be positive (except max_budget_usd may be zero)")
    return values, ("config" if configured else "agent_profile")


def _write_effective_limits(run_dir: Path, limits: dict[str, int | float], source: str) -> tuple[str, str]:
    """Persist a secret-free effective-limit snapshot before Candidate start."""
    payload = {"limits": limits, "source": source}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    path = run_dir / "effective-limits.json"
    temporary = run_dir / f".effective-limits.{uuid.uuid4().hex}.tmp"
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps({**payload, "digest": digest}, indent=2, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return str(path), digest


def _effective_limits_digest(limits: dict[str, int | float], source: str) -> str:
    payload = {"limits": limits, "source": source}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _verify_effective_limits_snapshot(path: Path, limits: dict[str, int | float], source: str, digest: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise MvpError("effective candidate limits snapshot is missing or unsafe")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MvpError(f"effective candidate limits snapshot is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("limits") != limits or payload.get("source") != source:
        raise MvpError("effective candidate limits snapshot content changed before resume")
    if payload.get("digest") != digest:
        raise MvpError("effective candidate limits snapshot digest changed before resume")


def _observed_budget(logs: dict[str, Any] | None) -> dict[str, int | float]:
    """Extract bounded candidate usage counters from safe adapter telemetry."""
    gateway = (logs or {}).get("model_gateway") or {}
    usage = {
        "model_turns": int(gateway.get("turn_count") or 0),
        "total_tokens": int(gateway.get("tokens_used") or 0),
    }
    return usage


def _previous_budget(state: dict[str, Any]) -> dict[str, int | float]:
    usage = state.get("budget_usage")
    required = {"model_turns", "total_tokens", "candidate_phase_walltime_sec"}
    if not isinstance(usage, dict) or not required <= set(usage):
        raise MvpError("existing run has no complete cumulative budget usage; restart it instead of granting new budget")
    turns = usage["model_turns"]
    tokens = usage["total_tokens"]
    active = usage["candidate_phase_walltime_sec"]
    if (isinstance(turns, bool) or not isinstance(turns, int) or turns < 0
            or isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0
            or isinstance(active, bool) or not isinstance(active, (int, float))
            or not math.isfinite(float(active)) or active < 0):
        raise MvpError("existing run cumulative budget usage is malformed")
    return {"model_turns": turns, "total_tokens": tokens,
            "candidate_phase_walltime_sec": float(active)}


def _recompute_completed_budget(transcript: Path) -> dict[str, int | float]:
    """Recompute cumulative usage from completed host-side Candidate phases.

    The mutable run-state is an index.  A phase is spendable on resume only
    when its host transcript contains a matching ``candidate_complete`` event;
    an interrupted ``start`` event is deliberately not granted a zero budget.
    """
    if transcript.is_symlink() or not transcript.is_file():
        raise MvpError("candidate transcript is missing or unsafe; cannot resume budget")
    total = {"model_turns": 0, "total_tokens": 0,
             "candidate_phase_walltime_sec": 0.0}
    pending = False
    completed = 0
    try:
        rows = transcript.read_text(encoding="utf-8").splitlines()
        for line in rows:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                continue
            event = row.get("event")
            if event == "start":
                if pending:
                    raise MvpError("candidate transcript has an incomplete prior phase")
                pending = True
            elif event == "candidate_complete":
                phase = row.get("phase_usage")
                if not pending or not isinstance(phase, dict):
                    raise MvpError("candidate transcript has incomplete budget phase")
                turns = phase.get("model_turns")
                tokens = phase.get("total_tokens")
                elapsed = phase.get("candidate_phase_walltime_sec")
                if (isinstance(turns, bool) or not isinstance(turns, int) or turns < 0
                        or isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 0
                        or isinstance(elapsed, bool) or not isinstance(elapsed, (int, float))
                        or not math.isfinite(float(elapsed)) or elapsed < 0):
                    raise MvpError("candidate transcript contains malformed phase budget")
                total["model_turns"] += turns
                total["total_tokens"] += tokens
                total["candidate_phase_walltime_sec"] += float(elapsed)
                completed += 1
                pending = False
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise MvpError(f"candidate transcript cannot be trusted for resume budget: {exc}") from exc
    if pending or completed == 0:
        raise MvpError("candidate transcript has no complete resumable Candidate phase")
    return total


def _resolve_host_credential(key_name: str | None) -> tuple[str | None, tuple[str, ...]]:
    """Resolve accepted Anthropic credential aliases without exporting them."""
    names: list[str] = [key_name] if isinstance(key_name, str) and key_name else []
    if key_name == "ANTHROPIC_API_KEY":
        names.append("ANTHROPIC_AUTH_TOKEN")
    elif key_name == "ANTHROPIC_AUTH_TOKEN":
        names.append("ANTHROPIC_API_KEY")
    value = next((os.environ[name] for name in names if os.environ.get(name)), None)
    return value, tuple(names)


def _hydrate_formal_image_qualification(profile: dict[str, Any]) -> dict[str, Any]:
    """Load host-local signed Candidate/sidecar locks for Formal admission."""
    from bench.runtime.candidate_qualification import (
        CandidateQualificationError,
        load_qualified_runtime,
    )
    public_key = os.environ.get("BENCH_IMAGE_QUALIFICATION_PUBLIC_KEY", "")
    key_id = os.environ.get("BENCH_IMAGE_QUALIFICATION_KEY_ID", "image-qualification-v1")
    if not public_key:
        raise MvpError("Formal requires BENCH_IMAGE_QUALIFICATION_PUBLIC_KEY")
    hydrated = dict(profile)
    try:
        candidate_lock = load_qualified_runtime(
            profile.get("candidate_runtime_lock", ""), role="candidate",
            image_name=str(profile.get("image", "")),
            trusted_public_key_hex=public_key, trusted_key_id=key_id,
        )
        sidecar_lock = load_qualified_runtime(
            profile.get("sidecar_runtime_lock", ""), role="sidecar",
            image_name=str(profile.get("sidecar_image", "")),
            trusted_public_key_hex=public_key, trusted_key_id=key_id,
        )
    except CandidateQualificationError as exc:
        raise MvpError(str(exc)) from exc
    hydrated["image_digest"] = candidate_lock["image_digest"]
    hydrated["sidecar_image_digest"] = sidecar_lock["image_digest"]
    hydrated["candidate_qualification_receipt_digest"] = candidate_lock["receipt_digest"]
    hydrated["sidecar_qualification_receipt_digest"] = sidecar_lock["receipt_digest"]
    return hydrated


def _state_path(run_dir: Path) -> Path:
    return run_dir / "run-state.json"


def _write_state(run_dir: Path, state: dict[str, Any]) -> None:
    _state_path(run_dir).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_state(run_dir: Path) -> dict[str, Any]:
    try:
        state = json.loads(_state_path(run_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MvpError(f"run state is unavailable: {exc}") from exc
    if not isinstance(state, dict) or not state.get("run_id"):
        raise MvpError("run state is malformed")
    return state


_MAX_PUBLISHED_MESSAGES_BYTES = 16 * 1024 * 1024


def _publish_candidate_messages(run_dir: Path, thread_dir: str | Path | None = None) -> str | None:
    """Publish the Candidate transcript outside its writable workspace.

    Claude's JSONL stream is kept in the run-scoped thread directory.  The
    Candidate never receives the published destination: it is created only
    after the process has stopped (and is read-only thereafter).  All checks
    use the opened descriptor so a diagnostic symlink/hardlink cannot be
    smuggled into the run record.
    """
    root = Path(run_dir).resolve()
    source = Path(thread_dir).expanduser() / "messages.jsonl" if thread_dir else root / "threads" / root.name / "messages.jsonl"
    destination = root / "messages.jsonl"
    try:
        source_stat = source.lstat()
    except (FileNotFoundError, OSError):
        return None
    if (not stat.S_ISREG(source_stat.st_mode) or source.is_symlink()
            or source_stat.st_nlink != 1
            or source_stat.st_size > _MAX_PUBLISHED_MESSAGES_BYTES):
        return None
    temporary = root / f".messages.jsonl.{uuid.uuid4().hex}.tmp"
    source_fd = None
    try:
        source_fd = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(source_fd)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened.st_size > _MAX_PUBLISHED_MESSAGES_BYTES):
            return None
        dest_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        copied = 0
        try:
            with os.fdopen(dest_fd, "wb") as output:
                while True:
                    chunk = os.read(source_fd, min(1024 * 1024, _MAX_PUBLISHED_MESSAGES_BYTES - copied + 1))
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > _MAX_PUBLISHED_MESSAGES_BYTES:
                        return None
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        finally:
            dest_fd = None
        final_stat = os.fstat(source_fd)
        if final_stat.st_size != copied or final_stat.st_nlink != 1:
            return None
        os.replace(temporary, destination)
        os.chmod(destination, 0o444)
        return str(destination)
    except (OSError, ValueError):
        return None
    finally:
        if source_fd is not None:
            try:
                os.close(source_fd)
            except OSError:
                pass
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _operation_lock(run_dir: Path) -> int:
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        return os.open(run_dir / ".operation.lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise MvpError("another pilot operation is already in progress") from exc


def _release_operation_lock(run_dir: Path, fd: int) -> None:
    os.close(fd)
    try:
        (run_dir / ".operation.lock").unlink()
    except FileNotFoundError:
        pass


def _acquire_evaluation_seal(workspace: Path, root: Path, lock_path: Path):
    """Atomically mark a run for evaluation and freeze its submission.

    This is deliberately called only after all verifier/image/signing-key
    preflight has succeeded.  A failed freeze is a pre-commit failure: remove
    the newly-created evaluation marker and partial seal so the operator can
    retry the same run safely.
    """
    evaluate_lock = root / ".evaluate.lock"
    sealed = root / "sealed-submission"
    had_sealed = sealed.exists() or sealed.is_symlink()
    try:
        fd = os.open(evaluate_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"run_id": root.name, "at": _now()}, stream)
    except FileExistsError as exc:
        raise MvpError("run evaluation is already sealed or in progress") from exc
    try:
        seal = freeze_submission(workspace, sealed, lock_path=lock_path)
    except Exception:
        # Do not leave a failed pre-commit looking like a completed evaluation.
        try:
            evaluate_lock.unlink()
        except FileNotFoundError:
            pass
        if not had_sealed:
            if sealed.is_symlink() or sealed.is_file():
                sealed.unlink()
            elif sealed.is_dir():
                shutil.rmtree(sealed)
        raise
    return seal, sealed


def _readonly_tree(path: Path) -> None:
    if not path.exists():
        return
    for child in path.rglob("*"):
        child.chmod(0o555 if child.is_dir() else 0o444)
    path.chmod(0o555)


def _freeze_existing(path: Path, *, allow_new_files: bool) -> None:
    if not path.exists():
        return
    for child in path.rglob("*"):
        child.chmod(0o555 if child.is_dir() else 0o444)
    path.chmod(0o755 if allow_new_files else 0o555)


def _validate_requests(workspace: Path, lock: Path, operator_root: Path) -> tuple[list[str], dict[str, str]]:
    request_files = sorted((workspace / "compute-requests").glob("*.json"))
    digests: dict[str, str] = {}
    for request in request_files:
        payload = json.loads(request.read_text(encoding="utf-8"))
        if all(isinstance(item, dict) and set(item) == {"path"} for item in payload.get("inputs", [])):
            payload = materialize_compute_request(workspace, request)
            request.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        normalized = validate_compute_request(workspace, request, lock_path=lock)
        rel = request.relative_to(workspace).as_posix()
        digests[rel] = normalized["request_sha256"]
        operator_root.mkdir(parents=True, exist_ok=True)
        destination = operator_root / f"{normalized['request_sha256'].replace('sha256:', '')}.json"
        if not destination.exists():
            destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return [str(workspace / rel) for rel in digests], digests


def _operator_output_paths(workspace: Path, request_files: list[str]) -> list[str]:
    paths: list[str] = []
    for request_name in request_files:
        payload = json.loads(Path(request_name).read_text(encoding="utf-8"))
        for output in payload.get("outputs", []):
            if not isinstance(output, str) or not output or Path(output).is_absolute() or ".." in Path(output).parts:
                raise MvpError(f"operator output path is unsafe: {output!r}")
            path = (workspace / output).resolve()
            if workspace not in path.parents or path.is_symlink() or not path.is_file():
                raise MvpError(f"operator output is missing or unsafe: {output}")
            paths.append(Path(output).as_posix())
    return paths


def _check_operator_outputs(
    workspace: Path, request_files: list[str],
    *, expected_manifest: list[dict[str, Any]] | None = None,
) -> None:
    paths = _operator_output_paths(workspace, request_files)
    if expected_manifest is None:
        return
    expected = {str(item.get("path")): item for item in expected_manifest if isinstance(item, dict)}
    if set(expected) != set(paths):
        raise MvpError("operator output manifest does not match compute requests")
    for rel in paths:
        item = expected[rel]
        path = workspace / rel
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise MvpError(f"operator output cannot be inspected: {rel}") from exc
        digest = "sha256:" + sha256_file(path)
        if item.get("size") != size or item.get("sha256") != digest:
            raise MvpError(f"operator output changed after signed import: {rel}")


def _check_request_bindings(workspace: Path, previous: dict[str, Any]) -> None:
    bindings = previous.get("compute_request_digests")
    if not isinstance(bindings, dict):
        raise MvpError("run state has no request digest bindings")
    for rel, expected in bindings.items():
        path = workspace / rel
        if path.is_symlink() or not path.is_file() or sha256_file(path) != expected:
            raise MvpError(f"bound compute request changed: {rel}")


def import_results(run_dir: Path, source: Path, *, receipt: Path | None = None) -> dict[str, Any]:
    """Import operator files, requiring a signed handoff outside LOCAL_DEV."""
    root = Path(run_dir).expanduser().resolve()
    state = _read_state(root)
    if state.get("state") != "COMPUTE_REQUIRED":
        raise MvpError("results can only be imported for COMPUTE_REQUIRED runs")
    fd = _operation_lock(root)
    try:
        workspace = Path(state["workspace"]).resolve()
        source = Path(source).expanduser().resolve()
        _check_request_bindings(workspace, state)
        if source == root or root in source.parents:
            raise MvpError("operator result source must be outside Candidate workspace")
        requests = list(state.get("compute_requests", []))
        output_paths = _operator_output_paths_from_requests(requests)
        # ``formal`` is the durable coordinator admission marker.  Do not let
        # a mutable run-state edit downgrade an admitted run to LOCAL_DEV and
        # thereby skip the operator receipt gate.
        trust_mode = FORMAL if state.get("formal") is True else state.get("trust_mode", LOCAL_DEV)
        receipt_digest = None
        receipt_payload = None
        receipt_copy = None
        if trust_mode in {EXTERNAL_SUBMISSION, FORMAL}:
            if receipt is None:
                raise MvpError("external/formal compute import requires --receipt")
            from bench.core.operator_receipt import OperatorReceiptError, verify_operator_receipt
            expected = {
                "run_id": state.get("run_id"),
                "attempt": int(state.get("attempt", 1)),
                "compute_backend": state.get("compute_backend", "local"),
                "request_digests": state.get("compute_request_digests", {}),
            }
            if state.get("compute_profile") is not None:
                expected["compute_profile"] = state.get("compute_profile")
            try:
                receipt_digest, receipt_payload = verify_operator_receipt(
                    Path(receipt),
                    trusted_public_key_hex=os.environ.get("BENCH_OPERATOR_RECEIPT_TRUSTED_PUBLIC_KEY", ""),
                    trusted_key_id=os.environ.get("BENCH_OPERATOR_RECEIPT_KEY_ID", "operator-v1"),
                    expected=expected,
                    expected_outputs=output_paths,
                )
            except OperatorReceiptError as exc:
                raise MvpError(str(exc)) from exc
            if state.get("operator_receipt_digest") not in (None, receipt_digest):
                raise MvpError("operator receipt was replayed from another handoff")
            control_dir = root / ".control"
            control_dir.mkdir(mode=0o700, exist_ok=True)
            receipt_copy = control_dir / "operator-receipt.json"
            if receipt_copy.exists() or receipt_copy.is_symlink():
                raise MvpError("operator receipt already imported for this run")
        elif receipt is not None:
            raise MvpError("LOCAL_DEV does not consume operator receipts")
        receipt_outputs = {
            item["path"]: item for item in (receipt_payload or {}).get("outputs", [])
        }
        staging_root = root / ".control" / f".operator-import-{os.getpid()}"
        if staging_root.exists() or staging_root.is_symlink():
            raise MvpError("operator import staging path already exists")
        staging_root.mkdir(mode=0o700, parents=True)
        new_destinations: list[Path] = []
        results_root = workspace / "compute-results"
        results_root.mkdir(parents=True, exist_ok=True)
        results_root.chmod(0o755)
        for request_name in requests:
            payload = json.loads(Path(request_name).read_text(encoding="utf-8"))
            for output in payload.get("outputs", []):
                if not isinstance(output, str) or output not in output_paths:
                    raise MvpError(f"operator output path is unsafe: {output!r}")
                src = source / output
                dst = workspace / output
                try:
                    source_stat = os.lstat(src)
                except OSError as exc:
                    raise MvpError(f"operator output is missing or unsafe: {output}") from exc
                src_resolved = src.resolve()
                if source not in src_resolved.parents or source_stat.st_nlink != 1 or src.is_symlink() or not src.is_file():
                    raise MvpError(f"operator output is missing or unsafe: {output}")
                expected_output = receipt_outputs.get(Path(output).as_posix())
                if trust_mode in {EXTERNAL_SUBMISSION, FORMAL} and expected_output is None:
                    raise MvpError(f"operator receipt omitted output: {output}")
                actual_digest = sha256_file(src)
                if expected_output is not None:
                    expected_digest = expected_output["sha256"]
                    if expected_digest != actual_digest and expected_digest != "sha256:" + actual_digest:
                        raise MvpError(f"operator output digest mismatch: {output}")
                    if expected_output["size"] != source_stat.st_size:
                        raise MvpError(f"operator output size mismatch: {output}")
                dst.parent.mkdir(parents=True, exist_ok=True)
                if workspace not in dst.parent.resolve().parents and dst.parent.resolve() != workspace:
                    raise MvpError(f"operator output destination escapes workspace: {output}")
                if dst.exists() or dst.is_symlink():
                    raise MvpError(f"refusing to overwrite Candidate result: {output}")
                staged = staging_root / output
                staged.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, staged)
                if sha256_file(staged) != actual_digest or staged.stat().st_size != source_stat.st_size:
                    raise MvpError(f"operator output changed during copy: {output}")
        # Commit only after every source has passed lstat/hash/size checks.
        for output in output_paths:
            dst = workspace / output
            staged = staging_root / output
            if dst.exists() or dst.is_symlink():
                raise MvpError(f"refusing to overwrite Candidate result: {output}")
            os.replace(staged, dst)
            new_destinations.append(dst)
        if receipt_payload is not None and receipt_copy is not None:
            fd_receipt = os.open(receipt_copy, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd_receipt, "w", encoding="utf-8") as stream:
                json.dump(receipt_payload, stream, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        _readonly_tree(results_root)
        state["operator_results_source"] = str(source)
        state["operator_receipt_digest"] = receipt_digest
        state["operator_receipt_path"] = str(receipt_copy) if receipt_copy is not None else None
        state["operator_receipt_outputs"] = [
            receipt_outputs[path] for path in sorted(receipt_outputs)
        ] if receipt_outputs else None
        state["updated_at"] = _now()
        _write_state(root, state)
        shutil.rmtree(staging_root)
        return state
    except Exception:
        # A failed handoff must be retryable and must not leave a partial
        # compute-results tree or a receipt that claims a complete import.
        for destination in locals().get("new_destinations", []):
            try:
                destination.unlink()
            except FileNotFoundError:
                pass
        staging = locals().get("staging_root")
        if isinstance(staging, Path) and staging.exists():
            shutil.rmtree(staging)
        copied_receipt = locals().get("receipt_copy")
        if isinstance(copied_receipt, Path) and copied_receipt.exists():
            copied_receipt.unlink()
        raise
    finally:
        _release_operation_lock(root, fd)


def _operator_output_paths_from_requests(request_files: list[str]) -> list[str]:
    """Return the exact relative output set declared by locked requests."""
    paths: list[str] = []
    for request_name in request_files:
        payload = json.loads(Path(request_name).read_text(encoding="utf-8"))
        for output in payload.get("outputs", []):
            if not isinstance(output, str) or not output or Path(output).is_absolute() or ".." in Path(output).parts:
                raise MvpError(f"operator output path is unsafe: {output!r}")
            normalized = Path(output).as_posix()
            if normalized in paths:
                raise MvpError(f"duplicate operator output path: {normalized}")
            paths.append(normalized)
    return paths


def _verify_saved_operator_receipt(
    root: Path, state: dict[str, Any], request_files: list[str],
) -> list[dict[str, Any]]:
    """Re-verify the controlled operator receipt on resume/evaluate."""
    receipt_path = state.get("operator_receipt_path")
    claimed_digest = state.get("operator_receipt_digest")
    if not isinstance(receipt_path, str) or not isinstance(claimed_digest, str):
        raise MvpError("external/formal run has no controlled operator receipt")
    from bench.core.operator_receipt import OperatorReceiptError, verify_operator_receipt
    expected = {
        "run_id": state.get("run_id"),
        "attempt": int(state.get("attempt", 1)),
        "compute_backend": state.get("compute_backend", "local"),
        "request_digests": state.get("compute_request_digests", {}),
    }
    if state.get("compute_profile") is not None:
        expected["compute_profile"] = state.get("compute_profile")
    try:
        digest, payload = verify_operator_receipt(
            Path(receipt_path),
            trusted_public_key_hex=os.environ.get("BENCH_OPERATOR_RECEIPT_TRUSTED_PUBLIC_KEY", ""),
            trusted_key_id=os.environ.get("BENCH_OPERATOR_RECEIPT_KEY_ID", "operator-v1"),
            expected=expected,
            expected_outputs=_operator_output_paths_from_requests(request_files),
        )
    except OperatorReceiptError as exc:
        raise MvpError(f"controlled operator receipt rejected: {exc}") from exc
    if digest != claimed_digest:
        raise MvpError("controlled operator receipt digest changed")
    outputs = payload.get("outputs")
    if not isinstance(outputs, list):
        raise MvpError("controlled operator receipt has no output manifest")
    return outputs


def _skill_prompt(profile: dict[str, Any], run_dir: Path) -> tuple[str, str]:
    names = profile.get("allowed_skills")
    if not isinstance(names, list) or not names or any(not isinstance(x, str) for x in names):
        raise MvpError("agent profile allowed_skills must be a non-empty list")
    chunks: list[str] = []
    for name in names:
        if name != "bench-compute-request":
            raise MvpError(f"agent profile names an unavailable skill: {name!r}")
        path = ROOT / "runtimes" / "recipes" / "skills" / name / "SKILL.md"
        if not path.is_file():
            raise MvpError(f"allowlisted skill is missing: {name}")
        chunks.append(path.read_text(encoding="utf-8"))
    content = "\n\n".join(chunks)
    digest = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
    prompt_file = run_dir / "candidate-system-prompt.txt"
    prompt_file.write_text(
        "You are the Candidate. Work only inside the supplied workspace.\n"
        "Only use the explicitly allowed file tools; never execute commands, access outside paths, or discover plugins.\n\n"
        "Trusted skill content (digest=" + digest + "):\n\n" + content,
        encoding="utf-8",
    )
    return prompt_file.read_text(encoding="utf-8"), digest


def _run_container_candidate(
    *,
    profile: dict[str, Any],
    case_dir: Path,
    workspace: Path,
    run_dir: Path,
    prompt: str,
    session_id: str,
    resume_session: bool,
    candidate_config: dict[str, Any] | None = None,
    model_override: str | None = None,
    image_override: str | None = None,
    sidecar_image_override: str | None = None,
    formal: bool = False,
    resource_run_uid: str | None = None,
    effective_limits: dict[str, int | float] | None = None,
) -> dict[str, Any]:
    """Drive the only supported active Candidate path: Claude inside Docker.

    The adapter owns the Docker network, sidecar proxy and container cleanup;
    this function only bridges its async lifecycle into the synchronous MVP
    CLI and returns durable telemetry.  Model IDs are passed byte-for-byte to
    Claude and are intentionally not validated against a provider allowlist.
    """
    from bench.agents import ClaudeCodeAdapter
    runner = profile.get("runner")
    if runner != "container_claude_code":
        raise MvpError(
            f"agent profile runner must be container_claude_code; got {runner!r}"
        )
    candidate_config = candidate_config or {}
    limits = effective_limits or {}
    cfg_model = candidate_config.get("model", {})
    cfg_candidate = candidate_config.get("candidate", {})
    image = (image_override or _explicit_env("BENCH_CANDIDATE_IMAGE")
             or cfg_candidate.get("image") or profile.get("image"))
    sidecar_image = sidecar_image_override or _explicit_env("BENCH_SIDECAR_IMAGE") or cfg_candidate.get("sidecar_image") or profile.get("sidecar_image")
    if formal and (image != profile.get("image") or sidecar_image != profile.get("sidecar_image")):
        raise MvpError("formal Candidate runs must use the qualified profile images")
    if formal and any(
        not isinstance(profile.get(key), str) or not profile.get(key).startswith("sha256:")
        for key in ("image_digest", "sidecar_image_digest")
    ):
        raise MvpError("formal Candidate images require qualified immutable digests")
    # BENCH_* is the public run configuration. Profile values are only the
    # safe default; an explicitly configured custom model must pass through
    # unchanged (including bracketed/route suffixes).
    model = model_override or _explicit_env("BENCH_MODEL") or cfg_model.get("requested_id") or profile.get("model")
    if not isinstance(image, str) or not image:
        raise MvpError("container agent profile must declare a non-empty image")
    if not isinstance(model, str) or not model.strip():
        raise MvpError("container agent profile model must be a non-empty string")
    tools = profile.get("tools", ["Bash", "Read", "Write", "Edit", "Glob", "Grep"])
    if not isinstance(tools, list) or not tools or any(not isinstance(x, str) or not x for x in tools):
        raise MvpError("container agent profile tools must be a non-empty string list")

    # Formal runs consume only the route declared by the qualified profile.
    # Repository dotenv defaults must not silently replace that route.  Local
    # runs retain the convenient BENCH_* aliases as explicit overrides.
    explicit_endpoint = _explicit_env("BENCH_BASE_URL")
    explicit_key = _explicit_env("BENCH_API_KEY")
    endpoint_name = cfg_model.get("endpoint_env") or profile.get("api_endpoint_env", "ANTHROPIC_BASE_URL")
    key_name = cfg_model.get("credential_env") or profile.get("api_key_env", "ANTHROPIC_API_KEY")
    if not formal and explicit_endpoint:
        endpoint_name = "BENCH_BASE_URL"
    if not formal and explicit_key:
        key_name = "BENCH_API_KEY"
    endpoint = os.environ.get(endpoint_name) if isinstance(endpoint_name, str) else None
    # Claude Code deployments use both names in practice.  Treat them as
    # host-side aliases only: the real credential remains in ModelGatewayProxy
    # and the Candidate receives a run-scoped ephemeral ANTHROPIC_API_KEY.
    api_key, credential_names = _resolve_host_credential(key_name)
    if not endpoint or not api_key:
        credential_hint = " or ".join(credential_names) or str(key_name)
        raise MvpError(
            f"model gateway credentials are missing; set {endpoint_name} and {credential_hint} "
            "for an Anthropic-compatible endpoint"
        )
    api_auth_mode = "bearer" if (
        key_name == "ANTHROPIC_AUTH_TOKEN"
        or (
            key_name == "ANTHROPIC_API_KEY"
            and not os.environ.get("ANTHROPIC_API_KEY")
            and bool(os.environ.get("ANTHROPIC_AUTH_TOKEN"))
        )
    ) else "x-api-key"

    adapter = ClaudeCodeAdapter(
        model=model,
        max_turns=int(limits.get("max_turns", profile.get("max_turns", 64))),
        max_total_tokens=int(limits.get("max_total_tokens", profile.get("max_total_tokens", 50_000_000))),
        threads_root=run_dir / "threads",
        task_name=run_dir.name,
        image=image,
        case_dir=case_dir,
        gpus=0,
        agent_timeout_sec=float(limits.get("agent_timeout_sec", profile.get("agent_timeout_sec", 7200))),
        skill_roots=(),
        container_env={
            "BENCH_RUN_ID": run_dir.name,
            "BENCH_SESSION_ID": session_id,
        },
        forbidden_env_names=frozenset({*credential_names, endpoint_name}),
        execution_class="local_sandbox",
        api_endpoint=endpoint,
        api_key=api_key,
        api_auth_mode=api_auth_mode,
        engine_version="claude-code-container-v1",
        expected_image_digest=profile.get("image_digest"),
        workspace=workspace,
        session_id=session_id,
        resume_session=resume_session,
        cli_model=cfg_model.get("cli_model"),
        tools=tuple(tools),
        effort=str(profile.get("effort", "medium")),
        max_budget_usd=float(limits.get("max_budget_usd", profile.get("max_budget_usd", 0.0))),
        preserve_workspace=True,
        sidecar_image=sidecar_image,
        sidecar_expected_image_digest=profile.get("sidecar_image_digest"),
        preflight_timeout_sec=float(profile.get("preflight_timeout_sec", 20.0)),
        upstream_response_timeout_sec=float(profile.get("upstream_response_timeout_sec", 300.0)),
        resource_run_uid=resource_run_uid,
    )

    async def drive() -> dict[str, Any]:
        await adapter.prepare()
        try:
            await adapter.start(prompt)
            logs = adapter.collect_logs()
            messages_path = _publish_candidate_messages(run_dir, logs.get("thread_dir"))
            if messages_path:
                logs["messages_path"] = messages_path
            return logs
        finally:
            try:
                await adapter.close()
            except Exception:
                # Preserve the original Candidate error if there is one; a
                # cleanup failure is surfaced in the durable telemetry below.
                if adapter.container_id or adapter.internal_net:
                    raise

    try:
        return asyncio.run(drive())
    except BaseException as exc:
        # Preserve whatever the adapter emitted before surfacing the failure.
        # This is deliberately a bounded, out-of-workspace publication; the
        # run-state stores only its path and safe adapter telemetry.
        failed_logs: dict[str, Any] | None = None
        try:
            failed_logs = adapter.collect_logs()
            messages_path = _publish_candidate_messages(run_dir, failed_logs.get("thread_dir"))
            if messages_path:
                failed_logs["messages_path"] = messages_path
        except Exception:
            pass
        # asyncio.run normally executes drive()'s finally block.  Retry once
        # from a fresh loop after a signal/cancellation so cleanup remains
        # deterministic even if the original loop was interrupted mid-close.
        had_resources = bool(adapter.container_id or adapter.sidecar_cid or adapter.internal_net)
        if had_resources:
            async def emergency_close() -> None:
                await asyncio.wait_for(adapter.close(), timeout=20.0)
            try:
                asyncio.run(emergency_close())
            except Exception:
                pass
            # If the in-memory close was interrupted (including by a signal),
            # reconcile only this run's labelled resources.  Never fall back
            # to Docker prune or a name-based sweep.
            if adapter.resource_run_uid and (
                adapter.container_id or adapter.sidecar_cid or adapter.internal_net
            ):
                try:
                    from bench.core.sidecar_topology import reconcile_run_resources
                    asyncio.run(reconcile_run_resources(adapter.resource_run_uid))
                except Exception:
                    pass
        if failed_logs is not None:
            setattr(exc, "safe_logs", failed_logs)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        wrapped = MvpError(f"container Claude Code run failed: {exc}")
        setattr(wrapped, "original_error_type", type(exc).__name__)
        if failed_logs is not None:
            setattr(wrapped, "safe_logs", failed_logs)
        raise wrapped from exc


def start(case: str, *, run_dir: Path, formal: bool = False, allow_existing: bool = False, config_path: Path | None = None, model_override: str | None = None, image_override: str | None = None, sidecar_image_override: str | None = None, agent_profile_name: str | None = None, formal_admission_path: Path | None = None) -> dict[str, Any]:
    _load_candidate_dotenv()
    run_dir = Path(run_dir).expanduser().resolve()
    if run_dir.exists() and any(run_dir.iterdir()) and not allow_existing:
        raise MvpError(f"refusing to overwrite existing pilot directory: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    operation_fd = _operation_lock(run_dir)
    try:
        return _start_locked(case, run_dir=run_dir, formal=formal, allow_existing=allow_existing, config_path=config_path, model_override=model_override, image_override=image_override, sidecar_image_override=sidecar_image_override, agent_profile_name=agent_profile_name, formal_admission_path=formal_admission_path)
    finally:
        _release_operation_lock(run_dir, operation_fd)


def _start_locked(case: str, *, run_dir: Path, formal: bool, allow_existing: bool, config_path: Path | None = None, model_override: str | None = None, image_override: str | None = None, sidecar_image_override: str | None = None, agent_profile_name: str | None = None, formal_admission_path: Path | None = None) -> dict[str, Any]:
    workspace = run_dir / "candidate"
    lock = run_dir / "operator-lock.json"
    previous = _read_state(run_dir) if allow_existing and _state_path(run_dir).is_file() else None
    admission_copy = run_dir / ".control" / "formal-admission.json"
    if formal and previous is not None:
        # Resume always re-verifies the coordinator-signed copy; the mutable
        # run-state digest is only an index, never the trust anchor.
        if not admission_copy.is_file() or admission_copy.is_symlink():
            raise MvpError("formal resume requires its immutable admission copy")
        formal_admission_path = admission_copy
    config: dict[str, Any] = {}
    if config_path is not None:
        try:
            config = load_candidate_config(config_path)
        except CandidateConfigError as exc:
            raise MvpError(str(exc)) from exc
    elif previous is not None and isinstance(previous.get("candidate_config"), dict):
        # Resume from the normalized, secret-free snapshot rather than from a
        # mutable external file.  Credentials remain environment references.
        config = json.loads(json.dumps(previous["candidate_config"]))
        if previous.get("candidate_config_path"):
            config["_path"] = previous["candidate_config_path"]
        if previous.get("candidate_config_digest"):
            recomputed = candidate_config_digest(config)
            if recomputed != previous["candidate_config_digest"]:
                raise MvpError("candidate config snapshot digest changed before resume")
            config["digest"] = recomputed
    if previous is None:
        spec_payload = export_case(case, workspace, lock_path=lock)
    else:
        if Path(previous.get("workspace", "")) != workspace or not workspace.is_dir():
            raise MvpError("existing run workspace does not match durable state")
        spec_payload = check_bundle(workspace, lock_path=lock)
    from bench.contracts.case import CaseSpec
    from bench.mvp import resolve_case
    resolved_case = resolve_case(case)
    spec = CaseSpec.load(
        resolved_case, strict_external=_has_suite_manifest(resolved_case),
    )
    suite_digest = _suite_digest_for_case(spec.path)
    if formal:
        # A draft/explicitly not-ready paper case cannot be admitted merely
        # because its manifest parses.  Scientific readiness is a maintainer
        # decision, never a user-side formal switch.
        manifest_path = spec.path / "case.toml"
        try:
            manifest_raw = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise MvpError(f"formal admission cannot read case readiness: {exc}") from exc
        if "draft" in spec.case_version.lower() or manifest_raw.get("readiness") not in (None, "READY"):
            raise MvpError("formal admission requires a READY case; draft/not-ready cases are rejected")
        # An external paper pack is only formally admissible when its suite
        # manifest participates in the signed binding.  In-repository legacy
        # cases intentionally have no suite digest requirement.
        try:
            spec.path.resolve().relative_to((ROOT / "cases").resolve())
            external_case = False
        except ValueError:
            external_case = True
        if external_case and suite_digest is None:
            raise MvpError("formal external suite requires a valid suite manifest binding")
    selected_profile_name = agent_profile_name or spec.agent_profile or "claude-mvp"
    profiles = load_infra_profiles()
    profile = resolve_profile(profiles, "agents", selected_profile_name)
    if formal:
        profile = _hydrate_formal_image_qualification(profile)
    effective_limits, limits_source = _effective_limits(profile, config)
    limits_digest = _effective_limits_digest(effective_limits, limits_source)
    limits_path = run_dir / "effective-limits.json"
    if previous is None:
        limits_path_str, limits_digest = _write_effective_limits(run_dir, effective_limits, limits_source)
    else:
        limits_path_str = str(limits_path)
        if previous.get("effective_limits_digest") != limits_digest:
            raise MvpError("effective candidate limits changed before resume")
        _verify_effective_limits_snapshot(limits_path, effective_limits, limits_source, limits_digest)
    profile_mode = config.get("mode") or profile.get("mode") or "container"
    requested_trust = config.get("trust_mode")
    if formal:
        trust_mode = FORMAL
    elif requested_trust in {"external-verifier", "external_submission"}:
        trust_mode = EXTERNAL_SUBMISSION
    else:
        # local/untrusted (and an omitted value) are intentionally equivalent:
        # same-host runs can only be diagnostics and never formal scores.
        trust_mode = LOCAL_DEV
    if profile_mode != "container":
        raise MvpError("only Candidate Docker mode is supported")
    if formal and requested_trust in {"local", "untrusted"}:
        raise MvpError("formal admission cannot use a local/untrusted trust mode")
    if formal:
        # A registered agent profile is an allowed Formal treatment.  Its
        # complete content digest and qualified image identities are bound into
        # the coordinator admission below.  Scattered model/image overrides
        # remain forbidden because they bypass that binding.
        if any((model_override, image_override, sidecar_image_override)):
            raise MvpError("formal admission does not permit CLI model/image overrides")
        if "trust_mode" in config:
            raise MvpError("formal admission trust is coordinator-owned, not a user config field")
        if any(_explicit_env(name) for name in ("BENCH_MODEL", "BENCH_CANDIDATE_IMAGE", "BENCH_SIDECAR_IMAGE", "BENCH_BASE_URL", "BENCH_API_KEY")):
            raise MvpError("formal admission does not permit model/image environment overrides")
        if config.get("model"):
            if any(key not in {"requested_id"} for key in config["model"]):
                raise MvpError("formal admission does not permit model route/credential overrides")
            if config["model"].get("requested_id") not in (None, profile.get("model")):
                raise MvpError("formal admission model is not the qualified profile model")
        if config.get("candidate", {}).get("image") not in (None, profile.get("image")):
            raise MvpError("formal admission Candidate image is not qualified")
        if config.get("candidate", {}).get("sidecar_image") not in (None, profile.get("sidecar_image")):
            raise MvpError("formal admission sidecar image is not qualified")
        if any(
            not isinstance(profile.get(key), str) or not profile.get(key).startswith("sha256:")
            for key in ("image_digest", "sidecar_image_digest")
        ):
            raise MvpError("formal admission requires qualified Candidate and sidecar image digests")
        if formal_admission_path is None:
            raise MvpError("formal run requires a signed coordinator admission")
        from bench.core.verifier_worker import VerifierWorkerError, verify_formal_admission
        try:
            expected_admission = {
                "run_id": run_dir.name, "attempt": 1,
                "case_id": spec.case_id, "case_version": spec.case_version,
                "public_digest": spec_payload["public_digest"],
                "agent_profile": selected_profile_name,
                "agent_profile_digest": profiles.digest("agents", selected_profile_name),
                "model_id": profile.get("model"),
                "candidate_image": profile.get("image"),
                "candidate_image_digest": profile.get("image_digest"),
                "sidecar_image": profile.get("sidecar_image"),
                "sidecar_image_digest": profile.get("sidecar_image_digest"),
                "candidate_qualification_receipt_digest": profile.get("candidate_qualification_receipt_digest"),
                "sidecar_qualification_receipt_digest": profile.get("sidecar_qualification_receipt_digest"),
                "config_digest": config.get("digest", "profile-default"),
            }
            if suite_digest is not None:
                expected_admission["suite_digest"] = suite_digest
            admission_digest = verify_formal_admission(
                formal_admission_path,
                trusted_public_key_hex=os.environ.get("BENCH_FORMAL_TRUSTED_PUBLIC_KEY", ""),
                expected=expected_admission,
                trusted_key_id=os.environ.get("BENCH_FORMAL_KEY_ID", "coordinator-v1"),
            )
        except VerifierWorkerError as exc:
            raise MvpError(str(exc)) from exc
        # The signed admission is the trust anchor, while the state field is
        # only an index. A resume must prove that the controlled copy is the
        # exact admission previously recorded; otherwise another valid token
        # for this run could be substituted between attempts.
        if previous is not None and previous.get("formal_admission_digest") != admission_digest:
            raise MvpError("formal admission digest changed before resume")
        if not admission_copy.exists():
            admission_copy.parent.mkdir(parents=True, exist_ok=True)
            raw_admission = Path(formal_admission_path).expanduser()
            if raw_admission.is_symlink() or not raw_admission.is_file():
                raise MvpError("formal admission source is missing or unsafe")
            try:
                fd = os.open(admission_copy, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "wb") as target:
                    source_fd = os.open(raw_admission, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                    try:
                        source_stat = os.fstat(source_fd)
                        if source_stat.st_nlink != 1 or source_stat.st_size > 256 * 1024:
                            raise MvpError("formal admission source is too large or unsafe")
                        source_bytes = os.read(source_fd, source_stat.st_size + 1)
                        if len(source_bytes) != source_stat.st_size:
                            raise MvpError("formal admission source changed during copy")
                        target.write(source_bytes)
                    finally:
                        os.close(source_fd)
                    target.flush()
                    os.fsync(target.fileno())
            except FileExistsError as exc:
                raise MvpError("formal admission copy already exists") from exc
    else:
        admission_digest = None
    session_id = str(previous.get("session_id")) if previous else str(uuid.uuid4())
    resource_run_uid = session_id.replace("-", "")
    previous_budget = _previous_budget(previous) if previous else {}
    if previous is not None:
        canonical_transcript = str(run_dir / "transcript.jsonl")
        if previous.get("transcript") != canonical_transcript:
            raise MvpError("run transcript path is not the canonical run-scoped transcript")
        transcript_budget = _recompute_completed_budget(Path(previous["transcript"]))
        if transcript_budget != previous_budget:
            raise MvpError("run-state cumulative budget does not match completed transcript phases")
    prior_turns = int(previous_budget.get("model_turns", 0) or 0)
    prior_tokens = int(previous_budget.get("total_tokens", 0) or 0)
    prior_active = float(previous_budget.get("candidate_phase_walltime_sec", 0.0) or 0.0)
    remaining_limits = dict(effective_limits)
    remaining_limits["max_turns"] = int(effective_limits["max_turns"]) - prior_turns
    remaining_limits["max_total_tokens"] = int(effective_limits["max_total_tokens"]) - prior_tokens
    remaining_limits["agent_timeout_sec"] = float(effective_limits["agent_timeout_sec"]) - prior_active
    if (remaining_limits["max_turns"] < 1 or remaining_limits["max_total_tokens"] < 1
            or remaining_limits["agent_timeout_sec"] <= 0):
        raise MvpError("candidate limits already exhausted before resume")
    if previous is not None and previous.get("state") not in {"COMPUTE_REQUIRED"}:
        raise MvpError(f"run cannot resume from state {previous.get('state')!r}")
    consumed = set(previous.get("consumed_request_digests", [])) if previous else set()
    if previous is not None:
        _check_request_bindings(workspace, previous)
        saved_outputs = None
        if previous.get("formal") is True or previous.get("trust_mode") in {EXTERNAL_SUBMISSION, FORMAL}:
            saved_outputs = _verify_saved_operator_receipt(
                run_dir, previous, list(previous.get("compute_requests", []))
            )
        _check_operator_outputs(
            workspace, list(previous.get("compute_requests", [])),
            expected_manifest=saved_outputs or previous.get("operator_receipt_outputs"),
        )
    state = {
        "schema_version": "1.0", "run_id": run_dir.name, "session_id": session_id,
        "resource_run_uid": resource_run_uid,
        "case_id": spec.case_id, "case": str(resolve_case(case)), "formal": formal,
        "state": "CANDIDATE_RUNNING", "workspace": str(workspace), "lock": str(lock),
        "transcript": str(run_dir / "transcript.jsonl"), "agent_profile": selected_profile_name,
        "agent_profile_digest": profiles.digest("agents", selected_profile_name),
        "created_at": previous.get("created_at", _now()) if previous else _now(), "updated_at": _now(),
    }
    transcript = Path(state["transcript"])
    prompt = (workspace / "instruction.md").read_text(encoding="utf-8")
    if formal:
        prompt += "\n\nFormal mode: complete autonomously; do not request human guidance."
    skill_prompt, skill_digest = _skill_prompt(profile, run_dir)
    # Skills are supplied as a trusted system prompt. The Candidate itself is
    # always launched by ClaudeCodeAdapter inside a fresh Docker container.
    prompt = skill_prompt + "\n\n" + prompt
    normalized_config = {
        key: config[key]
        for key in ("type", "mode", "trust_mode", "agent", "model", "candidate", "environment", "compute", "limits")
        if key in config and (not isinstance(config.get(key), dict) or config.get(key))
    }
    state.update({
        "skill_digest": skill_digest,
        "runner": profile.get("runner"),
        "trust_mode": trust_mode,
        "model_id": model_override or _explicit_env("BENCH_MODEL") or config.get("model", {}).get("requested_id") or profile.get("model"),
        "candidate_image": image_override or _explicit_env("BENCH_CANDIDATE_IMAGE") or config.get("candidate", {}).get("image") or profile.get("image"),
        "task_environment_image": config.get("environment", {}).get("image"),
        "sidecar_image": sidecar_image_override or _explicit_env("BENCH_SIDECAR_IMAGE") or config.get("candidate", {}).get("sidecar_image") or profile.get("sidecar_image"),
        "compute_backend": config.get("compute", {}).get("backend", "local"),
        "compute_profile": config.get("compute", {}).get("profile"),
        "candidate_config": normalized_config,
        "candidate_config_path": config.get("_path"),
        "candidate_config_digest": config.get("digest"),
        "effective_limits": effective_limits,
        "effective_limits_source": limits_source,
        "effective_limits_path": limits_path_str,
        "effective_limits_digest": limits_digest,
        "budget_usage": {
            "model_turns": prior_turns,
            "total_tokens": prior_tokens,
            "candidate_phase_walltime_sec": prior_active,
        },
        "formal_admission_digest": admission_digest,
        "suite_digest": suite_digest,
            "candidate_image_digest": previous.get("candidate_image_digest") if previous else profile.get("image_digest"),
            "sidecar_image_digest": previous.get("sidecar_image_digest") if previous else profile.get("sidecar_image_digest"),
        "operator_receipt_digest": previous.get("operator_receipt_digest") if previous else None,
        "operator_receipt_path": previous.get("operator_receipt_path") if previous else None,
        "operator_receipt_outputs": previous.get("operator_receipt_outputs") if previous else None,
        "candidate_qualification_receipt_digest": profile.get("candidate_qualification_receipt_digest"),
        "sidecar_qualification_receipt_digest": profile.get("sidecar_qualification_receipt_digest"),
    })
    if previous is not None:
        if previous.get("public_digest") != spec_payload.get("public_digest"):
            raise MvpError("immutable public digest changed before resume")
        if previous.get("agent_profile_digest") != state["agent_profile_digest"]:
            raise MvpError("agent profile digest changed before resume")
        if previous.get("skill_digest") != skill_digest:
            raise MvpError("trusted skill digest changed before resume")
        for field in (
            "candidate_config_digest", "model_id", "candidate_image",
            "sidecar_image", "compute_backend", "compute_profile",
            "effective_limits_digest", "effective_limits", "effective_limits_source",
            "candidate_image_digest", "sidecar_image_digest",
            "formal_admission_digest",
            "suite_digest",
            "candidate_qualification_receipt_digest", "sidecar_qualification_receipt_digest",
            "operator_receipt_digest",
        ):
            if previous.get(field) != state.get(field):
                raise MvpError(f"immutable run configuration changed before resume: {field}")
        consumed.update(previous.get("compute_request_digests", {}).values())
        _freeze_existing(workspace / "compute-requests", allow_new_files=True)
        _readonly_tree(workspace / "compute-results")
    else:
        # A first-pass Candidate cannot manufacture external compute outputs.
        _readonly_tree(workspace / "compute-results")
    _write_state(run_dir, state)
    with transcript.open("a" if previous else "w", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "event": "start",
            "session_id": session_id,
            "runner": state["runner"],
            "model_id": state["model_id"],
            "candidate_image": state["candidate_image"],
            "sidecar_image": state["sidecar_image"],
            "compute_backend": state["compute_backend"],
            "at": _now(),
        }, sort_keys=True) + "\n")
    active_started = time.monotonic()
    logs: dict[str, Any] | None = None
    try:
        logs = _run_container_candidate(
            profile=profile, case_dir=Path(state["case"]), workspace=workspace,
            run_dir=run_dir, prompt=prompt, session_id=session_id,
            resume_session=previous is not None, candidate_config=config,
            model_override=model_override, image_override=image_override,
            sidecar_image_override=sidecar_image_override, formal=formal,
            resource_run_uid=resource_run_uid,
            effective_limits=remaining_limits,
        )
    except BaseException as exc:
        active_elapsed = max(0.0, time.monotonic() - active_started)
        failed_logs = getattr(exc, "safe_logs", None)
        state["budget_usage"] = {
            "model_turns": prior_turns + _observed_budget(failed_logs)["model_turns"],
            "total_tokens": prior_tokens + _observed_budget(failed_logs)["total_tokens"],
            "candidate_phase_walltime_sec": prior_active + active_elapsed,
        }
        if isinstance(failed_logs, dict):
            state["candidate_logs"] = failed_logs
            gateway = failed_logs.get("model_gateway") or {}
            if gateway.get("budget_exceeded_reason"):
                state["budget_exceeded_reason"] = gateway["budget_exceeded_reason"]
        messages_path = _publish_candidate_messages(
            run_dir, failed_logs.get("thread_dir") if isinstance(failed_logs, dict) else None
        )
        state.update({
            "state": "FAILED",
            "error_type": getattr(exc, "original_error_type", type(exc).__name__),
            # Keep the durable state useful without copying unbounded agent
            # stdout/stderr (which may contain task-private text).
            "error": f"{type(exc).__name__}: {str(exc)[:256]}",
            "updated_at": _now(),
        })
        error_type = state["error_type"]
        if error_type == "AgentBudgetExceededError":
            state["failure_kind"] = "AGENT_BUDGET_EXHAUSTED"
        elif error_type == "AgentTimeoutError":
            state["failure_kind"] = "AGENT_TIMEOUT"
        if messages_path:
            state["messages_path"] = messages_path
        with transcript.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "event": "candidate_failed",
                "error_type": state["error_type"],
                "failure_kind": state.get("failure_kind"),
                "messages_path": messages_path,
                "at": _now(),
            }, sort_keys=True) + "\n")
        _write_state(run_dir, state)
        raise
    active_elapsed = max(0.0, time.monotonic() - active_started)
    observed = _observed_budget(logs)
    phase_usage = {
        "model_turns": observed["model_turns"],
        "total_tokens": observed["total_tokens"],
        "candidate_phase_walltime_sec": active_elapsed,
    }
    state["budget_usage"] = {
        "model_turns": prior_turns + observed["model_turns"],
        "total_tokens": prior_tokens + observed["total_tokens"],
        "candidate_phase_walltime_sec": prior_active + active_elapsed,
    }
    with transcript.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": "candidate_complete", "logs": logs,
                                 "phase_usage": phase_usage, "at": _now()},
                                sort_keys=True) + "\n")
    try:
        request_files, request_digests = _validate_requests(workspace, lock, run_dir / "operator-requests")
    except MvpError as exc:
        state.update({"state": "FAILED", "error": str(exc), "updated_at": _now()})
        _write_state(run_dir, state)
        raise
    pending = [path for path in request_files if request_digests[Path(path).relative_to(workspace).as_posix()] not in consumed]
    next_state = "COMPUTE_REQUIRED" if pending else "CANDIDATE_COMPLETE"
    state.update({"state": next_state, "exit_code": 0, "updated_at": _now(), "compute_requests": request_files, "compute_request_digests": request_digests, "consumed_request_digests": sorted(consumed | (set(request_digests.values()) if previous is not None and not pending else set())), "public_digest": spec_payload["public_digest"], "candidate_logs": logs, "messages_path": logs.get("messages_path")})
    if logs.get("image_digest"):
        state["candidate_image_digest"] = logs["image_digest"]
    for field in ("requested_model", "cli_model", "upstream_model", "context_selector"):
        if field in logs:
            state[field] = logs[field]
    _write_state(run_dir, state)
    return state | {"public_digest": spec_payload["public_digest"]}


def status(run_dir: Path) -> dict[str, Any]:
    return _read_state(Path(run_dir).expanduser().resolve())


def resume(run_dir: Path) -> dict[str, Any]:
    state = _read_state(Path(run_dir).expanduser().resolve())
    if state.get("state") != "COMPUTE_REQUIRED":
        raise MvpError(f"run cannot resume from state {state.get('state')!r}")
    return start(state["case"], run_dir=Path(run_dir), formal=bool(state.get("formal")), allow_existing=True)


def evaluate(run_dir: Path) -> dict[str, Any]:
    root = Path(run_dir).expanduser().resolve()
    state = _read_state(root)
    if state.get("state") != "CANDIDATE_COMPLETE":
        raise MvpError(f"run cannot evaluate from state {state.get('state')!r}")
    if state.get("formal") is True and state.get("trust_mode") != FORMAL:
        raise MvpError("formal run state cannot be downgraded to a local trust mode")
    if state.get("trust_mode") not in {EXTERNAL_SUBMISSION, FORMAL}:
        if state.get("trust_mode") != LOCAL_DEV:
            raise MvpError("evaluation requires an external verifier trust boundary")
    fd = _operation_lock(root)
    try:
        return _evaluate_locked(root, state)
    finally:
        _release_operation_lock(root, fd)


def _evaluate_locked(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    workspace = Path(state["workspace"]).resolve()
    bundle_lock_path = Path(state["lock"]).resolve()
    bundle = check_bundle(workspace, lock_path=bundle_lock_path)
    if state.get("public_digest") != bundle.get("public_digest"):
        raise MvpError("immutable public digest changed before evaluation")
    request_files = list(state.get("compute_requests", []))
    saved_outputs = None
    if state.get("trust_mode") in {EXTERNAL_SUBMISSION, FORMAL} and request_files:
        saved_outputs = _verify_saved_operator_receipt(root, state, request_files)
    _check_operator_outputs(
        workspace, request_files,
        expected_manifest=saved_outputs or state.get("operator_receipt_outputs"),
    )
    profiles = load_infra_profiles()
    profile_name = str(state.get("agent_profile") or "claude-mvp")
    if state.get("agent_profile_digest") != profiles.digest("agents", profile_name):
        raise MvpError("agent profile digest changed before evaluation")
    profile = resolve_profile(profiles, "agents", profile_name)
    from bench.contracts.case import CaseSpec
    from bench.mvp import resolve_case
    case_path = resolve_case(state["case"])
    case_spec = CaseSpec.load(
        case_path, strict_external=_has_suite_manifest(case_path),
    )
    _, skill_digest = _skill_prompt(profile, root)
    if state.get("skill_digest") != skill_digest:
        raise MvpError("trusted skill digest changed before evaluation")
    if state.get("trust_mode") == FORMAL:
        if state.get("formal") is not True:
            raise MvpError("FORMAL evaluation requires a coordinator-admitted run")
        admission_path = root / ".control" / "formal-admission.json"
        if not admission_path.is_file() or admission_path.is_symlink():
            raise MvpError("FORMAL evaluation requires its signed admission copy")
        qualified_profile = _hydrate_formal_image_qualification(profile)
        expected_admission = {
            "run_id": root.name,
            "attempt": int(state.get("attempt", 1)),
            "case_id": case_spec.case_id,
            "case_version": case_spec.case_version,
            "public_digest": bundle["public_digest"],
            "agent_profile": profile_name,
            "agent_profile_digest": profiles.digest("agents", profile_name),
            "model_id": qualified_profile.get("model"),
            "candidate_image": qualified_profile.get("image"),
            "candidate_image_digest": qualified_profile.get("image_digest"),
            "sidecar_image": qualified_profile.get("sidecar_image"),
            "sidecar_image_digest": qualified_profile.get("sidecar_image_digest"),
            "candidate_qualification_receipt_digest": qualified_profile.get("candidate_qualification_receipt_digest"),
            "sidecar_qualification_receipt_digest": qualified_profile.get("sidecar_qualification_receipt_digest"),
            "config_digest": state.get("candidate_config_digest") or "profile-default",
        }
        suite_digest = state.get("suite_digest")
        if suite_digest is not None:
            expected_admission["suite_digest"] = suite_digest
        else:
            try:
                case_spec.path.resolve().relative_to((ROOT / "cases").resolve())
                external_case = False
            except ValueError:
                external_case = True
            if external_case and _suite_digest_for_case(case_spec.path) is None:
                raise MvpError("FORMAL external suite requires a suite-bound admission")
        from bench.core.verifier_worker import VerifierWorkerError, verify_formal_admission
        try:
            admission_digest = verify_formal_admission(
                admission_path,
                trusted_public_key_hex=os.environ.get("BENCH_FORMAL_TRUSTED_PUBLIC_KEY", ""),
                expected=expected_admission,
                trusted_key_id=os.environ.get("BENCH_FORMAL_KEY_ID", "coordinator-v1"),
            )
        except VerifierWorkerError as exc:
            raise MvpError(f"FORMAL admission rejected at evaluation: {exc}") from exc
        if state.get("formal_admission_digest") != admission_digest:
            raise MvpError("FORMAL admission digest does not match durable run state")
        for field, expected in {
            "model_id": qualified_profile.get("model"),
            "candidate_image": qualified_profile.get("image"),
            "sidecar_image": qualified_profile.get("sidecar_image"),
            "candidate_image_digest": qualified_profile.get("image_digest"),
            "sidecar_image_digest": qualified_profile.get("sidecar_image_digest"),
            "candidate_qualification_receipt_digest": qualified_profile.get("candidate_qualification_receipt_digest"),
            "sidecar_qualification_receipt_digest": qualified_profile.get("sidecar_qualification_receipt_digest"),
        }.items():
            if state.get(field) != expected:
                raise MvpError(f"FORMAL run binding changed before evaluation: {field}")
    if state.get("trust_mode") == LOCAL_DEV:
        # Local Candidate Docker + Verifier Docker is useful for iteration but
        # never produces a publication receipt or formal evidence.
        seal, sealed = _acquire_evaluation_seal(workspace, root, bundle_lock_path)
        result = evaluate_submission(
            state["case"], sealed, root / "verifier-logs", run_id=state["run_id"],
            # A case from an external paper suite may not yet have a private
            # verifier profile.  Local development still uses a separate,
            # networkless Verifier Docker with the already-bound Candidate
            # image as its safe fallback; it is never publication eligible.
            image=state.get("candidate_image"), require_qualified=False,
        )
        result["trust_mode"] = LOCAL_DEV
        result["publication_eligible"] = False
        state.update({"state": "EVALUATED", "formal": False,
                      "sealed_submission": str(sealed), "result": result,
                      "updated_at": _now()})
        _write_state(root, state)
        return {"state": state, "seal": seal.__dict__, "result": result}
    # Build the worker spec before launching any verifier process.  The worker
    # receives only the sealed submission and this case's private bundle.
    from bench.contracts.case import CaseSpec
    from bench.mvp import resolve_case, resolve_private_case_material, resolve_verifier_bundle
    from bench.core.verifier_worker import (
        VerifierWorkerError,
        VerifierWorkerSpec,
        directory_digest,
        run_verifier_worker,
        verify_verifier_receipt,
        write_verifier_receipt,
    )
    case_dir = resolve_case(state["case"])
    case_spec = CaseSpec.load(
        case_dir, strict_external=_has_suite_manifest(case_dir),
    )
    verifier_name = case_spec.verifier_profile
    if not verifier_name:
        raise MvpError("external evaluation requires a case verifier profile")
    verifier_profiles = load_infra_profiles()
    verifier_profile = resolve_profile(verifier_profiles, "verifiers", verifier_name)
    lock_name = verifier_profile.get("runtime_lock")
    if not isinstance(lock_name, str):
        raise MvpError("external evaluation requires a qualified verifier runtime lock")
    local_qualification_dir = os.environ.get("BENCH_VERIFIER_QUALIFICATION_DIR")
    if local_qualification_dir:
        qualification_root = Path(local_qualification_dir).expanduser()
        lock_candidates = [qualification_root / Path(lock_name).name,
                           qualification_root / f"{verifier_name}.lock.json"]
        verifier_lock_path = next((candidate for candidate in lock_candidates if candidate.is_file()), lock_candidates[0]).resolve()
    else:
        verifier_lock_path = (ROOT / lock_name).resolve()
    try:
        lock = json.loads(verifier_lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MvpError(f"cannot read verifier runtime lock: {exc}") from exc
    if lock.get("status") != "QUALIFIED" or lock.get("image_name") != verifier_profile.get("image"):
        raise MvpError("verifier runtime is not a qualified case-specific image")
    image_digest = lock.get("image_digest")
    if not isinstance(image_digest, str):
        raise MvpError("qualified verifier runtime lock has no image digest")
    # Resolve and re-inspect the qualified identity immediately before the
    # worker starts.  The worker then receives the immutable local-ID or
    # repo@digest reference, so a retag cannot win a TOCTOU race.
    qualified_verifier_profile = dict(verifier_profile)
    qualified_verifier_profile["runtime_lock"] = str(verifier_lock_path)
    immutable_verifier_image = _require_qualified_verifier(
        qualified_verifier_profile, verifier_name,
        allow_external=bool(local_qualification_dir),
    )
    verifier_bundle = resolve_verifier_bundle(
        case_dir, root, strict_external=_has_suite_manifest(case_dir),
    )
    reference_dir, solution_dir = resolve_private_case_material(case_dir)
    sealed = root / "sealed-submission"
    worker_spec = VerifierWorkerSpec(
        case_id=case_spec.case_id,
        verifier_profile=verifier_name,
        verifier_profile_digest=verifier_profiles.digest("verifiers", verifier_name),
        image=immutable_verifier_image,
        image_digest=image_digest,
        submission=sealed,
        verifier_bundle=verifier_bundle,
        logs=root / "verifier-logs",
        timeout_sec=float(case_spec.verifier_timeout_sec),
        run_id=state["run_id"],
        attempt=int(state.get("attempt", 1)),
        case_version=case_spec.case_version,
        public_digest=str(state["public_digest"]),
        verifier_bundle_digest=directory_digest(verifier_bundle),
        reference_digest=(directory_digest(reference_dir) if reference_dir else ""),
        solution_digest=(directory_digest(solution_dir) if solution_dir else ""),
        reference=reference_dir,
        solution=solution_dir,
        candidate_image=state.get("candidate_image"),
        candidate_image_digest=state.get("candidate_image_digest"),
        candidate_qualification_receipt_digest=state.get("candidate_qualification_receipt_digest"),
        sidecar_image=state.get("sidecar_image"),
        sidecar_image_digest=state.get("sidecar_image_digest"),
        sidecar_qualification_receipt_digest=state.get("sidecar_qualification_receipt_digest"),
        formal_admission_digest=state.get("formal_admission_digest") if state.get("formal") else None,
        tmpfs=str(verifier_profile.get("tmpfs", "/tmp:rw,noexec,nosuid,size=64m")),
        cpus=float(verifier_profile.get("cpus", 4.0)),
        memory=str(verifier_profile.get("memory", "8g")),
        pids_limit=int(verifier_profile.get("pids_limit", 512)),
    )
    try:
        signing_key = os.environ.get("BENCH_VERIFIER_SIGNING_KEY")
        trusted_public = os.environ.get("BENCH_VERIFIER_TRUSTED_PUBLIC_KEY")
        if not signing_key or not trusted_public:
            raise VerifierWorkerError(
                "formal/external evaluation requires a signed verifier receipt and pinned public key"
            )
        _validate_verifier_key_pair(signing_key, trusted_public)
        # All non-mutating verifier/image/bundle/key checks are complete now.
        # Only this point commits the run to evaluation and freezes the public
        # submission, so a preflight failure remains retryable.
        seal, sealed = _acquire_evaluation_seal(workspace, root, bundle_lock_path)
        result, receipt = run_verifier_worker(
            worker_spec, signing_key_hex=signing_key,
            key_id=os.environ.get("BENCH_VERIFIER_KEY_ID", "verifier-coordinator-v1"),
        )
        result_payload = result.to_dict()
        verify_verifier_receipt(
            receipt, spec=worker_spec, trusted_public_key_hex=trusted_public,
            trusted_key_id=os.environ.get("BENCH_VERIFIER_KEY_ID", "verifier-coordinator-v1"),
            expected_result=result_payload,
        )
        write_verifier_receipt(root / "verifier-receipt.json", receipt)
    except VerifierWorkerError as exc:
        raise MvpError(f"verifier receipt rejected: {exc}") from exc
    state.update({"state": "EVALUATED", "sealed_submission": str(sealed), "result": result_payload,
                  "verifier_receipt": str(root / "verifier-receipt.json"), "updated_at": _now()})
    _write_state(root, state)
    return {"state": state, "seal": seal.__dict__, "result": result_payload}
