"""Discovery run classification and scientific failure taxonomy for candidate cases."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any


class DiscoveryDecision(str, Enum):
    PROMOTED = "PROMOTED"
    REFINE = "REFINE"
    REJECT = "REJECT"


class FailureClass(str, Enum):
    """Scientific failure taxonomy for discovery run attribution."""

    SUCCESS = "SUCCESS"
    SOURCE_BLOCKED = "SOURCE_BLOCKED"
    RUNTIME_BLOCKED = "RUNTIME_BLOCKED"
    RESOURCE_BLOCKED = "RESOURCE_BLOCKED"
    CASE_DESIGN_BLOCKED = "CASE_DESIGN_BLOCKED"
    INFRA_INVALID = "INFRA_INVALID"
    AGENT_LIMITATION = "AGENT_LIMITATION"


class DiscoveryEvidenceError(ValueError):
    """Raised when discovery evidence is insufficient or malformed."""


REQUIRED_EVIDENCE_FIELDS = frozenset({
    "run_id",
    "candidate_bundle_digest",
    "case_ir_digest",
    "outcome",
    "metrics",
})


def validate_discovery_evidence_doc(doc: dict[str, Any]) -> None:
    """Validate that a discovery evidence document satisfies schema requirements."""
    if not isinstance(doc, dict):
        raise DiscoveryEvidenceError("Discovery evidence must be a JSON object")

    missing = REQUIRED_EVIDENCE_FIELDS - set(doc.keys())
    if missing:
        raise DiscoveryEvidenceError(
            f"Discovery evidence missing required fields: {sorted(missing)}"
        )

    # Validate digests format
    cb_digest = str(doc.get("candidate_bundle_digest", ""))
    if not cb_digest.startswith("sha256:"):
        raise DiscoveryEvidenceError(
            f"candidate_bundle_digest must be a sha256 hex string, got {cb_digest!r}"
        )

    ir_digest = str(doc.get("case_ir_digest", ""))
    if not ir_digest.startswith("sha256:"):
        raise DiscoveryEvidenceError(
            f"case_ir_digest must be a sha256 hex string, got {ir_digest!r}"
        )

    # Validate outcome
    outcome = doc.get("outcome")
    if not isinstance(outcome, dict):
        raise DiscoveryEvidenceError("outcome must be a dictionary")
    for key in ("terminal_state", "candidate_exit_code", "verifier_exit_code"):
        if key not in outcome:
            raise DiscoveryEvidenceError(f"outcome missing required field: '{key}'")

    # Validate metrics
    metrics = doc.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise DiscoveryEvidenceError("metrics must be a non-empty dictionary")


def classify_discovery_evidence(
    metrics_dir: Path,
) -> tuple[DiscoveryDecision, dict[str, Any]]:
    """Classify a discovery run based on structured evidence in metrics_dir.

    Parses run evidence files, validates schemas, applies failure taxonomy,
    and mechanically derives the DiscoveryDecision.
    """
    metrics_dir = Path(metrics_dir).resolve()
    if not metrics_dir.is_dir():
        raise DiscoveryEvidenceError(
            f"metrics_dir does not exist or is not a directory: {metrics_dir}"
        )

    metric_files = sorted(metrics_dir.glob("*.json"))
    if not metric_files:
        raise DiscoveryEvidenceError(
            f"No metric JSON files found in {metrics_dir}; cannot classify without evidence"
        )

    # Load and validate primary discovery evidence doc (e.g. discovery-run.json or first file)
    primary_doc: dict[str, Any] | None = None
    for mf in metric_files:
        try:
            doc = json.loads(mf.read_text(encoding="utf-8"))
            if isinstance(doc, dict) and "outcome" in doc:
                validate_discovery_evidence_doc(doc)
                primary_doc = doc
                break
        except DiscoveryEvidenceError:
            raise
        except Exception as exc:
            raise DiscoveryEvidenceError(f"Failed to parse {mf.name}: {exc}") from exc

    if primary_doc is None:
        raise DiscoveryEvidenceError(
            "No valid discovery evidence document found in metrics_dir matching schema "
            "(requires run_id, candidate_bundle_digest, case_ir_digest, outcome, metrics)"
        )

    outcome = primary_doc["outcome"]
    failure_class_str = primary_doc.get("failure_class", "")

    # Apply scientific failure taxonomy
    if failure_class_str:
        try:
            fclass = FailureClass(failure_class_str)
        except ValueError:
            raise DiscoveryEvidenceError(f"Unknown failure_class '{failure_class_str}'")
    else:
        # Infer failure class from exit codes
        c_code = outcome.get("candidate_exit_code", -1)
        v_code = outcome.get("verifier_exit_code", -1)
        if c_code == 0 and v_code == 0:
            fclass = FailureClass.SUCCESS
        elif c_code == 0 and v_code != 0:
            fclass = FailureClass.AGENT_LIMITATION
        else:
            fclass = FailureClass.INFRA_INVALID

    # Derive decision from failure class:
    # 1. SUCCESS -> PROMOTED
    # 2. AGENT_LIMITATION -> PROMOTED (agent failed on a valid scientific problem; legitimate benchmark case!)
    # 3. SOURCE_BLOCKED / RUNTIME_BLOCKED / CASE_DESIGN_BLOCKED -> REFINE (needs fix)
    # 4. RESOURCE_BLOCKED / INFRA_INVALID -> REJECT
    if fclass in (FailureClass.SUCCESS, FailureClass.AGENT_LIMITATION):
        decision = DiscoveryDecision.PROMOTED
    elif fclass in (FailureClass.SOURCE_BLOCKED, FailureClass.RUNTIME_BLOCKED, FailureClass.CASE_DESIGN_BLOCKED):
        decision = DiscoveryDecision.REFINE
    else:
        decision = DiscoveryDecision.REJECT

    primary_doc["derived_failure_class"] = fclass.value
    return decision, primary_doc


def record_discovery_result(
    run_dir: Path,
    decision: DiscoveryDecision,
    evidence: dict[str, Any],
    *,
    metrics: dict[str, Any] | None = None,
    notes: str = "",
) -> Path:
    """Record the discovery decision in discovery/classification.json.

    Args:
        run_dir: The builder run directory.
        decision: The classification decision.
        evidence: Evidence artifacts that support the decision.
        metrics: Optional additional metrics summary.
        notes: Free-form notes.
    """
    if decision == DiscoveryDecision.PROMOTED:
        if not evidence or not isinstance(evidence, dict):
            raise DiscoveryEvidenceError(
                "Cannot record PROMOTED decision without valid evidence dictionary."
            )
        # Ensure evidence satisfies schema
        if not evidence.get("manual"):
            validate_discovery_evidence_doc(evidence)

    disc_dir = Path(run_dir) / "discovery"
    disc_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "decision": decision.value if isinstance(decision, DiscoveryDecision) else str(decision),
        "evidence": evidence,
        "metrics": metrics or evidence.get("metrics", {}),
        "notes": notes,
    }
    target = disc_dir / "classification.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return target
