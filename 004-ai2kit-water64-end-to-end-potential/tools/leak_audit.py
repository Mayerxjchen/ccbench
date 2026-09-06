#!/usr/bin/env python3
"""Leak audit for the autonomous, coordinate-free 034 public surface."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
CASE = HERE.parent
ROOT = CASE.parent
PUBLIC = CASE / "public"
REF = CASE / "reference"
HIDDEN_STAGED = CASE / "tests" / "hidden"
HIDDEN_SRC = REF / "hidden-validation"
SKILLS = ROOT / "base-env-build" / "skills"
EXPECTED_PUBLIC = {"system.json"}

CONNECTION_TOKENS = (
    "ssh hpc",
    "module load",
    "/public/home",
    "<site-host-ip>",
    "cp2k/2024.3",
    "lammps/2022.6.23",
    "HPC_ENVIRONMENT.md",
)
SECRET_TOKENS = (
    "BEGIN OPENSSH PRIVATE KEY",
    "BEGIN RSA PRIVATE KEY",
    "PAGENT_HPC_KEY=",
    "IdentityFile ~/.ssh",
)
CASE_SPECIFIC_SKILL_TOKENS = (
    "034-ai2kit",
    "<site-host-ip>",
    "/public/home/<site-user>",
    "hpc-ref-01",
    "hpc-ref-02",
    "04d8df86",
    "ad080e39",
    "31298017",
    "27011653",
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _files_text(paths: list[Path]) -> str:
    chunks: list[str] = []
    for root in paths:
        if root.is_file():
            chunks.append(root.read_text(encoding="utf-8", errors="ignore"))
        elif root.is_dir():
            for path in sorted(root.rglob("*")):
                if path.is_file() and path.stat().st_size <= 2_000_000:
                    chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(chunks)


def check_public_isolation() -> tuple[bool, str]:
    actual = {p.name for p in PUBLIC.iterdir() if p.is_file()}
    coords = [p for p in PUBLIC.rglob("*") if p.suffix.lower() in {".xyz", ".pdb", ".cif", ".extxyz"}]
    ok = actual == EXPECTED_PUBLIC and not coords
    return ok, f"public_files={sorted(actual)} coordinate_file_count={len(coords)}"


def check_system_spec() -> tuple[bool, str]:
    try:
        data = json.loads((PUBLIC / "system.json").read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"system.json invalid: {exc}"
    expected = {
        "system": "liquid water",
        "molecules": {"H2O": 64},
        "atoms": 192,
        "cell_A": [12.4, 12.4, 12.4],
        "periodic": [True, True, True],
        "model_family": "DeePMD",
    }
    forbidden = {"positions", "coordinates", "xyz", "structure", "atoms_data"}
    ok = data == expected and forbidden.isdisjoint(data)
    return ok, "target specification exact; no coordinate payload" if ok else "system.json contract drift"


def check_prompt_fidelity() -> tuple[bool, str]:
    text = (CASE / "instruction.md").read_text(encoding="utf-8", errors="ignore")
    hits = [token for token in CONNECTION_TOKENS if token.lower() in text.lower()]
    ip_hits = re.findall(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)
    ok = not hits and not ip_hits and "generate every atomic coordinate" in text.lower()
    return ok, f"connection_recipe_hits={hits + ip_hits}"


def check_hidden_isolation() -> tuple[bool, str]:
    names = ("dft-validation.extxyz", "rdf-reference.json", "thresholds.json")
    missing = [name for name in names if not (HIDDEN_STAGED / name).is_file()]
    if missing:
        return False, f"hidden staged files missing: {missing}"
    source = HIDDEN_SRC / "dft-validation.extxyz"
    if not source.is_file() or sha256(source) != sha256(HIDDEN_STAGED / source.name):
        return False, "hidden validation staging does not match reference"
    public_hashes = {sha256(path) for path in PUBLIC.rglob("*") if path.is_file()}
    hidden_hashes = {sha256(path) for path in HIDDEN_STAGED.rglob("*") if path.is_file()}
    return public_hashes.isdisjoint(hidden_hashes), "hidden bytes absent from public surface"


def check_lock_integrity() -> tuple[bool, str]:
    lock = REF / "source.lock.json"
    try:
        data = json.loads(lock.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"source lock invalid: {exc}"
    files = data.get("public_input", {}).get("files", {})
    if set(files) != EXPECTED_PUBLIC:
        return False, f"source lock public files are stale: {sorted(files)}"
    record = files.get("system.json", {})
    if not isinstance(record, dict) or record.get("sha256") != sha256(PUBLIC / "system.json"):
        return False, "source lock system.json hash mismatch"
    return True, "source lock pins only system.json"


def check_dockerfile_copy() -> tuple[bool, str]:
    text = (CASE / "Dockerfile").read_text(encoding="utf-8")
    copies = [line.strip() for line in text.splitlines() if line.strip().upper().startswith(("COPY ", "ADD "))]
    ok = copies == ["COPY public/ /app/"]
    return ok, f"copy_directives={copies}"


def check_secret_boundary() -> tuple[bool, str]:
    visible = _files_text([PUBLIC, CASE / "instruction.md", CASE / "Dockerfile"])
    hits = [token for token in SECRET_TOKENS if token.lower() in visible.lower()]
    evidence_dir = CASE / "ablation" / "evidence" / "real-hpc-readiness" / "20260813-bootstrap-auth"
    evidence = _files_text([evidence_dir])
    connection_hits = [
        token for token in ("<site-host-ip>", "<site-user>", "id_rsa", "/public/home")
        if token.lower() in evidence.lower()
    ]
    ok = not hits and not connection_hits and "verdict=PASS" in evidence
    return ok, f"secret_hits={hits}; evidence_connection_hits={connection_hits}"


def check_skills_generic() -> tuple[bool, str]:
    text = _files_text([SKILLS])
    hits = [token for token in CASE_SPECIFIC_SKILL_TOKENS if token.lower() in text.lower()]
    skill_count = len(list(SKILLS.glob("*/SKILL.md")))
    return not hits, f"skill_count={skill_count} case_specific_hits={hits}"


CHECKS = [
    ("A1", "public isolation", check_public_isolation),
    ("A2", "coordinate-free target spec", check_system_spec),
    ("A3", "prompt connection-detail isolation", check_prompt_fidelity),
    ("A4", "hidden byte isolation", check_hidden_isolation),
    ("A5", "source lock integrity", check_lock_integrity),
    ("A6", "Docker build scope", check_dockerfile_copy),
    ("A7", "credential and evidence boundary", check_secret_boundary),
    ("A8", "generic skill boundary", check_skills_generic),
]


def main() -> int:
    print("034 autonomous PAgent leak audit")
    print("=" * 72)
    all_ok = True
    for code, name, check in CHECKS:
        ok, detail = check()
        all_ok = all_ok and ok
        print(f"[{'PASS' if ok else 'FAIL'}] {code} {name}: {detail}")
    print("=" * 72)
    print("OVERALL:", "PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
