"""Marginal coverage analysis for portfolio."""

from __future__ import annotations

from bench.contracts.case import CoverageTags, load_coverage_vocabularies

DIMENSIONS = ("scientific_domain", "method_family", "material_class", "computation_type")


def marginal_coverage_value(
    candidate: CoverageTags,
    existing: list[CoverageTags],
) -> float:
    """Compute marginal coverage value of a candidate against existing portfolio."""
    cand_dict = (
        {dim: getattr(candidate, dim, "") for dim in DIMENSIONS}
        if not isinstance(candidate, dict)
        else candidate
    )
    vocab = load_coverage_vocabularies()

    new_dims = 0
    total_valid = 0

    for dim in DIMENSIONS:
        val = cand_dict.get(dim)
        if not val:
            continue

        allowed = vocab.get(dim, set())
        if allowed and val not in allowed:
            continue

        total_valid += 1
        existing_vals = {
            getattr(e, dim, "") if not isinstance(e, dict) else e.get(dim, "")
            for e in existing
        }
        if val not in existing_vals:
            new_dims += 1

    if total_valid == 0:
        return 0.0
    return new_dims / float(len(DIMENSIONS))
