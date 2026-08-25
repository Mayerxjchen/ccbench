"""Treatment-only lock comparator for ablation studies.

Compares two resolved run locks and identifies differences. Only differences
in the declared treatment dimensions are allowed; all other differences
indicate confounds that invalidate the comparison.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Top-level keys that legitimately differ between arms and must be stripped
# before comparison. lock_digest is derived from the full payload (including
# treatment dimensions), so two valid skill-ablation locks always differ here.
_STRIP_KEYS = frozenset({"lock_digest", "checkpoint_digest"})

# Sentinel to distinguish "key absent" from "key present with value None".
_MISSING = object()


# Treatment matrices: which fields are allowed to differ for each treatment type.
TREATMENTS = {
    "skill_availability": frozenset({
        "experiment.condition_id",
        "agent.skill_bundle_digest",
    }),
    "model_identity": frozenset({
        "agent.provider",
        "agent.model_id",
        "agent.deployment_id",
        "agent.provider_model_version",
        "agent.identity_strength",
        "agent.model_identity_digest",
    }),
}


@dataclass(frozen=True)
class ComparisonDiff:
    """Result of comparing two locks under a treatment."""

    allowed_differences: tuple[str, ...]
    unexpected_differences: tuple[str, ...]

    @property
    def valid(self) -> bool:
        """True if all differences are in the allowed set."""
        return len(self.unexpected_differences) == 0


def _flatten_dict(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    """Flatten a nested dict with dot-separated keys.

    Uses _MISSING sentinel to distinguish absent keys from explicit None values.
    """
    result = {}
    for key, value in d.items():
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            result.update(_flatten_dict(value, full_key))
        else:
            result[full_key] = value
    return result


def compare_lock(
    left: dict[str, Any],
    right: dict[str, Any],
    treatment: str,
) -> ComparisonDiff:
    """Compare two lock payloads under a declared treatment.

    Returns a ComparisonDiff with allowed and unexpected differences.
    Only fields in the treatment's allowed set may differ; all other
    differences are unexpected and invalidate the comparison.
    """
    allowed_fields = TREATMENTS.get(treatment)
    if allowed_fields is None:
        raise ValueError(f"unknown treatment: {treatment!r}")

    left_flat = _flatten_dict(left)
    right_flat = _flatten_dict(right)

    # Strip keys that legitimately differ between arms (e.g. lock_digest)
    for k in _STRIP_KEYS:
        left_flat.pop(k, None)
        right_flat.pop(k, None)

    all_keys = sorted(set(left_flat.keys()) | set(right_flat.keys()))

    allowed = []
    unexpected = []

    for key in all_keys:
        left_val = left_flat.get(key, _MISSING)
        right_val = right_flat.get(key, _MISSING)
        # _MISSING vs _MISSING → equal (both absent); _MISSING vs None → differs
        if left_val is not right_val and left_val != right_val:
            if key in allowed_fields:
                allowed.append(key)
            else:
                unexpected.append(key)

    return ComparisonDiff(
        allowed_differences=tuple(allowed),
        unexpected_differences=tuple(unexpected),
    )
