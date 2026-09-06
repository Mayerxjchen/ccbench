"""Discovery run classification for candidate cases.

Security invariant: PROMOTED decisions require evidence artifacts.
CLI cannot directly declare PROMOTED without a valid metrics directory.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any


class DiscoveryDecision(str, Enum):
    PROMOTED = "PROMOTED"
    REFINE = "REFINE"
    REJECT = "REJECT"


class DiscoveryEvidenceError(ValueError):
    """Raised when discovery evidence is insufficient for the requested decision."""


def classify_discovery_evidence(
    metrics_dir: Path,
) -> tuple[DiscoveryDecision, dict[str, Any]]:
    """Classify a discovery run based on evidence in metrics_dir.

    Returns (decision, metrics_summary).
    metrics_dir must contain at least one JSON file with discovery metrics.
    """
    metrics_dir = Path(metrics_dir)
    if not metrics_dir.is_dir():
        raise DiscoveryEvidenceError(
            f"metrics_dir does not exist or is not a directory: {metrics_dir}"
        )

    metric_files = sorted(metrics_dir.glob("*.json"))
    if not metric_files:
        raise DiscoveryEvidenceError(
            f"No metric JSON files found in {metrics_dir}; "
            f"cannot classify without evidence"
        )

    # Aggregate metrics from all discovery run outputs
    aggregated: dict[str, Any] = {}
    for mf in metric_files:
        try:
            doc = json.loads(mf.read_text(encoding="utf-8"))
            if not isinstance(doc, dict):
                raise DiscoveryEvidenceError(
                    f"Metric file {mf.name} is not a JSON object"
                )
            aggregated[mf.stem] = doc
        except json.JSONDecodeError as e:
            raise DiscoveryEvidenceError(
                f"Metric file {mf.name} is not valid JSON: {e}"
            ) from e

    # Evidence-based decision: check if any metric file explicitly signals failure
    any_failure = False
    for name, metrics in aggregated.items():
        if metrics.get("passed") is False:
            any_failure = True
            break

    if any_failure:
        decision = DiscoveryDecision.REFINE
    else:
        decision = DiscoveryDecision.PROMOTED

    return decision, aggregated


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
        evidence: Evidence artifacts that support the decision.  For PROMOTED,
            this must be non-empty (enforced here, not at call site).
        metrics: Optional additional metrics summary.
        notes: Free-form notes.

    Raises:
        DiscoveryEvidenceError: If decision is PROMOTED but evidence is empty.
    """
    if decision == DiscoveryDecision.PROMOTED:
        if not evidence:
            raise DiscoveryEvidenceError(
                "Cannot record PROMOTED decision without non-empty evidence. "
                "Run classify_discovery_evidence() first."
            )

    disc_dir = Path(run_dir) / "discovery"
    disc_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "decision": decision.value if isinstance(decision, DiscoveryDecision) else str(decision),
        "evidence": evidence,
        "metrics": metrics or {},
        "notes": notes,
    }
    target = disc_dir / "classification.json"
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return target
