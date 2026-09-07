"""Atomic case publish transaction from builder workspace to canonical cases/ release directory.

Invariants:
1. Target case ID MUST strictly match Case IR case_id and CaseSpec case_id.
2. Publish is a dual-stage atomic transaction:
   - Staging public release package (task.md, case.toml, input/, verifier/).
   - Staging maintainer archive (source/, design/, discovery/, reports/, fixtures/).
3. Safe replacement: if destination exists, it is preserved in backup until the transaction commits.
4. If any error occurs, automatic rollback restores previous state and cleans up stagings.
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
    """Raised when publishing a case fails or violates invariants."""


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
    1. Case run state MUST be BENCHMARK_VALID (no force bypass allowed).
    2. Identity binding: target_case_id == Case IR case_id == CaseSpec case_id.
    3. Published directory cases/<target_case_id> MUST contain ONLY the 4 canonical objects:
       - task.md
       - input/
       - verifier/
       - case.toml
    4. Maintainer assets are archived into maintainer/cases/<target_case_id>/ atomically.
    5. Safe replace: old cases are preserved until new staging commits cleanly.
    """
    run_dir = Path(run_dir).resolve()
    draft_dir = run_dir / "draft"
    target_cases_dir = Path(cases_dir or CASES_DIR).resolve()
    target_maint_dir = Path(maintainer_dir or MAINTAINER_DIR).resolve()

    # 1. Triple identity binding: target_case_id == CaseSpec.case_id == Case IR case_id
    from ccbench.contracts.case import CaseSpec
    try:
        spec = CaseSpec.load(draft_dir)
    except Exception as exc:
        raise PublishError(f"Failed to load CaseSpec from draft: {exc}") from exc

    if spec.case_id != target_case_id:
        raise PublishError(
            f"Identity binding mismatch: target_case_id '{target_case_id}' != "
            f"CaseSpec.case_id '{spec.case_id}'"
        )

    # 2. Case IR identity binding
    case_ir_path = run_dir / "design" / "case.ir.yaml"
    if not case_ir_path.is_file():
        case_ir_path = run_dir / "design" / "case.ir.json"
    if case_ir_path.is_file():
        try:
            import yaml
            raw_text = case_ir_path.read_text(encoding="utf-8")
            ir_doc = yaml.safe_load(raw_text) if case_ir_path.suffix in (".yaml", ".yml") else json.loads(raw_text)
            if isinstance(ir_doc, dict):
                ir_case_id = (ir_doc.get("identity") or {}).get("case_id")
                if ir_case_id and ir_case_id != target_case_id:
                    raise PublishError(
                        f"Identity binding mismatch: target_case_id '{target_case_id}' != "
                        f"Case IR identity.case_id '{ir_case_id}'"
                    )
        except PublishError:
            raise
        except Exception as exc:
            raise PublishError(f"Failed to read Case IR identity: {exc}") from exc

    # 2. State check
    state = derive_state(run_dir)
    if state.current_state != CaseLifecycleState.BENCHMARK_VALID:
        raise PublishError(
            f"Cannot publish case from state '{state.current_state}'. "
            f"Run must reach '{CaseLifecycleState.BENCHMARK_VALID}' before publishing. "
            "Neither force nor manual overrides can bypass benchmark validity."
        )

    dest_case_dir = target_cases_dir / target_case_id
    dest_maint_dir = target_maint_dir / "cases" / target_case_id

    if dest_case_dir.exists() and not force:
        raise PublishError(f"Case destination already exists: {dest_case_dir}. Use force=True to safely replace.")

    # ── Transaction staging ──────────────────────────────────────────
    staging_token = uuid.uuid4().hex[:8]
    staging_public = target_cases_dir / f".staging-cases-{target_case_id}-{staging_token}"
    staging_maint = target_maint_dir / "cases" / f".staging-maint-{target_case_id}-{staging_token}"

    backup_public = target_cases_dir / f".backup-cases-{target_case_id}-{staging_token}"
    backup_maint = target_maint_dir / "cases" / f".backup-maint-{target_case_id}-{staging_token}"

    try:
        staging_public.mkdir(parents=True, exist_ok=True)
        staging_maint.mkdir(parents=True, exist_ok=True)

        verifier_dir = run_dir / "verifier" if (run_dir / "verifier").is_dir() else draft_dir / "verifier"
        input_dir = draft_dir / "input"

        # Stage 1: Public Release Package
        src_task = draft_dir / "task.md"
        if not src_task.is_file():
            raise PublishError(f"Draft missing task.md: {src_task}")
        shutil.copy2(src_task, staging_public / "task.md")

        src_toml = draft_dir / "case.toml"
        if not src_toml.is_file():
            raise PublishError(f"Draft missing case.toml: {src_toml}")
        shutil.copy2(src_toml, staging_public / "case.toml")

        dest_input = staging_public / "input"
        if input_dir.is_dir():
            shutil.copytree(input_dir, dest_input)
        else:
            dest_input.mkdir(exist_ok=True)

        dest_verifier = staging_public / "verifier"
        if verifier_dir.is_dir():
            shutil.copytree(verifier_dir, dest_verifier)
        else:
            raise PublishError(f"Verifier directory missing at {verifier_dir}")

        # Verify exactly the 4 canonical objects in staging
        allowed_names = {"task.md", "case.toml", "input", "verifier"}
        actual_names = {p.name for p in staging_public.iterdir()}
        unexpected = actual_names - allowed_names
        if unexpected:
            raise PublishError(f"Published case contains non-canonical objects: {unexpected}")

        # Stage 2: Maintainer Private Archive
        for subdir in ("source", "design", "discovery", "reports", "fixtures", "evidence"):
            sdir = run_dir / subdir
            if sdir.is_dir():
                shutil.copytree(sdir, staging_maint / subdir)

        # ── Commit Phase ─────────────────────────────────────────────
        # Track whether this is a new case (no pre-existing dest)
        had_public = dest_case_dir.exists()
        had_maint = dest_maint_dir.exists()

        # 1. Backup existing public case if replacing
        if had_public:
            os.rename(str(dest_case_dir), str(backup_public))

        # 2. Atomic rename of staging -> final public
        os.rename(str(staging_public), str(dest_case_dir))

        # 3. Backup existing maintainer archive if replacing
        if had_maint:
            os.rename(str(dest_maint_dir), str(backup_maint))

        # 4. Atomic rename of staging -> final maintainer
        os.rename(str(staging_maint), str(dest_maint_dir))

        # 5. Remove temporary backups
        if backup_public.exists():
            shutil.rmtree(backup_public, ignore_errors=True)
        if backup_maint.exists():
            shutil.rmtree(backup_maint, ignore_errors=True)

    except Exception as exc:
        # ── Rollback Phase ───────────────────────────────────────────
        if staging_public.exists():
            shutil.rmtree(staging_public, ignore_errors=True)
        if staging_maint.exists():
            shutil.rmtree(staging_maint, ignore_errors=True)

        # Restore public backup if it was moved (replacing existing case)
        if backup_public.exists():
            if dest_case_dir.exists():
                shutil.rmtree(dest_case_dir, ignore_errors=True)
            os.rename(str(backup_public), str(dest_case_dir))
        elif not had_public and dest_case_dir.exists():
            # New case: no backup exists, but dest was created in this transaction
            # Roll back by removing the orphaned public case
            shutil.rmtree(dest_case_dir, ignore_errors=True)

        # Restore maintainer backup if it was moved (replacing existing archive)
        if backup_maint.exists():
            if dest_maint_dir.exists():
                shutil.rmtree(dest_maint_dir, ignore_errors=True)
            os.rename(str(backup_maint), str(dest_maint_dir))
        elif not had_maint and dest_maint_dir.exists():
            # New case: no backup exists, but maintainer was created in this transaction
            shutil.rmtree(dest_maint_dir, ignore_errors=True)

        raise PublishError(f"Publish transaction aborted and rolled back: {exc}") from exc

    # ── Record completion ────────────────────────────────────────────
    pub_marker = run_dir / "reports" / "published.json"
    pub_marker.parent.mkdir(parents=True, exist_ok=True)
    pub_marker.write_text(
        json.dumps({
            "published": True,
            "target_case_id": target_case_id,
            "path": str(dest_case_dir),
            "maintainer_path": str(dest_maint_dir),
        }, indent=2),
        encoding="utf-8",
    )

    return dest_case_dir
