#!/usr/bin/env python3
"""Deterministic legacy -> infra v2 migration for case manifests.

Transforms ``task.toml`` in place (``--write``) or reports drift (``--check``)
for the case scope given by ``--scope`` (``local`` today; ``hpc`` lands in
Task 16).

Local scope migration, per case:

  * ``[execution] class = "local_sandbox"``  -- explicit, never inferred
  * ``[candidate]`` block: instruction, submission_root, legacy layout, plus
    ``max_agent_seconds`` only when the legacy agent budget is non-standard
    (the infra default is 600s; the MACE/XTB cases declare 1500s)
  * ``[runtime] requirements`` -- canonical scientific requirements derived
    from the case Dockerfile ``FROM`` base image (resolved to a concrete
    image by the runtime registry at build time)
  * ``[submission_contract]`` -- structural pre-Verifier gate: the collected
    submission must be non-empty (shape check only, never executes)
  * removes ``[agent]`` policy -- harness-owned after migration

Only ``task.toml`` is written.  Scientific instruction / public / reference /
threshold / test bytes are never touched.  The transform is a pure function of
the input bytes: the same manifest always migrates to the same output, and
``--check`` is clean after ``--write`` (idempotent).

Python 3.12+, standard library only (tomllib + argparse).
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

# Infra default agent budget (agents.py).  Cases that need more declare an
# explicit ``max_agent_seconds``; the 600s majority rely on the infra default.
STANDARD_AGENT_SECONDS = 600.0

# Case Dockerfile FROM base image -> canonical scientific requirements.
# ``dftworld-base`` is the empty runtime: a case built on it declares nothing.
_FROM_REQUIREMENTS: dict[str, tuple[str, ...] | None] = {
    "dftworld-base": None,
    "dftworld-base-cp2k": ("cp2k",),
    "dftworld-base-packmol": ("packmol",),
    "dftworld-base-packmol-src": ("packmol",),
    "dftworld-base-deepmd": ("deepmd-kit",),
    "dftworld-base-chem": ("ase-rdkit",),
    "dftworld-base-mace": ("mace",),
    "dftworld-base-xtb": ("xtb",),
}

# Case id -> migration scope.
_SCOPE_RANGES: dict[str, tuple[tuple[int, int], ...]] = {
    "local": ((1, 30), (35, 41)),
    "hpc": ((31, 34), (42, 42)),
}

# HPC case number -> canonical scientific runtime requirement.  Field order is
# ``name / implementation (registry family) / version`` per the case contract.
# The compute image is resolved by the runtime registry — never owned by the
# case and never read from the (legacy, non-authoritative) Dockerfile.
_HPC_RUNTIME: dict[int, tuple[str, str, str]] = {
    31: ("matclaw-cips", "matclaw-cips", "==2.2.11"),
    32: ("matclaw-cips", "matclaw-cips", "==2.2.11"),
    33: ("matclaw-cips", "matclaw-cips", "==2.2.11"),
    34: ("ai2kit", "ai2kit", "==1.1.0"),
    42: ("dpmp", "deepmd-jax", "==0.2.1"),
}

# HPC case number -> scientific capabilities.  ``required`` is fixed by the
# paper (e.g. CP2K); ``optional`` names heavy-science Discovery choices (e.g.
# GPU DPMP training).  Category plugins consume these; they are capability
# checks, never a second image-selection mechanism.
_HPC_SCOPES: dict[int, dict[str, list[str]]] = {
    31: {"required": ["matclaw-cips", "cp2k"], "optional": ["gpu-training"]},
    32: {"required": ["matclaw-cips", "cp2k"], "optional": ["gpu-training"]},
    33: {"required": ["matclaw-cips", "cp2k"], "optional": ["gpu-training"]},
    34: {"required": ["ai2kit", "cp2k"], "optional": ["gpu-training"]},
    42: {"required": ["cp2k"], "optional": ["gpu-training", "jax-md-gpu"]},
}


class MigrationError(RuntimeError):
    """The migration cannot proceed safely for a case."""


# --------------------------------------------------------------------------- #
# TOML rendering (deterministic; input ordering preserved per table)
# --------------------------------------------------------------------------- #

def _render_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    raise MigrationError(f"cannot render scalar TOML value {value!r}")


def _render_value(value: Any) -> str:
    if isinstance(value, dict):
        inner = ", ".join(
            f"{key} = {_render_scalar(value[key])}" for key in sorted(value)
        )
        return "{ " + inner + " }" if inner else "{}"
    if isinstance(value, list):
        if not value:
            return "[]"
        return "[" + ", ".join(_render_value(item) for item in value) + "]"
    return _render_scalar(value)


def _emit_section(lines: list[str], path: str, body: dict[str, Any]) -> None:
    """Append one table and any nested tables (``[parent.child]``) to lines."""
    block = [f"[{path}]"]
    nested: list[tuple[str, dict[str, Any]]] = []
    for key, value in body.items():
        if isinstance(value, dict):
            nested.append((f"{path}.{key}", value))
        else:
            block.append(f"{key} = {_render_value(value)}")
    lines.append("\n".join(block))
    for sub_path, sub_body in nested:
        _emit_section(lines, sub_path, sub_body)


def _emit_toml(out: dict[str, Any]) -> str:
    """Serialize a tomllib dict deterministically: scalar header, then tables
    (in document order), nested dicts as sub-tables, one blank line between
    sections.  The same input always yields the same output."""
    scalars: list[str] = []
    sections: list[tuple[str, dict[str, Any]]] = []
    for key, value in out.items():
        if isinstance(value, dict):
            sections.append((key, value))
        else:
            scalars.append(f"{key} = {_render_value(value)}")
    lines = list(scalars)
    for path, body in sections:
        _emit_section(lines, path, body)
    return "\n\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Transform
# --------------------------------------------------------------------------- #

def _base_image(case_dir: Path) -> str:
    dockerfile = case_dir / "Dockerfile"
    if not dockerfile.is_file():
        dockerfile = case_dir / "environment" / "Dockerfile"
    if not dockerfile.is_file():
        raise MigrationError(f"{case_dir.name}: no Dockerfile to derive runtime from")
    match = re.search(r"^\s*FROM\s+([^\s]+)", dockerfile.read_text(encoding="utf-8"), re.M)
    if match is None:
        raise MigrationError(f"{case_dir.name}: Dockerfile has no FROM line")
    return match.group(1)


def _transform_local(raw: dict[str, Any], case_dir: Path) -> dict[str, Any]:
    """Pure legacy -> v2 transform for one local case manifest dict."""
    out: dict[str, Any] = {}

    # Scalar header first, then the v2 identity block, then preserved tables.
    for key in ("schema_version", "artifacts"):
        if key in raw:
            out[key] = raw[key]

    out["execution"] = {"class": "local_sandbox"}

    agent = raw.get("agent") or {}
    infra_policy = sorted(set(agent) - {"timeout_sec"})
    if infra_policy:
        raise MigrationError(
            f"{case_dir.name}: [agent] carries infra-owned policy "
            f"{infra_policy}; refusing to drop it silently"
        )

    candidate = dict(raw.get("candidate") or {})
    candidate["instruction"] = str(candidate.get("instruction", "instruction.md"))
    candidate["submission_root"] = str(candidate.get("submission_root", "."))
    candidate["legacy_submission_layout"] = bool(
        candidate.get("legacy_submission_layout", True)
    )
    timeout = agent.get("timeout_sec")
    if isinstance(timeout, (int, float)) and float(timeout) != STANDARD_AGENT_SECONDS:
        candidate["max_agent_seconds"] = float(timeout)
    # Local cases own no concrete image; the harness resolves the runtime.
    candidate.pop("image", None)
    candidate.pop("files", None)
    out["candidate"] = candidate

    for key, value in raw.items():
        if key in ("schema_version", "artifacts", "execution", "candidate", "agent"):
            continue
        out[key] = value

    requirements = _FROM_REQUIREMENTS.get(_base_image(case_dir))
    if requirements is not None:
        out["runtime"] = {"requirements": [{"name": name} for name in requirements]}

    contract = dict(raw.get("submission_contract") or {})
    contract.setdefault("required", [])
    contract.setdefault("non_empty", True)
    out["submission_contract"] = contract

    return out


def _case_number(case_dir: Path) -> int:
    prefix = case_dir.name.split("-", 1)[0]
    try:
        return int(prefix)
    except ValueError as exc:
        raise MigrationError(f"{case_dir.name}: cannot read case number") from exc


def _transform_hpc(raw: dict[str, Any], case_dir: Path) -> dict[str, Any]:
    """Pure legacy -> v2 transform for one hpc_controller case manifest dict.

    Declares the canonical scientific runtime requirements and the per-paper
    ``scientific_capabilities``; drops infra-owned ``[agent]`` policy and the
    legacy ``candidate.image`` (image authority moves to the runtime registry).
    The case's own Dockerfile is never read or rewritten — it stays a
    byte-identical, non-authoritative legacy artifact until Task 12 Step 6.
    """
    number = _case_number(case_dir)
    if number not in _HPC_RUNTIME or number not in _HPC_SCOPES:
        raise MigrationError(
            f"{case_dir.name}: no Task 16 migration mapping for case {number}"
        )

    out: dict[str, Any] = {}
    for key in ("schema_version", "case_version", "artifacts"):
        if key in raw:
            out[key] = raw[key]

    out["execution"] = dict(raw.get("execution") or {})
    if out["execution"].get("class") != "hpc_controller":
        raise MigrationError(
            f"{case_dir.name}: hpc scope requires [execution] class = hpc_controller"
        )

    # Drop infra-owned agent policy (agent timeouts live in the infra profile
    # after migration, never in the case).
    agent = dict(raw.get("agent") or {})
    infra_policy = sorted(set(agent) - {"timeout_sec"})
    if infra_policy:
        raise MigrationError(
            f"{case_dir.name}: [agent] carries infra-owned policy "
            f"{infra_policy}; refusing to drop it silently"
        )
    if "timeout_sec" in agent:
        agent.pop("timeout_sec")
    if agent:
        out["agent"] = agent

    # Candidate block: keep instruction / submission_root / allowlist files;
    # drop the legacy concrete image — the runtime registry resolves it.
    candidate = dict(raw.get("candidate") or {})
    candidate.pop("image", None)
    candidate.setdefault("instruction", "instruction.md")
    candidate.setdefault("submission_root", ".")
    candidate.setdefault("legacy_submission_layout", True)
    if not candidate.get("files"):
        raise MigrationError(
            f"{case_dir.name}: [candidate] requires an allowlist [[files]] "
            f"(the packager stages the bundle from it)"
        )
    out["candidate"] = candidate

    # HPC block: keep contract + required_capabilities; add scientific
    # capabilities (idempotent).
    hpc = dict(raw.get("hpc") or {})
    hpc.setdefault("contract_version", "hpc-execution/v1")
    hpc.setdefault("required_capabilities", ["batch_jobs", "gpu", "artifact_fetch"])
    existing_sc = hpc.get("scientific_capabilities")
    if not isinstance(existing_sc, dict) or not existing_sc:
        hpc["scientific_capabilities"] = _HPC_SCOPES[number]
    out["hpc"] = hpc

    name, family, version = _HPC_RUNTIME[number]
    out["runtime"] = {
        "requirements": [{"name": name, "family": family, "version": version}]
    }

    # Preserve every other non-infra-owned table unchanged (environment,
    # solution.env, metadata, verifier, task, generated).
    for key, value in raw.items():
        if key in (
            "schema_version", "case_version", "artifacts",
            "execution", "candidate", "hpc", "runtime", "agent",
        ):
            continue
        out[key] = value

    return out


def _transform(raw: dict[str, Any], case_dir: Path, scope: str) -> dict[str, Any]:
    if scope == "local":
        return _transform_local(raw, case_dir)
    if scope == "hpc":
        return _transform_hpc(raw, case_dir)
    raise NotImplementedError(scope)


# --------------------------------------------------------------------------- #
# Discovery + CLI
# --------------------------------------------------------------------------- #

def discover_cases(scope: str) -> list[Path]:
    cases: list[Path] = []
    for start, end in _SCOPE_RANGES[scope]:
        for number in range(start, end + 1):
            prefix = f"{number:03d}-"
            matches = sorted(p for p in ROOT.iterdir() if p.name.startswith(prefix))
            if len(matches) != 1:
                raise MigrationError(f"expected exactly one {prefix}* case, got {matches}")
            cases.append(matches[0])
    return cases


def migrate(scope: str, write: bool) -> list[Path]:
    changed: list[Path] = []
    for case_dir in discover_cases(scope):
        toml_path = case_dir / "task.toml"
        if not toml_path.is_file():
            raise MigrationError(f"{case_dir.name}: no task.toml")
        raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
        out = _transform(raw, case_dir, scope)
        rendered = _emit_toml(out)
        if rendered == toml_path.read_text(encoding="utf-8"):
            continue
        changed.append(toml_path)
        if write:
            toml_path.write_text(rendered, encoding="utf-8")
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate case manifests to the infra v2 contract (deterministic)."
    )
    parser.add_argument("--scope", choices=sorted(_SCOPE_RANGES), default="local")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="report drift; exit 1 if any")
    mode.add_argument("--write", action="store_true", help="rewrite task.toml in place")
    parser.add_argument(
        "--paths-out",
        metavar="FILE",
        help="write migrated (or would-migrate) task.toml paths, one per line",
    )
    args = parser.parse_args(argv)

    changed = migrate(args.scope, write=args.write)
    for path in changed:
        print(f"{'WROTE' if args.write else 'DRIFT '} {path.relative_to(ROOT)}")
    if args.paths_out:
        Path(args.paths_out).write_text(
            "\n".join(str(p) for p in changed) + ("\n" if changed else ""),
            encoding="utf-8",
        )
    if changed and not args.write:
        print(f"{len(changed)} manifest(s) would change; run --write to migrate")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
