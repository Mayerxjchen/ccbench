"""Discovery run classification and scientific failure taxonomy for candidate cases."""

from __future__ import annotations

import hashlib
import json
import re
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


# ── Canonical Discovery Verifier ──────────────────────────────────────

SHA256_HEX_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def verify_discovery_classification(
    run_dir: Path,
    *,
    verify_candidate_digest: bool = True,
) -> tuple[bool, dict[str, Any]]:
    """Canonical verification of discovery classification.

    Re-derives the decision from evidence, validates schema, verifies
    case_ir_digest and candidate_bundle_digest bindings, and checks
    that the recorded decision matches the derived decision.

    Returns (ok, result_dict).
    """
    import re as _re
    run_dir = Path(run_dir).resolve()
    disc_file = run_dir / "discovery" / "classification.json"

    if not disc_file.is_file():
        return False, {"errors": ["Missing discovery/classification.json"]}

    try:
        doc = json.loads(disc_file.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, {"errors": [f"Failed to parse classification.json: {exc}"]}

    recorded_decision = doc.get("decision")
    evidence = doc.get("evidence")
    errors: list[str] = []

    if not recorded_decision:
        return False, {"errors": ["classification.json missing 'decision' field"]}

    # 1. Evidence schema validation
    if not evidence or not isinstance(evidence, dict):
        return False, {"errors": ["PROMOTED decision lacks evidence dictionary"]}

    try:
        validate_discovery_evidence_doc(evidence)
    except DiscoveryEvidenceError as exc:
        return False, {"errors": [f"Evidence schema invalid: {exc}"]}

    # 2. Verify digests format (strict sha256 hex)
    cb_digest = str(evidence.get("candidate_bundle_digest", ""))
    ir_digest = str(evidence.get("case_ir_digest", ""))

    if not SHA256_HEX_RE.match(cb_digest):
        errors.append(f"candidate_bundle_digest is not valid sha256 hex: {cb_digest!r}")
    if not SHA256_HEX_RE.match(ir_digest):
        errors.append(f"case_ir_digest is not valid sha256 hex: {ir_digest!r}")

    # 3. Verify case_ir_digest binding against real artifact
    case_ir_path = run_dir / "design" / "case.ir.yaml"
    if not case_ir_path.is_file():
        case_ir_path = run_dir / "design" / "case.ir.json"

    if case_ir_path.is_file() and SHA256_HEX_RE.match(ir_digest):
        actual_ir_digest = f"sha256:{hashlib.sha256(case_ir_path.read_bytes()).hexdigest()}"
        if ir_digest != actual_ir_digest:
            errors.append(
                f"case_ir_digest mismatch: receipt={ir_digest} != actual={actual_ir_digest}"
            )

    # 4. Verify candidate_bundle_digest binding against real package
    if verify_candidate_digest and SHA256_HEX_RE.match(cb_digest):
        try:
            from bench.contracts.case import CaseSpec
            from bench.core.packager import package_candidate
            import tempfile

            draft_dir = run_dir / "draft"
            spec = CaseSpec.load(draft_dir)
            with tempfile.TemporaryDirectory() as tmp_str:
                bundle = package_candidate(spec, Path(tmp_str))
                actual_cb_digest = bundle.public_digest
            # Normalize: strip sha256: prefix for comparison
            receipt_hex = cb_digest.removeprefix("sha256:")
            actual_hex = actual_cb_digest.removeprefix("sha256:")
            if receipt_hex != actual_hex:
                errors.append(
                    f"candidate_bundle_digest mismatch: receipt={cb_digest} != actual=sha256:{actual_hex}"
                )
        except Exception as exc:
            errors.append(f"Failed to verify candidate_bundle_digest: {exc}")

    # 5. Re-derive decision from failure_class
    derived_decision = _derive_decision_from_evidence(evidence)
    if recorded_decision != derived_decision.value:
        errors.append(
            f"Decision mismatch: recorded={recorded_decision} != derived={derived_decision.value} "
            f"(evidence failure_class={evidence.get('failure_class', 'N/A')})"
        )

    if errors:
        return False, {"errors": errors}

    return True, {
        "decision": recorded_decision,
        "derived_decision": derived_decision.value,
        "evidence": evidence,
        "metrics": doc.get("metrics", {}),
    }


def _derive_decision_from_evidence(evidence: dict[str, Any]) -> DiscoveryDecision:
    """Re-derive decision from evidence failure_class and outcome."""
    fclass_str = evidence.get("failure_class", "")

    if fclass_str:
        try:
            fclass = FailureClass(fclass_str)
        except ValueError:
            return DiscoveryDecision.REJECT
    else:
        outcome = evidence.get("outcome", {})
        c_code = outcome.get("candidate_exit_code", -1)
        v_code = outcome.get("verifier_exit_code", -1)
        if c_code == 0 and v_code == 0:
            fclass = FailureClass.SUCCESS
        elif c_code == 0 and v_code != 0:
            fclass = FailureClass.AGENT_LIMITATION
        else:
            fclass = FailureClass.INFRA_INVALID

    if fclass in (FailureClass.SUCCESS, FailureClass.AGENT_LIMITATION):
        return DiscoveryDecision.PROMOTED
    elif fclass in (FailureClass.SOURCE_BLOCKED, FailureClass.RUNTIME_BLOCKED, FailureClass.CASE_DESIGN_BLOCKED):
        return DiscoveryDecision.REFINE
    else:
        return DiscoveryDecision.REJECT
