#!/usr/bin/env python3
"""Marginal coverage scoring for portfolio representativeness.

Computes how much a new candidate would improve portfolio diversity:
the fraction of dimensions where the candidate introduces a NEW value
not yet present in the existing portfolio.

Usage::

    from scripts.portfolio.marginal_coverage import marginal_coverage_value

    score = marginal_coverage_value(candidate_tags, existing_tags_list)
    # 0.0 = no new coverage; 1.0 = new value on every dimension
"""
from __future__ import annotations

from dataclasses import dataclass

from dftworld_bench.contracts.case import CoverageTags, load_coverage_vocabularies

DIMENSIONS = ("scientific_domain", "method_family", "material_class", "computation_type")


def marginal_coverage_value(
    candidate: CoverageTags,
    existing: list[CoverageTags],
) -> float:
    """Compute marginal coverage value of a candidate against existing portfolio.

    Returns 0.0-1.0: fraction of dimensions where the candidate introduces
    a value not yet in the existing portfolio.  Empty candidate dimensions
    contribute nothing.  Empty existing dimensions don't count as "covered".
    Invalid values on curated dimensions cannot score as new coverage.

    Args:
        candidate: Coverage tags of the proposed new case.
        existing: Coverage tags of all existing cases in the portfolio.

    Returns:
        Float in [0.0, 1.0].
    """
    if not any(getattr(candidate, d) for d in DIMENSIONS):
        return 0.0

    vocab = load_coverage_vocabularies()

    existing_values: dict[str, set[str]] = {d: set() for d in DIMENSIONS}
    for tag in existing:
        for dim in DIMENSIONS:
            val = getattr(tag, dim)
            if val:
                allowed = vocab.get(dim, set())
                # If curated, only record valid values as existing coverage
                if not allowed or val in allowed:
                    existing_values[dim].add(val)

    new_count = 0
    scored_dims = 0
    for dim in DIMENSIONS:
        cand_val = getattr(candidate, dim)
        if not cand_val:
            continue
        scored_dims += 1
        allowed = vocab.get(dim, set())
        # Curated dimension check: invalid vocabulary values cannot score new coverage
        if allowed and cand_val not in allowed:
            continue
        if cand_val not in existing_values[dim]:
            new_count += 1

    if scored_dims == 0:
        return 0.0
    return round(new_count / scored_dims, 4)


def coverage_gaps(
    existing: list[CoverageTags],
) -> dict[str, set[str]]:
    """Return the set of values currently present per dimension.

    Useful for visualizing what's already covered.
    """
    values: dict[str, set[str]] = {d: set() for d in DIMENSIONS}
    for tag in existing:
        for dim in DIMENSIONS:
            val = getattr(tag, dim)
            if val:
                values[dim].add(val)
    return values


def rank_by_marginal_coverage(
    candidates: list[dict],
    existing: list[CoverageTags],
) -> list[dict]:
    """Rank candidates by marginal coverage value (descending).

    Each candidate dict must have a 'coverage' key with dimension values.
    Returns the same list with 'marginal_coverage_score' added, sorted
    descending by score.
    """
    for c in candidates:
        cov = c.get("coverage") or {}
        tags = CoverageTags(
            scientific_domain=cov.get("scientific_domain", ""),
            method_family=cov.get("method_family", ""),
            material_class=cov.get("material_class", ""),
            computation_type=cov.get("computation_type", ""),
        )
        c["marginal_coverage_score"] = marginal_coverage_value(tags, existing)
    return sorted(candidates, key=lambda c: -c["marginal_coverage_score"])
