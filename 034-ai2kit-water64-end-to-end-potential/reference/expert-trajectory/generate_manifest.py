#!/usr/bin/env python3
"""
Generate reference/expert-trajectory/manifest.json — per-artifact path/size/
sha256/provenance for the archived expert lineage (Acceptance Board A2).

Run: python3 reference/expert-trajectory/generate_manifest.py
Regenerates manifest.json from the tree on disk. Deterministic (sorted paths).
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent

PROVENANCE_BY_DIR = {
    "initial": "PACKMOL 64-H2O fill (data/packmol/); source of public water64.xyz",
    "geopt": "CP2K GEO_OPT of the initial structure (BLYP-D3/TZV2P-GTH); 394 steps, E=-1102.5766305742 Ha",
    "aimd": "CP2K AIMD NVT 300 K from the geopt-optimized structure; 214 raw frames -> 190-frame mother set",
    "active-learning": "ai2-kit CLL loop (DeepMD train -> LAMMPS explore -> model-deviation screen -> CP2K label -> retrain), 5 iterations",
    "validation": "dp-test / NVT / RDF of the final potential",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    artifacts = []
    for p in sorted(HERE.rglob("*")):
        if not p.is_file() or p == Path(__file__).resolve():
            continue
        rel = str(p.relative_to(HERE))
        top = rel.split("/", 1)[0]
        artifacts.append({
            "path": rel,
            "size": p.stat().st_size,
            "sha256": sha256(p),
            "provenance": PROVENANCE_BY_DIR.get(top, f"archived expert {top} artifact"),
        })
    manifest = {
        "benchmark_id": "034-ai2kit-water64-end-to-end-potential",
        "description": "Frozen expert-trajectory lineage (Acceptance Board A2). Every artifact: path, size, sha256, provenance.",
        "generated": datetime.now(timezone.utc).isoformat(),
        "artifacts": artifacts,
        "note": "The expert results in this tree are strong SCIENTIFIC evidence, not benchmark acceptance. Acceptance requires independent re-run + verification + freeze inside the real 034 container (see ../../VALIDATION.json).",
    }
    out = HERE / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2))
    print(f"wrote {out} ({len(artifacts)} artifacts)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
