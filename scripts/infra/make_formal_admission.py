#!/usr/bin/env python3
"""Create a coordinator-signed Formal admission for one exact run binding."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ed25519


def _payload(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--signing-key-hex", default=os.environ.get("BENCH_FORMAL_SIGNING_KEY", ""))
    p.add_argument("--key-id", default=os.environ.get("BENCH_FORMAL_KEY_ID", "coordinator-v1"))
    p.add_argument("--run-id", required=True)
    p.add_argument("--case", help="high-level mode: derive case/version/public bundle/profile bindings")
    p.add_argument("--config", type=Path, help="optional strict candidate config; only its digest is admitted")
    p.add_argument("--case-id"); p.add_argument("--case-version"); p.add_argument("--public-digest")
    p.add_argument("--agent-profile"); p.add_argument("--agent-profile-digest")
    p.add_argument("--model-id"); p.add_argument("--candidate-image")
    p.add_argument("--candidate-image-digest"); p.add_argument("--sidecar-image")
    p.add_argument("--sidecar-image-digest")
    p.add_argument("--candidate-qualification-receipt-digest")
    p.add_argument("--sidecar-qualification-receipt-digest")
    p.add_argument("--config-digest", default="profile-default")
    p.add_argument("--suite-digest", help="composite digest of an external suite manifest and its public cases")
    p.add_argument("--attempt", type=int, default=1); p.add_argument("--ttl-hours", type=float, default=72)
    args = p.parse_args(argv)
    if not args.signing_key_hex:
        p.error("provide --signing-key-hex or BENCH_FORMAL_SIGNING_KEY")
    if args.case:
        # This is the operator-friendly path.  Exporting to a disposable
        # public bundle computes the exact digest pilot will later bind; the
        # Candidate/sidecar image and receipt identities come only from the
        # already signed host qualification locks.
        try:
            from bench.config.profiles import load_infra_profiles, resolve_profile
            from bench.contracts.case import CaseSpec
            from bench.mvp import _has_suite_manifest, export_case, resolve_case
            from bench.pilot import _hydrate_formal_image_qualification
            case_dir = resolve_case(args.case)
            spec = CaseSpec.load(
                case_dir, strict_external=_has_suite_manifest(case_dir),
            )
            # External paper packs must bind the exact suite manifest and
            # public allowlists into admission.  Repository legacy cases do
            # not have a suite manifest and remain compatible.
            try:
                from bench.suite import inspect_suite
                case_dir_resolved = case_dir.resolve()
                for parent in (case_dir_resolved, *case_dir_resolved.parents):
                    suite_file = parent / "suite.toml"
                    if not suite_file.is_file() or suite_file.is_symlink():
                        continue
                    report = inspect_suite(parent)
                    row = next(
                        (item for item in report.get("cases", [])
                         if item.get("directory") == case_dir_resolved.name),
                        None,
                    )
                    if row is None or not report.get("manifest_digest"):
                        raise ValueError("case is not present in a valid suite manifest")
                    args.suite_digest = report["manifest_digest"]
                    break
            except ImportError:
                pass
            profiles = load_infra_profiles()
            profile_name = args.agent_profile or spec.agent_profile or "claude-mvp"
            profile = resolve_profile(profiles, "agents", profile_name)
            qualified = _hydrate_formal_image_qualification(profile)
            with tempfile.TemporaryDirectory(prefix="bench-admission-") as temp:
                bundle = Path(temp) / "public"
                payload = export_case(args.case, bundle, lock_path=Path(temp) / "operator-lock.json")
            args.case_id = spec.case_id
            args.case_version = spec.case_version
            args.public_digest = payload["public_digest"]
            args.agent_profile = profile_name
            args.agent_profile_digest = profiles.digest("agents", profile_name)
            args.model_id = qualified.get("model")
            args.candidate_image = qualified.get("image")
            args.candidate_image_digest = qualified.get("image_digest")
            args.sidecar_image = qualified.get("sidecar_image")
            args.sidecar_image_digest = qualified.get("sidecar_image_digest")
            args.candidate_qualification_receipt_digest = qualified.get("candidate_qualification_receipt_digest")
            args.sidecar_qualification_receipt_digest = qualified.get("sidecar_qualification_receipt_digest")
            if args.config:
                from bench.config.candidate import load_candidate_config
                args.config_digest = load_candidate_config(args.config)["digest"]
        except Exception as exc:
            p.error(f"cannot derive Formal admission bindings: {exc}")
    required = (
        "case_id", "case_version", "public_digest", "agent_profile",
        "agent_profile_digest", "model_id", "candidate_image",
        "candidate_image_digest", "sidecar_image", "sidecar_image_digest",
        "candidate_qualification_receipt_digest", "sidecar_qualification_receipt_digest",
    )
    missing = [name for name in required if not getattr(args, name)]
    if missing:
        p.error("missing admission bindings (use --case for automatic derivation): " + ", ".join(missing))
    if args.case and args.suite_digest is None:
        try:
            case_path = Path(args.case).expanduser().resolve()
            repo_cases = (Path(__file__).resolve().parents[2] / "cases").resolve()
            case_path.relative_to(repo_cases)
        except ValueError:
            p.error("external case requires --suite-digest or a valid parent suite.toml")
    now = datetime.now(timezone.utc)
    body = {
        "schema_version": "1", "run_id": args.run_id, "attempt": args.attempt,
        "case_id": args.case_id, "case_version": args.case_version,
        "public_digest": args.public_digest, "agent_profile": args.agent_profile,
        "agent_profile_digest": args.agent_profile_digest, "model_id": args.model_id,
        "candidate_image": args.candidate_image, "candidate_image_digest": args.candidate_image_digest,
        "sidecar_image": args.sidecar_image, "sidecar_image_digest": args.sidecar_image_digest,
        "candidate_qualification_receipt_digest": args.candidate_qualification_receipt_digest,
        "sidecar_qualification_receipt_digest": args.sidecar_qualification_receipt_digest,
        "config_digest": args.config_digest, "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=args.ttl_hours)).isoformat(),
        "resume_until": (now + timedelta(hours=args.ttl_hours)).isoformat(),
        "nonce": os.urandom(16).hex(),
    }
    if args.suite_digest is not None:
        body["suite_digest"] = args.suite_digest
    key = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(args.signing_key_hex))
    admission = dict(body)
    admission["signature"] = {"algorithm": "ed25519", "key_id": args.key_id,
                               "signature_hex": key.sign(_payload(body)).hex()}
    encoded = json.dumps(admission, indent=2, sort_keys=True) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding="utf-8")
    print(f"wrote {args.output} digest=sha256:{hashlib.sha256(json.dumps(admission, sort_keys=True, separators=( ',', ':' )).encode()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
