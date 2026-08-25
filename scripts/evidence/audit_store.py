#!/usr/bin/env python3
"""Store audit: check every Git-tracked v2 manifest against both CAS stores (plan Task 9).

For each formal run manifest with a ``bundle`` descriptor, the audit verifies the
object is present with the right digest and size in *both* stores:

- both present + digest/size correct -> ``complete``
- exactly one present              -> ``degraded`` (never "valid redundancy")
- neither present                 -> ``unavailable`` (blocks benchmark validity)
- bytes present but wrong digest   -> ``corrupt``

``cas+file://`` stores normally live on the cluster; ``--via-ssh HOST`` checks the
objects remotely over ``ssh sha256sum`` so the Git-tracked host manifests can be
audited against the live cluster stores without copying bytes.

Usage::

    python scripts/evidence/audit_store.py --all-manifests [--via-ssh <site-alias>]
    python scripts/evidence/audit_store.py --case 031 --via-ssh <site-alias>

Exit 0 iff every audited run is ``complete``. JSON report on stdout (fit for Run
Record attachment).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse, unquote

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EVIDENCE_ROOT = ROOT / "evidence" / "matclaw" / "formal"


def _object_path_from_uri(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "cas+file":
        raise ValueError(f"unsupported store scheme: {parsed.scheme!r}")
    return Path(unquote(parsed.path))


def _check_object(path: Path, digest: str, size: int, via_ssh: str | None) -> dict:
    """Return {ok, present, sha256_ok, size_ok, error, actual_digest} for one object.

    ``present`` distinguishes "missing" (no bytes) from "corrupt" (bytes there,
    wrong digest) — both have ``ok=False`` but different operational meaning.
    """
    if via_ssh:
        try:
            proc = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", via_ssh,
                 f"sha256sum '{path}' ; stat -c %s '{path}'"],
                capture_output=True, text=True, timeout=600)
        except (subprocess.SubprocessError, OSError) as exc:
            return {"ok": False, "present": False, "sha256_ok": False, "size_ok": False,
                    "error": f"ssh check failed: {exc}", "actual_digest": None}
        if proc.returncode != 0:
            return {"ok": False, "present": False, "sha256_ok": False, "size_ok": False,
                    "error": (proc.stderr or proc.stdout).strip() or "object missing",
                    "actual_digest": None}
        try:
            digest_line, size_line = proc.stdout.strip().splitlines()[:2]
            actual_digest = digest_line.split()[0]
            actual_size = int(size_line)
        except (ValueError, IndexError):
            return {"ok": False, "present": False, "sha256_ok": False, "size_ok": False,
                    "error": f"unparseable ssh output: {proc.stdout.strip()!r}",
                    "actual_digest": None}
    else:
        if not path.is_file():
            return {"ok": False, "present": False, "sha256_ok": False, "size_ok": False,
                    "error": "object missing", "actual_digest": None}
        actual_size = path.stat().st_size
        actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()

    sha_ok = actual_digest == digest
    size_ok = actual_size == size
    errors = []
    if not sha_ok:
        errors.append(f"sha256 mismatch: object hashes to {actual_digest[:16]}…, want {digest[:16]}…")
    if not size_ok:
        errors.append(f"size mismatch: {actual_size} != {size}")
    return {"ok": sha_ok and size_ok, "present": True,
            "sha256_ok": sha_ok, "size_ok": size_ok,
            "error": "; ".join(errors) or None, "actual_digest": actual_digest}


def audit_store(evidence_root: Path = DEFAULT_EVIDENCE_ROOT,
                case_ids: list[str] | None = None,
                via_ssh: str | None = None) -> dict:
    """Audit every tracked v2 manifest's bundle against both stores."""
    cases = {}
    any_degraded = any_unavailable = False
    for manifest in sorted(evidence_root.glob("*/run-*/manifest.json")):
        case_id = manifest.parts[-3]
        if case_ids and case_id not in case_ids:
            continue
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("schema_version") != "2.0":
            continue
        bundle = data.get("bundle")
        if not bundle or not bundle.get("sha256"):
            continue  # not a sealed v2 run (e.g. 033) — nothing to audit
        digest, size = bundle["sha256"], int(bundle["size_bytes"])
        pri = _check_object(_object_path_from_uri(bundle["primary_uri"]),
                            digest, size, via_ssh)
        rep = _check_object(_object_path_from_uri(bundle["replica_uri"]),
                            digest, size, via_ssh)
        corrupt = (pri["present"] and not pri["sha256_ok"]) or \
                  (rep["present"] and not rep["sha256_ok"])
        if pri["ok"] and rep["ok"]:
            status = "complete"
        elif corrupt:
            status = "corrupt"          # bytes present but wrong in at least one store
        elif pri["ok"] or rep["ok"]:
            status = "degraded"
        else:
            status = "unavailable"
        if status == "degraded":
            any_degraded = True
        if status in ("unavailable", "corrupt"):
            any_unavailable = True
        cases.setdefault(case_id, {"runs": {}})
        cases[case_id]["runs"][manifest.parts[-2]] = {
            "status": status,
            "primary_ok": pri["ok"], "replica_ok": rep["ok"],
            "primary_sha256_ok": pri["sha256_ok"], "replica_sha256_ok": rep["sha256_ok"],
            "sha256_ok": pri["sha256_ok"] and rep["sha256_ok"],
            "bundle_sha256": digest, "size_bytes": size,
            "errors": [e for e in (pri["error"], rep["error"]) if e],
        }
    return {
        "cases": cases,
        "any_degraded": any_degraded,
        "any_unavailable": any_unavailable,
        "blocks_benchmark_valid": any_unavailable,
        "via_ssh": via_ssh,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--all-manifests", action="store_true")
    group.add_argument("--case", action="append", default=[])
    ap.add_argument("--evidence-root", type=Path, default=DEFAULT_EVIDENCE_ROOT)
    ap.add_argument("--via-ssh", default=None,
                    help="check objects on a remote host (stores are cluster-side)")
    args = ap.parse_args(argv)

    report = audit_store(args.evidence_root,
                         case_ids=args.case or None, via_ssh=args.via_ssh)
    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if (not report["any_degraded"] and not report["any_unavailable"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
