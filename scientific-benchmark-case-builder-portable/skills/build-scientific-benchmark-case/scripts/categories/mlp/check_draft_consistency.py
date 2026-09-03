#!/usr/bin/env python3
"""Read-only cross-layer consistency checks for MLP Runnable Draft cases.

Two layers of checks run here:

1. System-scope checks (original): the canonical `public/system.json` set
   versus hidden scopes, public recipe mentions, and source hash locks.
2. Machine layer-consistency checks (MVP contract, WP3): submission-root
   agreement across task/design/verifier-plan, instruction paths versus the
   predicted packaged bundle, input-manifest candidate paths, held-out set
   ownership uniqueness, metric comparator agreement, capability-vs-label
   source honesty, and CONTRACT.md candidate visibility. Every machine check
   reads declared machine-readable fields; none parse free prose. Each is
   conditional on its inputs being present so partial drafts fail with a
   specific error instead of a crash.

Run with --json for the machine-readable verdict; exit 1 when any error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise SystemExit("PyYAML is required: python -m pip install pyyaml") from exc

DATASET_PATH_RE = re.compile(r"(?:train_dataset|datasets?)/([A-Za-z0-9._-]+)")
HIDDEN_BASES = (
    Path("reference/hidden-validation"),
    Path("tests/hidden"),
)
# Mirrors dftworld_bench.contracts.case._LEGACY_PUBLIC_RULE: manifests without
# explicit [candidate].files stage public/** with the prefix stripped.
LEGACY_PUBLIC_RULE = {"source": "public/**", "destination": ".", "strip_prefix": "public"}
VALIDATION_SET_OWNERS = {"candidate_generated", "verifier_hidden", "expert_calibration"}
METRIC_OPERATORS = {"<", "<=", ">", ">="}
# Label sources that are NOT DFT; claiming dft_dynamics with these is a
# capability overstatement (e.g. teacher-potential labeling written as DFT).
NON_DFT_LABEL_SOURCES = {"teacher_inference", "published_model", "self_generated"}
_PATH_TOKEN_RE = re.compile(r"`([A-Za-z0-9._@+-]+(?:/[A-Za-z0-9._@+-]+)+)`")
_COMPARATOR_RE = re.compile(r"(<=|>=|<|>)\s*(-?\d+(?:\.\d+)?)")


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root must be an object")
    return value


def _yaml(path: Path) -> dict[str, Any]:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root must be a mapping")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_canonical_systems(case_dir: Path) -> set[str]:
    path = case_dir / "public/system.json"
    if not path.is_file():
        raise ValueError("public/system.json missing")
    interfaces = _json(path).get("interfaces")
    if not isinstance(interfaces, dict) or not interfaces:
        raise ValueError("public/system.json#/interfaces must be a non-empty object")
    return {str(name) for name in interfaces}


def _manifest_scope(path: Path) -> tuple[set[str], list[str]]:
    data = _json(path)
    systems = data.get("systems")
    if isinstance(systems, list):
        declared = {str(name) for name in systems}
    else:
        declared = set()
    frames = data.get("frames") or []
    frame_systems: set[str] = set()
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        source = str(frame.get("source", ""))
        match = DATASET_PATH_RE.search(source)
        if match:
            frame_systems.add(match.group(1))
    errors: list[str] = []
    if declared and frame_systems and declared != frame_systems:
        errors.append(
            f"{path}: systems {sorted(declared)} disagree with frame sources {sorted(frame_systems)}"
        )
    count = data.get("total_held_out_frames")
    if isinstance(count, int) and count != len(frames):
        errors.append(f"{path}: total_held_out_frames={count} but frames={len(frames)}")
    return declared or frame_systems, errors


def _recipe_scope(public_dir: Path) -> set[str]:
    systems: set[str] = set()
    for path in public_dir.rglob("*"):
        if not path.is_file() or path.name == "system.json":
            continue
        if path.suffix.lower() not in {".py", ".json", ".yaml", ".yml", ".toml", ".md", ".txt"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        systems.update(DATASET_PATH_RE.findall(text))
    return systems


def _hash_entries(lock: dict[str, Any]) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for section in ("public_input", "hidden_validation"):
        files = (lock.get(section) or {}).get("files") or {}
        if not isinstance(files, dict):
            continue
        for rel, value in files.items():
            expected = value.get("sha256") if isinstance(value, dict) else value
            if isinstance(expected, str):
                entries.append((str(rel), expected))
    return entries


def _check_hash_lock(case_dir: Path) -> list[str]:
    lock_path = case_dir / "reference/source.lock.json"
    if not lock_path.is_file():
        return []
    errors: list[str] = []
    for rel, expected in _hash_entries(_json(lock_path)):
        path = case_dir / rel
        if not path.is_file():
            errors.append(f"{lock_path}: locked file missing: {rel}")
        else:
            actual = _sha256(path)
            if actual != expected:
                errors.append(
                    f"{lock_path}: hash mismatch {rel}: expected {expected}, got {actual}"
                )
    return errors


# --------------------------------------------------------------------------
# Machine layer-consistency helpers (shared with check_discovery_runnable)
# --------------------------------------------------------------------------

def _has_glob(source: str) -> bool:
    return any(ch in source for ch in "*?[")


def _mapped_dest(rel: str, rule: dict[str, Any], single_file: bool) -> str | None:
    """Mirror of dftworld_bench.core.packager._normalized_dest for static use."""
    prefix = rule.get("strip_prefix")
    stripped = rel
    if prefix:
        base = str(prefix).rstrip("/")
        if rel == base:
            stripped = ""
        elif rel.startswith(base + "/"):
            stripped = rel[len(base) + 1 :]
        else:
            return None
    dest = str(rule.get("destination", "."))
    if single_file:
        posix = PurePosixPath(dest)
    else:
        posix = PurePosixPath(dest) / PurePosixPath(stripped)
    if posix.is_absolute() or ".." in posix.parts:
        return None
    return posix.as_posix() if posix.as_posix() not in (".", "") else stripped or None


def read_candidate_contract(case_dir: Path) -> dict[str, Any] | None:
    """Parse [candidate]/[execution]/[hpc] from task.toml; None when absent."""
    path = case_dir / "task.toml"
    if not path.is_file():
        return None
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    candidate = raw.get("candidate") or {}
    rules = candidate.get("files") or [dict(LEGACY_PUBLIC_RULE)]
    return {
        "task_toml": raw,
        "rules": [r for r in rules if isinstance(r, dict)],
        "submission_root": str(candidate.get("submission_root", ".")),
        "instruction": str(candidate.get("instruction", "instruction.md")),
    }


def predict_bundle_paths(case_dir: Path, rules: list[dict[str, Any]]) -> set[str]:
    """Candidate-relative file paths the packager would stage (static mirror)."""
    staged: set[str] = set()
    for rule in rules:
        source = str(rule.get("source", ""))
        if not source:
            continue
        if _has_glob(source):
            matches = [m for m in case_dir.glob(source) if m.is_file()]
            for match in matches:
                rel = match.relative_to(case_dir).as_posix()
                dest = _mapped_dest(rel, rule, single_file=False)
                if dest:
                    staged.add(dest)
        else:
            src = case_dir / source
            if src.is_file():
                rel = src.relative_to(case_dir).as_posix()
                dest = _mapped_dest(rel, rule, single_file=True)
                if dest:
                    staged.add(dest)
            elif src.is_dir():
                for match in src.rglob("*"):
                    if not match.is_file():
                        continue
                    rel = match.relative_to(case_dir).as_posix()
                    dest = _mapped_dest(rel, rule, single_file=False)
                    if dest:
                        staged.add(dest)
    staged.add("instruction.md")
    return staged


def _path_exists_in_bundle(token: str, bundle: set[str]) -> bool:
    if PurePosixPath(token).is_absolute():
        return False
    return any(p == token or p.startswith(token + "/") for p in bundle)


def instruction_input_tokens(case_dir: Path) -> set[str]:
    path = case_dir / "instruction.md"
    if not path.is_file():
        return set()
    text = path.read_text(encoding="utf-8", errors="replace")
    tokens = set()
    for match in _PATH_TOKEN_RE.finditer(text):
        token = match.group(1)
        if token.startswith("/"):
            continue
        if token.split("/", 1)[0] in {"logs", "submission", "app", "tests"}:
            continue
        tokens.add(token)
    return tokens


def check_machine_layers(case_dir: Path) -> list[str]:
    """WP3 invariants; every check is conditional on its declared inputs."""
    errors: list[str] = []
    contract: dict[str, Any] | None = None
    try:
        contract = read_candidate_contract(case_dir)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        errors.append(f"task.toml: unreadable case manifest: {exc}")
    design_path = case_dir / "case-design.yaml"
    design: dict[str, Any] = {}
    if design_path.is_file():
        try:
            design = _yaml(design_path)
        except (OSError, ValueError) as exc:
            errors.append(f"case-design.yaml: unreadable: {exc}")
    plan: dict[str, Any] = {}
    plan_path = case_dir / "verifier-plan.yaml"
    if plan_path.is_file():
        try:
            plan = _yaml(plan_path)
        except (OSError, ValueError) as exc:
            errors.append(f"verifier-plan.yaml: unreadable: {exc}")

    # (A) One submission root across task/design/verifier-plan.
    roots: dict[str, str] = {}
    if contract is not None:
        roots["task.toml [candidate].submission_root"] = contract["submission_root"]
    design_root = (design.get("submission") or {}).get("root") if isinstance(design.get("submission"), dict) else None
    if design_root is not None:
        roots["case-design submission.root"] = str(design_root)
    if plan.get("submission_root") is not None:
        roots["verifier-plan submission_root"] = str(plan["submission_root"])
    normalized = {k: ("." if v in (".", "") else v.rstrip("/")) for k, v in roots.items()}
    if len(set(normalized.values())) > 1:
        errors.append(
            "submission root disagreement: "
            + "; ".join(f"{k}={v!r}" for k, v in sorted(normalized.items()))
        )

    bundle: set[str] | None = None
    if contract is not None:
        bundle = predict_bundle_paths(case_dir, contract["rules"])

        # (B) Instruction input paths must exist in the packaged bundle.
        tokens = instruction_input_tokens(case_dir)
        for token in sorted(tokens):
            if token.startswith("public/") and not _path_exists_in_bundle(token, bundle):
                errors.append(
                    f"instruction references {token!r} but the packaged bundle has no such path "
                    "(candidate sees bundle-relative paths)"
                )

        # (C) public/input-manifest.json candidate paths.
        input_manifest_path = case_dir / "public/input-manifest.json"
        if input_manifest_path.is_file():
            try:
                input_manifest = _json(input_manifest_path)
            except (OSError, ValueError) as exc:
                input_manifest = {}
                errors.append(f"public/input-manifest.json: unreadable: {exc}")
            raw_files = input_manifest.get("files") or []
            if isinstance(raw_files, dict):
                entries = [
                    {"candidate_path": name, **(value if isinstance(value, dict) else {})}
                    for name, value in raw_files.items()
                ]
            elif isinstance(raw_files, list):
                entries = raw_files
            else:
                entries = []
                errors.append("public/input-manifest.json: files must be a list or path map")
            for entry in entries:
                if not isinstance(entry, dict):
                    errors.append("public/input-manifest.json: files entries must be objects")
                    continue
                candidate_path = str(entry.get("candidate_path", ""))
                posix = PurePosixPath(candidate_path)
                if not candidate_path or posix.is_absolute() or ".." in posix.parts:
                    errors.append(
                        f"public/input-manifest.json: unsafe candidate_path {candidate_path!r}"
                    )
                    continue
                if entry.get("candidate_generated") is True:
                    continue
                if entry.get("candidate_visible") is False:
                    errors.append(
                        f"public/input-manifest.json: {candidate_path!r} listed as candidate input "
                        "but candidate_visible=false"
                    )
                    continue
                if not _path_exists_in_bundle(candidate_path, bundle):
                    errors.append(
                        f"public/input-manifest.json: candidate_path {candidate_path!r} is not a "
                        "path the candidate file rules package"
                    )
                case_source = entry.get("case_source")
                if case_source and not (case_dir / str(case_source)).exists():
                    errors.append(
                        f"public/input-manifest.json: case_source {case_source!r} missing in case tree"
                    )

        # (F) CONTRACT.md visibility must match actual staging.
        design_contract = design.get("contract") if isinstance(design.get("contract"), dict) else {}
        declared_visible = design_contract.get("candidate_visible")
        staged_contract = "CONTRACT.md" in bundle
        if declared_visible is True and not staged_contract:
            errors.append(
                "case-design contract.candidate_visible=true but [candidate].files does not "
                "stage CONTRACT.md into the bundle"
            )
        if declared_visible is False and staged_contract:
            errors.append(
                "CONTRACT.md is staged into the bundle but case-design declares "
                "contract.candidate_visible=false; remove the staging rule or the declaration"
            )

    # (D) Held-out validation set ownership is unique per name.
    owners: dict[str, set[str]] = {}
    for entry in design.get("validation_sets") or []:
        if not isinstance(entry, dict):
            errors.append("case-design validation_sets entries must be objects")
            continue
        name = str(entry.get("name", ""))
        owner = str(entry.get("owner", ""))
        if owner not in VALIDATION_SET_OWNERS:
            errors.append(
                f"case-design validation_sets {name!r}: owner {owner!r} not in "
                f"{sorted(VALIDATION_SET_OWNERS)}"
            )
            continue
        owners.setdefault(name, set()).add(owner)
    for name, owner_set in sorted(owners.items()):
        if len(owner_set) > 1:
            errors.append(
                f"case-design validation set {name!r} has multiple owners {sorted(owner_set)}; "
                "one authority per held-out set (candidate_generated | verifier_hidden | expert_calibration)"
            )

    # (E) Metric comparators agree between design and instruction prose.
    instruction_text = ""
    instruction_path = case_dir / "instruction.md"
    if instruction_path.is_file():
        instruction_text = instruction_path.read_text(encoding="utf-8", errors="replace")
    seen_comparators = _COMPARATOR_RE.findall(instruction_text)
    for entry in design.get("metric_contract") or []:
        if not isinstance(entry, dict):
            errors.append("case-design metric_contract entries must be objects")
            continue
        operator = str(entry.get("operator", ""))
        if operator not in METRIC_OPERATORS:
            errors.append(
                f"case-design metric_contract {entry.get('name')!r}: operator {operator!r} "
                f"must be one of {sorted(METRIC_OPERATORS)}"
            )
            continue
        threshold = entry.get("threshold")
        if not isinstance(threshold, (int, float)):
            continue
        rendered = f"{threshold:g}"
        for found_operator, found_value in seen_comparators:
            if found_value.rstrip("0").rstrip(".") != rendered.rstrip("0").rstrip("."):
                continue
            if found_operator != operator:
                errors.append(
                    f"metric comparator drift: {entry.get('name')!r} bound is {operator} "
                    f"{rendered} in case-design but instruction shows {found_operator} {found_value}"
                )

    # (G) Workflow capability versus label source.
    caps = design.get("workflow_capabilities") or []
    label_source = str(design.get("label_source", ""))
    if "dft_dynamics" in caps and label_source in NON_DFT_LABEL_SOURCES:
        errors.append(
            f"workflow capability drift: dft_dynamics claimed but labels come from "
            f"{label_source}; teacher-published labeling is not DFT dynamics"
        )

    return errors


def check_case(case_dir: Path) -> dict[str, Any]:
    case_dir = case_dir.resolve()
    errors: list[str] = []
    observed: dict[str, list[str]] = {}
    hash_errors: list[str] = []
    errors.extend(check_machine_layers(case_dir))

    try:
        canonical = load_canonical_systems(case_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "valid": False,
            "errors": sorted(set(errors)) + [str(exc)],
            "canonical_systems": [],
            "observed_scopes": {},
            "hash_mismatches": [],
        }

    for base_rel in HIDDEN_BASES:
        base = case_dir / base_rel
        hidden = base / "hidden-frames"
        if hidden.is_dir():
            dirs = {path.name for path in hidden.iterdir() if path.is_dir()}
            key = f"{base_rel.as_posix()}/hidden-frames"
            observed[key] = sorted(dirs)
            if dirs != canonical:
                errors.append(
                    f"hidden scope mismatch at {key}: expected {sorted(canonical)}, got {sorted(dirs)}"
                )
        manifest = base / "manifest.json"
        if manifest.is_file():
            try:
                scope, manifest_errors = _manifest_scope(manifest)
                observed[manifest.relative_to(case_dir).as_posix()] = sorted(scope)
                errors.extend(manifest_errors)
                if scope != canonical:
                    errors.append(
                        f"hidden manifest scope mismatch at {manifest.relative_to(case_dir)}: "
                        f"expected {sorted(canonical)}, got {sorted(scope)}"
                    )
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                errors.append(str(exc))

    recipe = _recipe_scope(case_dir / "public")
    observed["public_recipe_systems"] = sorted(recipe)
    extra_recipe = recipe - canonical
    if extra_recipe:
        errors.append(
            f"public recipe names systems absent from public/system.json: {sorted(extra_recipe)}"
        )

    try:
        hash_errors = _check_hash_lock(case_dir)
        errors.extend(hash_errors)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(str(exc))

    return {
        "valid": not errors,
        "errors": errors,
        "canonical_systems": sorted(canonical),
        "observed_scopes": observed,
        "hash_mismatches": hash_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = check_case(args.case_dir)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for error in result["errors"]:
            print(f"ERROR: {error}")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
