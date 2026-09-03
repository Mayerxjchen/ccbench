#!/usr/bin/env python3
"""Small outcome/provenance verifier used for Runnable-Draft diagnostics.

It intentionally does not score expert paths: only the public system identity,
manifest outcomes, traceable stage lineage, and safe relative paths are checked.
Scientific E/F values are recomputed by the future hidden verifier from hidden
labels; this tool only checks the candidate's machine-readable contract.
"""
from __future__ import annotations
import argparse, hashlib, json, math
from pathlib import Path

EXPECTED = {"Cu": 9, "In": 9, "P": 18, "S": 54}
REQUIRED_STAGES = {"train", "explore", "select", "label", "grow", "retrain"}

def fail(errors, msg): errors.append(msg)

def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for b in iter(lambda: f.read(1 << 16), b""): h.update(b)
    return h.hexdigest()

def check(root: Path) -> dict:
    errors = []
    final = root / "final"
    manifest_path = final / "manifest.json"
    if not manifest_path.is_file():
        return {"valid": False, "errors": ["final/manifest.json is missing"]}
    try: manifest = json.loads(manifest_path.read_text())
    except Exception as exc: return {"valid": False, "errors": [f"manifest is not JSON: {exc}"]}
    if manifest.get("schema_version") != 1: fail(errors, "manifest schema_version must be 1")
    if manifest.get("model_family") != "DeePMD": fail(errors, "model_family must be DeePMD")
    target = manifest.get("target_system") or {}
    if target.get("system_id") != "cips_bulk_3x3x1": fail(errors, "wrong or missing target system identity")
    if target.get("composition") != EXPECTED: fail(errors, "composition is not Cu9In9P18S54")
    if target.get("type_map") != ["Cu", "In", "P", "S"]: fail(errors, "element order/type_map mismatch")
    outputs = manifest.get("outputs") or {}
    model_files = outputs.get("model_files") or []
    if not model_files: fail(errors, "outputs.model_files is empty")
    for rel in model_files:
        p = final / str(rel)
        try: p.relative_to(final)
        except ValueError: fail(errors, f"model path escapes final/: {rel}"); continue
        if not p.is_file() or p.stat().st_size == 0: fail(errors, f"model file missing/empty: {rel}")
    stages = manifest.get("lineage", {}).get("stages") or []
    stage_names = {str(s.get("stage", "")).lower() for s in stages if isinstance(s, dict)}
    if not REQUIRED_STAGES.issubset(stage_names): fail(errors, f"closed-loop stages missing: {sorted(REQUIRED_STAGES-stage_names)}")
    held = manifest.get("splits", {}).get("held_out") or {}
    if held.get("trajectory_id") in (None, "") or held.get("training_overlap") is not False:
        fail(errors, "held-out trajectory identity/negative overlap claim missing")
    metrics = manifest.get("metrics") or {}
    for key in ("force_mae_eV_per_angstrom", "force_rmse_eV_per_angstrom", "energy_mae_eV_per_atom"):
        value = metrics.get(key)
        if not isinstance(value, (int, float)) or not math.isfinite(value): fail(errors, f"metric missing/nonfinite: {key}")
    rounds = manifest.get("active_iterations")
    if not isinstance(rounds, int) or rounds < 1: fail(errors, "active_iterations must be >= 1")
    if not isinstance(manifest.get("jobs"), list) or not manifest["jobs"]: fail(errors, "job receipts missing")
    for forbidden in ("reference", "hidden", "solution", "tests", ".git"):
        if forbidden in json.dumps(manifest).lower(): fail(errors, f"manifest leaks forbidden token: {forbidden}")
    return {"valid": not errors, "errors": errors, "checked_models": len(model_files), "checked_stages": sorted(stage_names)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--submission", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.dry_run:
        print(json.dumps({"valid": True, "mode": "dry-run", "checks": ["identity", "safe paths", "model existence", "closed lineage", "split declaration", "finite metrics"]}, indent=2)); return 0
    if not args.submission: ap.error("--submission is required unless --dry-run")
    out = check(args.submission.resolve()); print(json.dumps(out, indent=2, sort_keys=True)); return 0 if out["valid"] else 1
if __name__ == "__main__": raise SystemExit(main())
