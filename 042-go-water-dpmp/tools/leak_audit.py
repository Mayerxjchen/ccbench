#!/usr/bin/env python3
"""Case 042 leak audit — the candidate-visible surface must contain no hidden
reference material.

Scans public/ (the only subtree COPYed into /app) and the instruction, and
flags any leak of: labeled training frames (DeepMD set.000 / energy.npy /
force.npy), the trained model, threshold bounds, hidden hashes, cluster/site
recipes, or expert trajectory paths.

Exit 0 on PASS, 1 on FAIL.
"""
from __future__ import annotations

import sys
from pathlib import Path

CASE = Path(__file__).resolve().parents[1]
PUBLIC = CASE / "public"

ALLOWED_PUBLIC_TOP = {
    "README.md", "system.json", "reference-method.json", "dpmp-config", "structures"
}
ALLOWED_STRUCTURE_IFACES = {
    "air-water", "graphene-water", "graphene-O12", "graphene-O25", "graphene-O50"
}

# text signals that must NOT appear anywhere in the agent-visible surface
FORBIDDEN_SIGNALS = [
    "14140", "1412", "threshold",
    "energy_rmse", "force_rmse", "first_peak_position", "depletion_width",
    "sbatch", "squeue", "sacct", "scancel", "sinfo", "slurm", "account",
    "partition", "dftworld-base-deepmd-jax", "AI2KIT_042", "/tests/hidden",
]


def scan(path: Path, hits: list[str]) -> None:
    if not path.is_file():
        return
    if path.suffix in {".npy", ".pb", ".pkl"}:
        hits.append(f"binary leak candidate: {path.relative_to(CASE)}")
        return
    if path.suffix == ".raw":
        return  # raw coordinate/type data (initial structures), not keyword-scanned
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return
    low = text.lower()
    for sig in FORBIDDEN_SIGNALS:
        if sig.lower() in low:
            hits.append(f"signal '{sig}' in {path.relative_to(CASE)}")


def main() -> int:
    hits: list[str] = []

    # 1. public/ top-level whitelist
    for p in PUBLIC.iterdir():
        if p.name not in ALLOWED_PUBLIC_TOP:
            hits.append(f"unexpected public top-level entry: {p.name}")

    # 2. structures/ layout + content
    struct = PUBLIC / "structures"
    if struct.is_dir():
        for iface in struct.iterdir():
            if not iface.is_dir():
                hits.append(f"unexpected structures entry: {iface.name}")
                continue
            if iface.name not in ALLOWED_STRUCTURE_IFACES:
                hits.append(f"unexpected interface dir: {iface.name}")
            for f in iface.iterdir():
                if f.name not in {"box.raw", "coord.raw", "type.raw"}:
                    hits.append(f"unexpected structure file: {f.relative_to(CASE)}")

    # 3. no DeepMD raw set / labeled data anywhere under public/
    for p in PUBLIC.rglob("*"):
        if p.name in {"energy.npy", "force.npy"} or (p.is_dir() and p.name.startswith("set.")):
            hits.append(f"labeled-data leak: {p.relative_to(CASE)}")

    # 4. text scan of every public file + instruction
    for p in PUBLIC.rglob("*"):
        scan(p, hits)
    scan(CASE / "instruction.md", hits)

    # 5. confirm no sibling hidden dirs are copied by the Dockerfile
    dockerfile = (CASE / "Dockerfile").read_text(encoding="utf-8", errors="ignore")
    if "reference" in dockerfile or "solution" in dockerfile or "tests" in dockerfile:
        hits.append("Dockerfile COPY leaks a hidden subtree")

    if hits:
        print("LEAK AUDIT FAIL")
        for h in sorted(set(hits)):
            print(f"  - {h}")
        return 1
    print("LEAK AUDIT PASS: public surface contains no hidden reference material")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
