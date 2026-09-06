"""Case Builder Admission Gate — Evaluates candidate cases before heavy construction."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AdmissionDecision(str, Enum):
    """Decision produced by the admission gate."""

    ADMIT = "ADMIT"
    REFINE = "REFINE"
    REJECT = "REJECT"


@dataclass(frozen=True)
class AdmissionReport:
    """Detailed evidence and score card for a case admission proposal."""

    decision: AdmissionDecision
    reasons: list[str]
    scores: dict[str, float] = field(default_factory=dict)
    marginal_coverage: dict[str, Any] = field(default_factory=dict)

    def is_admitted(self) -> bool:
        return self.decision == AdmissionDecision.ADMIT


def evaluate_admission(
    proposal: dict[str, Any],
    portfolio_registry: dict[str, Any] | None = None,
) -> AdmissionReport:
    """Evaluate a proposed case against portfolio admission criteria."""
    reasons = []
    scores = {}

    target = proposal.get("scientific_target", {})
    if not target.get("system") or not target.get("objective"):
        reasons.append("Missing essential scientific target (system or objective)")
        return AdmissionReport(
            decision=AdmissionDecision.REJECT,
            reasons=reasons,
            scores={"source_sufficiency": 0.0},
        )

    scores["source_sufficiency"] = 1.0

    # Leakage risk check
    sources = proposal.get("sources", [])
    has_gold = any(s.get("tier") == "GOLD_SOURCE" for s in sources)
    leakage_risk = 0.8 if has_gold else 0.1
    scores["leakage_risk"] = leakage_risk

    # Execution feasibility
    runtime = proposal.get("runtime", {})
    if not runtime.get("candidate_image"):
        reasons.append("Missing candidate runtime image specification")
        return AdmissionReport(
            decision=AdmissionDecision.REFINE,
            reasons=reasons,
            scores=scores,
        )

    # Portfolio overlap check
    existing_domains = set()
    if portfolio_registry:
        for case in portfolio_registry.get("cases", []):
            cov = case.get("coverage", {})
            existing_domains.add((
                cov.get("scientific_domain"),
                cov.get("method_family"),
                cov.get("material_class"),
            ))

    cov = proposal.get("coverage", {})
    cand_tuple = (
        cov.get("scientific_domain"),
        cov.get("method_family"),
        cov.get("material_class"),
    )

    is_duplicate = cand_tuple in existing_domains and len(existing_domains) > 0
    scores["overlap"] = 1.0 if is_duplicate else 0.0

    if is_duplicate:
        reasons.append(
            f"Case duplicates existing portfolio coverage tuple: {cand_tuple}"
        )
        return AdmissionReport(
            decision=AdmissionDecision.REFINE,
            reasons=reasons,
            scores=scores,
        )

    reasons.append("Case satisfies novelty, feasibility, and verifiability gates")
    return AdmissionReport(
        decision=AdmissionDecision.ADMIT,
        reasons=reasons,
        scores=scores,
        marginal_coverage={"new_tuple": cand_tuple},
    )
