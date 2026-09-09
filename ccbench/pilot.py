"""Host-side direct Claude Code pilot orchestration.

The pilot owns only a Candidate process and durable run metadata. It never
imports or invokes an Operator, scheduler, cloud client, or Docker runtime.
"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ccbench.config.profiles import load_infra_profiles, resolve_profile
from ccbench.mvp import (
    MvpError,
    check_bundle,
    evaluate_submission,
    export_case,
    freeze_submission,
    materialize_compute_request,
    validate_compute_request,
)
from ccbench.core.digests import sha256_file
from ccbench.paths import ROOT

_SAFE_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep")
_SAFE_ENV = frozenset({"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "LANGUAGE", "TZ"})
_UNSAFE_FLAGS = frozenset({
    "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions",
    "--tools", "--allowed-tools", "--mcp-config", "--plugin-dir", "--plugin-url",
    "--settings", "--permission-mode", "--add-dir", "--remote-control", "--cloud",
    "--model", "--effort", "--max-budget-usd", "--session-id", "--resume",
    "--append-system-prompt", "--system-prompt", "--disable-slash-commands",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _check_operator_outputs(workspace: Path, request_files: list[str]) -> None:
    for request_name in request_files:
        payload = json.loads(Path(request_name).read_text(encoding="utf-8"))
        for output in payload.get("outputs", []):
            path = workspace / output
            if path.is_symlink() or not path.is_file():
                raise MvpError(f"operator output is missing or unsafe: {output}")


def _check_request_bindings(workspace: Path, previous: dict[str, Any]) -> None:
    bindings = previous.get("compute_request_digests")
    if not isinstance(bindings, dict):
        raise MvpError("run state has no request digest bindings")
    for rel, expected in bindings.items():
        path = workspace / rel
        if path.is_symlink() or not path.is_file() or sha256_file(path) != expected:
            raise MvpError(f"bound compute request changed: {rel}")


def import_results(run_dir: Path, source: Path) -> dict[str, Any]:
    """Import Operator-produced files through a trusted, explicit handoff."""
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
        results_root = workspace / "compute-results"
        results_root.chmod(0o755)
        for request_name in requests:
            payload = json.loads(Path(request_name).read_text(encoding="utf-8"))
            for output in payload.get("outputs", []):
                src = source / output
                dst = workspace / output
                if src.is_symlink() or not src.is_file():
                    raise MvpError(f"operator output is missing or unsafe: {output}")
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists() or dst.is_symlink():
                    raise MvpError(f"refusing to overwrite Candidate result: {output}")
                shutil.copy2(src, dst)
        _readonly_tree(results_root)
        state["operator_results_source"] = str(source)
        state["updated_at"] = _now()
        _write_state(root, state)
        return state
    finally:
        _release_operation_lock(root, fd)


def _command(profile: dict[str, Any]) -> list[str]:
    command = profile.get("command")
    if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
        raise MvpError("agent profile command must be a non-empty argv array")
    for token in command:
        if token in _UNSAFE_FLAGS or token in {"default", "bypassPermissions", "auto"}:
            raise MvpError(f"agent command contains a forbidden security override: {token}")
    resolved = shutil.which(command[0])
    if not resolved or Path(resolved).name.lower() != "claude":
        raise MvpError("agent profile command must resolve to the trusted claude executable")
    return list(command)


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


def _secure_argv(
    profile: dict[str, Any],
    command: list[str],
    workspace: Path,
    session_id: str,
    *,
    resume_session: bool,
) -> list[str]:
    policy_path = ROOT / str(profile.get("tool_policy", "infra/config/tool-policy.json"))
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MvpError(f"candidate tool policy is unavailable: {exc}") from exc
    if policy.get("allowed_tools") != list(_SAFE_TOOLS) or policy.get("allowed_network_destinations") != []:
        raise MvpError("candidate tool policy is not the frozen no-network file-tool policy")
    argv = list(command)
    argv += [
        "--bare", "--restricted", "--strict-mcp-config",
        "--permission-prompts", "none", "--disable-slash-commands",
        "--tools", ",".join(_SAFE_TOOLS), "--add-dir", str(workspace),
        "--model", str(profile["model"]), "--effort", str(profile["effort"]),
        "--max-budget-usd", str(profile["max_budget_usd"]),
    ]
    argv += ["--resume", session_id] if resume_session else ["--session-id", session_id]
    return argv


def _safe_env(profile: dict[str, Any]) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key in _SAFE_ENV}
    for key_name in (profile.get("api_endpoint_env"), profile.get("api_key_env")):
        if isinstance(key_name, str) and key_name in os.environ:
            env[key_name] = os.environ[key_name]
    return env


def start(case: str, *, run_dir: Path, formal: bool = False, allow_existing: bool = False) -> dict[str, Any]:
    run_dir = Path(run_dir).expanduser().resolve()
    if run_dir.exists() and any(run_dir.iterdir()) and not allow_existing:
        raise MvpError(f"refusing to overwrite existing pilot directory: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    operation_fd = _operation_lock(run_dir)
    try:
        return _start_locked(case, run_dir=run_dir, formal=formal, allow_existing=allow_existing)
    finally:
        _release_operation_lock(run_dir, operation_fd)


def _start_locked(case: str, *, run_dir: Path, formal: bool, allow_existing: bool) -> dict[str, Any]:
    workspace = run_dir / "candidate"
    lock = run_dir / "operator-lock.json"
    previous = _read_state(run_dir) if allow_existing and _state_path(run_dir).is_file() else None
    if previous is None:
        spec_payload = export_case(case, workspace, lock_path=lock)
    else:
        if Path(previous.get("workspace", "")) != workspace or not workspace.is_dir():
            raise MvpError("existing run workspace does not match durable state")
        spec_payload = check_bundle(workspace, lock_path=lock)
    from ccbench.contracts.case import CaseSpec
    from ccbench.mvp import resolve_case
    spec = CaseSpec.load(resolve_case(case))
    profile = resolve_profile(load_infra_profiles(), "agents", spec.agent_profile or "claude-mvp")
    session_id = str(previous.get("session_id")) if previous else str(uuid.uuid4())
    if previous is not None and previous.get("state") not in {"COMPUTE_REQUIRED"}:
        raise MvpError(f"run cannot resume from state {previous.get('state')!r}")
    consumed = set(previous.get("consumed_request_digests", [])) if previous else set()
    if previous is not None:
        _check_request_bindings(workspace, previous)
        _check_operator_outputs(workspace, list(previous.get("compute_requests", [])))
    state = {
        "schema_version": "1.0", "run_id": run_dir.name, "session_id": session_id,
        "case_id": spec.case_id, "case": str(resolve_case(case)), "formal": formal,
        "state": "CANDIDATE_RUNNING", "workspace": str(workspace), "lock": str(lock),
        "transcript": str(run_dir / "transcript.jsonl"), "agent_profile": spec.agent_profile,
        "agent_profile_digest": load_infra_profiles().digest("agents", spec.agent_profile or "claude-mvp"),
        "created_at": previous.get("created_at", _now()) if previous else _now(), "updated_at": _now(),
    }
    transcript = Path(state["transcript"])
    argv = _command(profile)
    prompt = (workspace / "instruction.md").read_text(encoding="utf-8")
    if formal:
        prompt += "\n\nFormal mode: complete autonomously; do not request human guidance."
    skill_prompt, skill_digest = _skill_prompt(profile, run_dir)
    argv = _secure_argv(
        profile, argv, workspace, session_id, resume_session=previous is not None
    )
    env = _safe_env(profile)
    state.update({"skill_digest": skill_digest, "argv": argv})
    if previous is not None:
        if previous.get("public_digest") != spec_payload.get("public_digest"):
            raise MvpError("immutable public digest changed before resume")
        if previous.get("agent_profile_digest") != state["agent_profile_digest"]:
            raise MvpError("agent profile digest changed before resume")
        if previous.get("skill_digest") != skill_digest:
            raise MvpError("trusted skill digest changed before resume")
        consumed.update(previous.get("compute_request_digests", {}).values())
        _freeze_existing(workspace / "compute-requests", allow_new_files=True)
        _readonly_tree(workspace / "compute-results")
    else:
        # A first-pass Candidate cannot manufacture external compute outputs.
        _readonly_tree(workspace / "compute-results")
    _write_state(run_dir, state)
    with transcript.open("a" if previous else "w", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": "start", "session_id": session_id, "argv": argv, "at": _now()}, sort_keys=True) + "\n")
        try:
            proc = subprocess.run(argv + ["--append-system-prompt", skill_prompt, prompt], cwd=workspace, env=env, stdout=stream, stderr=subprocess.STDOUT, text=True, timeout=int(profile.get("max_turns", 64)) * 120, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            state.update({"state": "FAILED", "error": str(exc), "updated_at": _now()})
            _write_state(run_dir, state)
            raise MvpError(f"Claude Code run failed: {exc}") from exc
    if proc.returncode != 0:
        state.update({"state": "FAILED", "error": f"Claude Code exited with status {proc.returncode}", "exit_code": proc.returncode, "updated_at": _now()})
        _write_state(run_dir, state)
        raise MvpError(f"Claude Code exited with status {proc.returncode}")
    try:
        request_files, request_digests = _validate_requests(workspace, lock, run_dir / "operator-requests")
    except MvpError as exc:
        state.update({"state": "FAILED", "error": str(exc), "updated_at": _now()})
        _write_state(run_dir, state)
        raise
    pending = [path for path in request_files if request_digests[Path(path).relative_to(workspace).as_posix()] not in consumed]
    next_state = "COMPUTE_REQUIRED" if pending else "CANDIDATE_COMPLETE"
    state.update({"state": next_state, "exit_code": proc.returncode, "updated_at": _now(), "compute_requests": request_files, "compute_request_digests": request_digests, "consumed_request_digests": sorted(consumed | (set(request_digests.values()) if previous is not None and not pending else set())), "public_digest": spec_payload["public_digest"]})
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
    fd = _operation_lock(root)
    try:
        return _evaluate_locked(root, state)
    finally:
        _release_operation_lock(root, fd)


def _evaluate_locked(root: Path, state: dict[str, Any]) -> dict[str, Any]:
    workspace = Path(state["workspace"]).resolve()
    lock_path = Path(state["lock"]).resolve()
    bundle = check_bundle(workspace, lock_path=lock_path)
    if state.get("public_digest") != bundle.get("public_digest"):
        raise MvpError("immutable public digest changed before evaluation")
    profiles = load_infra_profiles()
    profile_name = str(state.get("agent_profile") or "claude-mvp")
    if state.get("agent_profile_digest") != profiles.digest("agents", profile_name):
        raise MvpError("agent profile digest changed before evaluation")
    profile = resolve_profile(profiles, "agents", profile_name)
    _, skill_digest = _skill_prompt(profile, root)
    if state.get("skill_digest") != skill_digest:
        raise MvpError("trusted skill digest changed before evaluation")
    evaluate_lock = root / ".evaluate.lock"
    try:
        fd = os.open(evaluate_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"run_id": state["run_id"], "at": _now()}, stream)
    except FileExistsError as exc:
        raise MvpError("run evaluation is already sealed or in progress") from exc
    sealed = root / "sealed-submission"
    seal = freeze_submission(workspace, sealed, lock_path=lock_path)
    result = evaluate_submission(state["case"], sealed, root / "verifier-logs", run_id=state["run_id"])
    state.update({"state": "EVALUATED", "sealed_submission": str(sealed), "result": result, "updated_at": _now()})
    _write_state(root, state)
    return {"state": state, "seal": seal.__dict__, "result": result}
