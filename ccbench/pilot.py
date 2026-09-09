"""Host-side direct Claude Code pilot orchestration.

The pilot owns only a Candidate process and durable run metadata. It never
imports or invokes an Operator, scheduler, cloud client, or Docker runtime.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ccbench.config.profiles import load_infra_profiles, resolve_profile
from ccbench.mvp import MvpError, evaluate_submission, export_case, freeze_submission


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


def _command(profile: dict[str, Any], override: list[str] | None) -> list[str]:
    command = override or profile.get("command")
    if not isinstance(command, list) or not command or any(not isinstance(x, str) or not x for x in command):
        raise MvpError("agent profile command must be a non-empty argv array")
    # A command override is for fake/offline tests only and remains argv-only.
    return list(command)


def start(case: str, *, run_dir: Path, formal: bool = False, command: list[str] | None = None, allow_existing: bool = False) -> dict[str, Any]:
    run_dir = Path(run_dir).expanduser().resolve()
    if run_dir.exists() and any(run_dir.iterdir()) and not allow_existing:
        raise MvpError(f"refusing to overwrite existing pilot directory: {run_dir}")
    run_dir.mkdir(parents=True, exist_ok=True)
    workspace = run_dir / "candidate"
    lock = run_dir / "operator-lock.json"
    previous = _read_state(run_dir) if allow_existing and _state_path(run_dir).is_file() else None
    if previous is None:
        spec_payload = export_case(case, workspace, lock_path=lock)
    else:
        if Path(previous.get("workspace", "")) != workspace or not workspace.is_dir():
            raise MvpError("existing run workspace does not match durable state")
        from ccbench.mvp import check_bundle
        spec_payload = check_bundle(workspace, lock_path=lock)
    from ccbench.contracts.case import CaseSpec
    from ccbench.mvp import resolve_case
    spec = CaseSpec.load(resolve_case(case))
    profile = resolve_profile(load_infra_profiles(), "agents", spec.agent_profile or "claude-mvp")
    session_id = str(previous.get("session_id")) if previous else str(uuid.uuid4())
    state = {
        "schema_version": "1.0", "run_id": run_dir.name, "session_id": session_id,
        "case_id": spec.case_id, "case": str(resolve_case(case)), "formal": formal,
        "state": "RUNNING", "workspace": str(workspace), "lock": str(lock),
        "transcript": str(run_dir / "transcript.jsonl"), "agent_profile": spec.agent_profile,
        "agent_profile_digest": load_infra_profiles().digest("agents", spec.agent_profile or "claude-mvp"),
        "created_at": _now(), "updated_at": _now(),
    }
    _write_state(run_dir, state)
    transcript = Path(state["transcript"])
    argv = _command(profile, command)
    prompt = (workspace / "instruction.md").read_text(encoding="utf-8")
    if formal:
        prompt += "\n\nFormal mode: complete autonomously; do not request human guidance."
    env = os.environ.copy()
    env["CCBENCH_SESSION_ID"] = session_id
    env["CCBENCH_RUN_ID"] = run_dir.name
    with transcript.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": "start", "session_id": session_id, "argv": argv, "at": _now()}, sort_keys=True) + "\n")
        try:
            proc = subprocess.run(argv + [prompt], cwd=workspace, env=env, stdout=stream, stderr=subprocess.STDOUT, text=True, timeout=int(profile.get("max_turns", 64)) * 120, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            state.update({"state": "FAILED", "error": str(exc), "updated_at": _now()})
            _write_state(run_dir, state)
            raise MvpError(f"Claude Code run failed: {exc}") from exc
    request_files = sorted((workspace / "compute-requests").glob("*.json"))
    state.update({"state": "COMPUTE_REQUIRED" if request_files else "RUNNING", "exit_code": proc.returncode, "updated_at": _now(), "compute_requests": [str(p) for p in request_files]})
    _write_state(run_dir, state)
    if proc.returncode != 0:
        raise MvpError(f"Claude Code exited with status {proc.returncode}")
    return state | {"public_digest": spec_payload["public_digest"]}


def status(run_dir: Path) -> dict[str, Any]:
    return _read_state(Path(run_dir).expanduser().resolve())


def resume(run_dir: Path, *, command: list[str] | None = None) -> dict[str, Any]:
    state = _read_state(Path(run_dir).expanduser().resolve())
    if state.get("state") not in {"COMPUTE_REQUIRED", "RUNNING"}:
        raise MvpError(f"run cannot resume from state {state.get('state')!r}")
    return start(state["case"], run_dir=Path(run_dir), formal=bool(state.get("formal")), command=command, allow_existing=True)


def evaluate(run_dir: Path) -> dict[str, Any]:
    root = Path(run_dir).expanduser().resolve()
    state = _read_state(root)
    sealed = root / "sealed-submission"
    seal = freeze_submission(Path(state["workspace"]), sealed, lock_path=Path(state["lock"]))
    result = evaluate_submission(state["case"], sealed, root / "verifier-logs", run_id=state["run_id"])
    state.update({"state": "EVALUATED", "sealed_submission": str(sealed), "result": result, "updated_at": _now()})
    _write_state(root, state)
    return {"state": state, "seal": seal.__dict__, "result": result}
