"""Atomic case publisher from builder workspace to canonical cases/ release directory.

Security invariant: publication is atomic via staging directory + os.rename().
Partial failures never leave orphan directories in the target.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from ccbench.builder.state import CaseLifecycleState, derive_state
from ccbench.paths import CASES_DIR, MAINTAINER_DIR


class PublishError(RuntimeError):
    """Raised when publishing a case fails."""


def publish_case(
    run_dir: Path,
    target_case_id: str,
    *,
    cases_dir: Path | None = None,
    maintainer_dir: Path | None = None,
    force: bool = False,
) -> Path:
    """Atomically publish a benchmark case from a validated run directory.

    Strict rules:
    1. Case run state MUST be BENCHMARK_VALID (unless force=True).
    2. Published directory cases/<target_case_id> MUST contain ONLY the 4 canonical objects:
       - task.md
       - input/
       - verifier/
       - case.toml
    3. Maintainer-only assets (source, design, discovery, reports) are archived into
       maintainer/cases/<target_case_id>/ and NEVER leaked into cases/.

    Atomicity: All files are staged to a temporary directory first.  Only after
    all checks pass is the staging directory renamed to the final location via
    os.rename() (atomic on the same filesystem).  Any error triggers cleanup.
    """
    run_dir = Path(run_dir)
    target_cases_dir = cases_dir or CASES_DIR
    target_maint_dir = maintainer_dir or MAINTAINER_DIR

    state = derive_state(run_dir)
    if state.current_state != CaseLifecycleState.BENCHMARK_VALID:
        raise PublishError(
            f"Cannot publish case from state '{state.current_state}'. "
            f"Run must reach '{CaseLifecycleState.BENCHMARK_VALID}' before publishing. "
            "Neither force nor manual overrides can bypass benchmark validity."
        )

    dest_case_dir = target_cases_dir / target_case_id
    if dest_case_dir.exists():
        if not force:
            raise PublishError(f"Case destination already exists: {dest_case_dir}")
        shutil.rmtree(dest_case_dir)

    # ── Stage to temporary directory ─────────────────────────────────
    staging_id = uuid.uuid4().hex[:8]
    staging_dir = target_cases_dir / f".staging-{target_case_id}-{staging_id}"

    try:
        staging_dir.mkdir(parents=True, exist_ok=True)

        draft_dir = run_dir / "draft"
        verifier_dir = run_dir / "verifier" if (run_dir / "verifier").is_dir() else draft_dir / "verifier"
        input_dir = draft_dir / "input"

        # 1. Copy task.md
        src_task = draft_dir / "task.md"
        if not src_task.is_file():
            raise PublishError(f"Draft missing task.md: {src_task}")
        shutil.copy2(src_task, staging_dir / "task.md")

        # 2. Copy case.toml
        src_toml = draft_dir / "case.toml"
        if not src_toml.is_file():
            raise PublishError(f"Draft missing case.toml: {src_toml}")
        shutil.copy2(src_toml, staging_dir / "case.toml")

        # 3. Copy input/
        dest_input = staging_dir / "input"
        if input_dir.is_dir():
            shutil.copytree(input_dir, dest_input)
        else:
            dest_input.mkdir(exist_ok=True)

        # 4. Copy verifier/
        dest_verifier = staging_dir / "verifier"
        if verifier_dir.is_dir():
            shutil.copytree(verifier_dir, dest_verifier)
        else:
            raise PublishError(f"Verifier directory missing at {verifier_dir}")

        # ── Verify exactly the 4 canonical objects ───────────────────
        allowed_names = {"task.md", "case.toml", "input", "verifier"}
        actual_names = {p.name for p in staging_dir.iterdir()}
        unexpected = actual_names - allowed_names
        if unexpected:
            raise PublishError(f"Published case contains non-canonical objects: {unexpected}")

        # ── Atomic rename: staging → final ───────────────────────────
        os.rename(str(staging_dir), str(dest_case_dir))

    except Exception:
        # Clean up staging on ANY failure
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    # ── Archive maintainer assets (non-critical, after atomic publish) ─
    maint_case_dir = target_maint_dir / "cases" / target_case_id
    maint_case_dir.mkdir(parents=True, exist_ok=True)

    for subdir in ("source", "design", "discovery", "reports", "fixtures", "evidence"):
        sdir = run_dir / subdir
        if sdir.is_dir():
            dest_sdir = maint_case_dir / subdir
            if dest_sdir.exists():
                shutil.rmtree(dest_sdir)
            shutil.copytree(sdir, dest_sdir)

    # Record publish completion in run_dir
    pub_marker = run_dir / "reports" / "published.json"
    pub_marker.parent.mkdir(parents=True, exist_ok=True)
    pub_marker.write_text(
        json.dumps({
            "published": True,
            "target_case_id": target_case_id,
            "path": str(dest_case_dir),
        }, indent=2),
        encoding="utf-8",
    )

    return dest_case_dir
