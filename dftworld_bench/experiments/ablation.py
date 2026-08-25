"""Skill-ablation release and pair-comparability gates.

A paired trial (one no-skill + one with-skill attempt of the same case) may
differ ONLY in Skill availability.  Every other frozen identity field must
match, so a score difference can be attributed to the treatment and nothing
else.  A formal record must reference the frozen release commit and must not
carry a pilot experiment ID.  INFRA_INVALID observations never enter the
formal pair: they are excluded and retained in a separate invalid-run ledger.
"""

from __future__ import annotations

import hashlib
import json

RELEASE_SCHEMA = "ablation-ready-release/v1"
PROTOCOL_SCHEMA = "skill-ablation-protocol/v1"

FORMAL_EXPERIMENT_ID = "skill-ablation-v1"
PILOT_EXPERIMENT_ID = "skill-ablation-v1-pilot"

# Components the release manifest must freeze for every case/input.
REQUIRED_RELEASE_COMPONENTS = frozenset(
    {
        "schemas",
        "site_adapter",
        "cases",
        "skills",
        "resource_profiles",
        "platform_profiles",
        "compute_runtimes",
        "verifiers",
        "candidate_images",
    }
)

# Run-record fields that must be IDENTICAL across a paired trial.  run_id,
# job_id, usage and lifecycle_events legitimately differ between two attempts.
_PAIRED_FIELDS = (
    "case_id",
    "experiment_id",
    "image",
    "benchmark_commit",
    "profile",
    "submission_root",
    "verifier",
    "platform",
    "site_config_digest",
    "replicate",
    "agent_model",
    "execution_class",
)


def is_pilot_experiment(experiment_id: str | None) -> bool:
    """Pilot trials use a distinct ID and never enter the formal summary."""
    return "pilot" in (experiment_id or "")


def release_digest(release: dict) -> str:
    """Deterministic digest over the frozen manifest, excluding mutable fields.

    ``generated_at`` and ``release_digest`` are excluded so the digest is
    reproducible from the frozen inputs alone.
    """
    payload = {
        k: v for k, v in release.items() if k not in ("release_digest", "generated_at")
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def release_errors(release: dict) -> list[str]:
    """Structural gate for the release manifest.  Empty list means frozen."""
    errors: list[str] = []
    if release.get("schema") != RELEASE_SCHEMA:
        errors.append("release schema mismatch")
    for key in ("name", "source_commit", "release_digest", "components"):
        if not release.get(key):
            errors.append(f"release missing {key}")
    components = release.get("components") or {}
    missing = sorted(REQUIRED_RELEASE_COMPONENTS - set(components))
    if missing:
        errors.append(f"release missing components: {missing}")
    for key in REQUIRED_RELEASE_COMPONENTS:
        entries = components.get(key) or []
        if entries and not all(isinstance(e, dict) for e in entries):
            errors.append(f"component {key} must be a list of records")
    blob = json.dumps(release)
    if ":latest" in blob:
        errors.append("release references a mutable :latest tag")
    if "-----BEGIN" in blob:
        errors.append("release embeds a private-key-like value")
    if release.get("release_digest") != release_digest(release):
        errors.append("release_digest does not match the frozen payload")
    return errors


def protocol_errors(protocol: dict) -> list[str]:
    """Gate for the experiment protocol.  Empty list means predeclared."""
    errors: list[str] = []
    if protocol.get("schema") != PROTOCOL_SCHEMA:
        errors.append("protocol schema mismatch")
    if not protocol.get("name"):
        errors.append("protocol missing name")
    if not protocol.get("release"):
        errors.append("protocol must reference a frozen release")
    paired = protocol.get("paired_trials") or {}
    if not paired.get("same_site"):
        errors.append("paired trials must share one site")
    if not paired.get("same_profile"):
        errors.append("paired trials must share one profile")
    replicates = protocol.get("replicates")
    if not isinstance(replicates, int) or replicates < 1:
        errors.append("replicates must be a fixed positive integer")
    retry = protocol.get("retry_policy") or {}
    if retry.get("infra_invalid") != "replace-with-new-run-id":
        errors.append("infra_invalid must be replaceable by a new attempt with a new run id")
    for arm in ("scientific_failure", "agent_failure"):
        if retry.get(arm) != "no-silent-rerun":
            errors.append(f"{arm} may not be silently rerun")
    if protocol.get("treatment_difference") != "skill-availability-only":
        errors.append("treatment difference must be skill availability only")
    pilot = protocol.get("pilot") or {}
    if pilot.get("experiment_id") != PILOT_EXPERIMENT_ID:
        errors.append("pilot must use the reserved pilot experiment id")
    if pilot.get("excluded_from_formal") is not True:
        errors.append("pilot observations must be excluded from the formal result")
    return errors


def formal_record_errors(release: dict, record: dict) -> list[str]:
    """Why this record cannot enter a formal comparison.  Empty list = eligible."""
    errors: list[str] = []
    if record.get("run_mode", "formal") != "formal":
        errors.append("run_mode is not formal; smoke runs never enter a formal pair")
    if record.get("benchmark_commit") != release.get("source_commit"):
        errors.append("benchmark_commit is not the frozen release source_commit")
    if is_pilot_experiment(record.get("experiment_id")):
        errors.append("experiment_id is a pilot and may not enter the formal pair")
    result = record.get("result") or {}
    if result.get("result_class") == "INFRA_INVALID":
        errors.append("INFRA_INVALID record must be routed to the invalid-run ledger")
    return errors


def comparability_errors(no_skill: dict, with_skill: dict) -> list[str]:
    """Field-level reasons the pair is not strictly comparable.  Empty = ok."""
    errors: list[str] = []
    if no_skill.get("condition_id") != "no-skill" or with_skill.get("condition_id") != "with-skill":
        errors.append("pair must be one no-skill and one with-skill record")
    for field in _PAIRED_FIELDS:
        if no_skill.get(field) != with_skill.get(field):
            errors.append(f"{field} differs between conditions")
    # Skill availability is the ONLY permitted difference.
    if not (no_skill.get("skills_source") == "none" and no_skill.get("skills_sha") is None):
        errors.append("no-skill arm must have skills_source 'none' and a null skills_sha")
    src = with_skill.get("skills_source")
    if not (src and src != "none" and with_skill.get("skills_sha")):
        errors.append("with-skill arm must carry a non-none skills_source and a skills_sha digest")
    return errors


def partition_formal(records: list[dict]) -> tuple[list[str], list[str]]:
    """Split run records into (formal_candidates, invalid_run_ids).

    INFRA_INVALID observations are excluded from scientific denominators and
    retained in the invalid-run ledger instead of entering a formal pair.
    SMOKE observations (run_mode != "formal") are excluded from BOTH lists:
    they never enter a formal pair and never enter the invalid-run ledger.
    """
    candidates: list[str] = []
    invalid: list[str] = []
    for record in records:
        if record.get("run_mode", "formal") != "formal":
            continue
        result = record.get("result") or {}
        if result.get("result_class") == "INFRA_INVALID":
            invalid.append(record.get("run_id", ""))
        else:
            candidates.append(record.get("run_id", ""))
    return candidates, invalid
