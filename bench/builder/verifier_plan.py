"""Verifier plan specification, layer explicitness, and threshold SSOT binding."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LayerStatus(str, Enum):
    """Explicit status of a verifier layer."""

    SELECTED = "selected"
    DEFERRED = "deferred"
    NOT_APPLICABLE = "not_applicable"


# Mandatory layers that can never be deferred or marked not_applicable
MANDATORY_STRUCTURAL_LAYERS: frozenset[str] = frozenset({"V0", "V1"})

ALL_STANDARD_LAYERS: tuple[str, ...] = (
    "V0",  # Submission existence
    "V1",  # Artifact manifest & schema integrity
    "V2",  # Finite-value & numerical sanity (no NaN/Inf)
    "V3",  # Execution trace & provenance
    "V4",  # Scientific accuracy & threshold metrics (e.g. RMSE)
    "V5",  # Baseline comparison & relative lift
    "V6",  # Generalization / hold-out evaluation
    "V7",  # Stability & physical consistency (e.g. MD stability)
    "V8",  # Blind / holdout challenge evaluation
)


class VerifierPlanError(ValueError):
    """Raised when a verifier plan violates consistency or integrity invariants."""


@dataclass
class LayerDeclaration:
    """Explicit declaration for a verification layer."""

    layer: str
    status: LayerStatus = LayerStatus.SELECTED
    reason: str = ""

    def __post_init__(self):
        if self.layer in MANDATORY_STRUCTURAL_LAYERS and self.status != LayerStatus.SELECTED:
            raise VerifierPlanError(
                f"Mandatory structural layer '{self.layer}' cannot be {self.status.value}; "
                "it must always be 'selected'."
            )
        if self.status in (LayerStatus.DEFERRED, LayerStatus.NOT_APPLICABLE) and not self.reason.strip():
            raise VerifierPlanError(
                f"Layer '{self.layer}' is {self.status.value} but missing a non-empty reason."
            )


@dataclass
class VerifierRule:
    """A single verification primitive check."""

    primitive: str
    target: str
    layer: str = "V1"
    threshold_ref: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    weight: float = 1.0


@dataclass
class VerifierPlan:
    """Authoritative declarative verifier plan for a benchmark case."""

    case_id: str
    layers: list[LayerDeclaration]
    rules: list[VerifierRule]
    thresholds: dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        # 0. Normalize layer entries to LayerDeclaration
        normalized_layers: list[LayerDeclaration] = []
        for l in self.layers:
            if isinstance(l, str):
                normalized_layers.append(LayerDeclaration(layer=l, status=LayerStatus.SELECTED))
            elif isinstance(l, LayerDeclaration):
                normalized_layers.append(l)
            elif isinstance(l, dict):
                normalized_layers.append(
                    LayerDeclaration(
                        layer=l["layer"],
                        status=LayerStatus(l.get("status", "selected")),
                        reason=l.get("reason", ""),
                    )
                )
        self.layers = normalized_layers

        # 1. Resolve and bind threshold references
        for rule in self.rules:
            if rule.threshold_ref:
                if rule.threshold_ref not in self.thresholds:
                    raise VerifierPlanError(
                        f"Unresolved threshold_ref '{rule.threshold_ref}' for primitive "
                        f"'{rule.primitive}'; declared thresholds: {sorted(self.thresholds.keys())}"
                    )
                # Bind the resolved threshold value directly into params
                rule.params["threshold"] = self.thresholds[rule.threshold_ref]

        # 2. Verify all selected layers have at least one rule
        selected_layers = {l.layer for l in self.layers if l.status == LayerStatus.SELECTED}
        rules_layers = {r.layer for r in self.rules}
        for layer in selected_layers:
            if layer not in rules_layers:
                raise VerifierPlanError(
                    f"Selected layer '{layer}' has no associated verification rules; "
                    "no silent drop permitted."
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "layers": [
                {
                    "layer": l.layer,
                    "status": l.status.value,
                    "reason": l.reason,
                }
                for l in self.layers
            ],
            "thresholds": self.thresholds,
            "rules": [
                {
                    "primitive": r.primitive,
                    "target": r.target,
                    "layer": r.layer,
                    "threshold_ref": r.threshold_ref,
                    "params": r.params,
                    "weight": r.weight,
                }
                for r in self.rules
            ],
        }

    @classmethod
    def from_case_ir(cls, case_ir: dict[str, Any]) -> VerifierPlan:
        """Derive authoritative verifier plan from Case IR."""
        case_id = case_ir.get("identity", {}).get("case_id", "draft-case")
        verif = case_ir.get("verification", {})
        raw_layers = verif.get("layers", ["V0", "V1", "V2", "V4"])
        thresholds = verif.get("thresholds", {})

        # Build LayerDeclarations
        layer_decls: list[LayerDeclaration] = []
        selected_set = set(raw_layers)
        for std_layer in ALL_STANDARD_LAYERS:
            if std_layer in selected_set:
                layer_decls.append(LayerDeclaration(layer=std_layer, status=LayerStatus.SELECTED))
            else:
                layer_decls.append(
                    LayerDeclaration(
                        layer=std_layer,
                        status=LayerStatus.NOT_APPLICABLE,
                        reason=f"Not required for {case_ir.get('identity', {}).get('category', 'case')}",
                    )
                )

        # Enforce category mandatory layers
        from bench.builder.design import get_category_plugin
        cat = case_ir.get("identity", {}).get("category", "")
        plugin = get_category_plugin(cat)
        if plugin:
            category_required = plugin.derive_verifier_layers(case_ir)
            for req in category_required:
                if req not in selected_set:
                    raise VerifierPlanError(
                        f"Category '{cat}' requires layer '{req}'; cannot be omitted or dropped from verifier plan."
                    )

        rules: list[VerifierRule] = []
        root = case_ir.get("submission", {}).get("root", "final")

        # Mandatory V0: submission existence
        rules.append(
            VerifierRule(
                primitive="artifact_exists",
                target=root,
                layer="V0",
                params={"is_dir": True},
            )
        )

        # Mandatory V1: required artifacts existence
        for art in case_ir.get("submission", {}).get("artifacts", []):
            if art.get("required", True):
                rules.append(
                    VerifierRule(
                        primitive="artifact_exists",
                        target=f"{root}/{art['path']}",
                        layer="V1",
                    )
                )

        # Custom primitives
        for prim in verif.get("primitives", []):
            threshold_ref = prim.get("threshold_ref")
            params = dict(prim.get("params", {}))

            if "threshold" in params:
                raise VerifierPlanError(
                    f"Inline 'threshold' in primitive '{prim['primitive']}' is forbidden; "
                    "use 'threshold_ref' pointing to [verification.thresholds] as single source of truth."
                )

            rules.append(
                VerifierRule(
                    primitive=prim["primitive"],
                    target=prim.get("target", ""),
                    layer=prim.get("layer", "V4"),
                    threshold_ref=threshold_ref,
                    params=params,
                )
            )

        # For V2 if selected: add numeric check on results
        if "V2" in selected_set:
            rules.append(
                VerifierRule(
                    primitive="json_schema",
                    target=f"{root}/metrics.json" if any("metrics.json" in r.target for r in rules) else f"{root}/{case_ir.get('submission', {}).get('artifacts', [{}])[0].get('path', '')}",
                    layer="V2",
                    params={"schema": {"type": "object"}},
                )
            )

        return cls(
            case_id=case_id,
            layers=layer_decls,
            rules=rules,
            thresholds=thresholds,
        )
