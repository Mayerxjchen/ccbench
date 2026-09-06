"""Verifier plan model and validation for CCBench case builder."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class VerifierRule:
    """A single verification primitive check."""

    primitive: str
    target: str
    params: dict[str, Any] = field(default_factory=dict)
    weight: float = 1.0
    layer: str = "V1"


@dataclass
class VerifierPlan:
    """Full verifier specification to be compiled into executable verifier scripts."""

    case_id: str
    layers: list[str]
    rules: list[VerifierRule]
    thresholds: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "layers": self.layers,
            "thresholds": self.thresholds,
            "rules": [
                {
                    "primitive": r.primitive,
                    "target": r.target,
                    "params": r.params,
                    "weight": r.weight,
                    "layer": r.layer,
                }
                for r in self.rules
            ],
        }

    @classmethod
    def from_case_ir(cls, case_ir: dict[str, Any]) -> VerifierPlan:
        """Derive verifier plan from Case IR."""
        case_id = case_ir.get("identity", {}).get("case_id", "draft-case")
        verif = case_ir.get("verification", {})
        layers = verif.get("layers", ["V0", "V1", "V2", "V4"])
        thresholds = verif.get("thresholds", {})

        rules = []
        root = case_ir.get("submission", {}).get("root", "final")
        rules.append(
            VerifierRule(
                primitive="artifact_exists",
                target=root,
                layer="V0",
                params={"is_dir": True},
            )
        )

        for art in case_ir.get("submission", {}).get("artifacts", []):
            if art.get("required", True):
                rules.append(
                    VerifierRule(
                        primitive="artifact_exists",
                        target=f"{root}/{art['path']}",
                        layer="V1",
                    )
                )

        for prim in verif.get("primitives", []):
            rules.append(
                VerifierRule(
                    primitive=prim["primitive"],
                    target=prim.get("target", ""),
                    params=prim.get("params", {}),
                    layer=prim.get("layer", "V4"),
                )
            )

        return cls(
            case_id=case_id,
            layers=layers,
            rules=rules,
            thresholds=thresholds,
        )
