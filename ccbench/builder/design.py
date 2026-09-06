"""Case IR loading, validation, and serialization."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from ccbench.paths import SCHEMAS_DIR


class CaseIRValidationError(ValueError):
    """Raised when Case IR fails schema or semantic validation."""


def load_case_ir_schema() -> dict[str, Any]:
    """Load the canonical Case IR json schema."""
    schema_path = SCHEMAS_DIR / "case-ir.schema.json"
    if not schema_path.is_file():
        raise FileNotFoundError(f"Case IR schema not found at {schema_path}")
    return json.loads(schema_path.read_text(encoding="utf-8"))


def validate_case_ir(doc: dict[str, Any]) -> None:
    """Validate a Case IR document against schemas/case-ir.schema.json."""
    schema = load_case_ir_schema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = list(validator.iter_errors(doc))
    if errors:
        msg = "; ".join(e.message for e in errors)
        raise CaseIRValidationError(f"Case IR schema validation failed: {msg}")

    # Semantic cross-checks
    sel = doc.get("selection", {})
    if sel.get("paradigm") == "research_question":
        if not sel.get("hypothesis"):
            raise CaseIRValidationError(
                "Research question cases must define selection.hypothesis"
            )


def load_case_ir(path: Path) -> dict[str, Any]:
    """Load and validate Case IR from YAML or JSON file."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Case IR file not found: {path}")

    raw_text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        data = yaml.safe_load(raw_text)
    else:
        data = json.loads(raw_text)

    if not isinstance(data, dict):
        raise CaseIRValidationError("Case IR must be a mapping/dict at root")

    validate_case_ir(data)
    return data
