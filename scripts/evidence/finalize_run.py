#!/usr/bin/env python3
"""Two-phase evidence finalization with a recoverable state machine (plan Task 6).

One completed formal run is frozen, curated to the policy-resolved minimal set,
verified, bundled deterministically, stored in primary and replica CAS stores
with read-back verification, restored and re-verified, then sealed with a v2
manifest. Each step persists its result before advancing, so a crash resumes
from the last durable state without overwriting a sealed object.

States: FROZEN -> CURATED -> VERIFIED -> BUNDLED -> PRIMARY_VERIFIED ->
REPLICA_VERIFIED -> SEALED -> MANIFEST_COMMITTED. Before SEALED no Git-facing
manifest exists; after MANIFEST_COMMITTED the transaction refuses to re-run.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.evidence.bundle import build_bundle, extract_bundle
from scripts.evidence.curate_and_verify import (
    CurateError,
    stage_files,
    verify_curated,
)
from scripts.evidence.resolve_required_artifacts import (
    EvidenceFile,
    resolve_required_artifacts,
)
from scripts.evidence.store import EvidenceStore

STATES = ("FROZEN", "CURATED", "VERIFIED", "BUNDLED", "PRIMARY_VERIFIED",
          "REPLICA_VERIFIED", "SEALED", "MANIFEST_COMMITTED")
STATE_FILE = "finalize-state.json"


class SlurmVerifierRuntime:
    """Run the frozen case verifier via ``sbatch`` on a cluster compute node.

    The finalize transaction itself runs on the cluster login node (python3 +
    numpy + zstd, no ase); the frozen CPU verifier SIF can only ``apptainer
    exec`` on a compute node. Each ``run()`` renders a slurm script mirroring
    the 031 formal template's verifier invocation, submits it with ``sbatch
    --wait``, and reads ``verifier_report.json`` from the job's out dir. The
    submission, tests, and SIF all live on the cluster filesystem — no bytes
    cross the host<->cluster link.
    """

    def __init__(self, sif: str, tests_src: Path, apptainer: str,
                 scratch_base: str, partition: str = "gpu",
                 gres: str = "gpu:1", account: str | None = None,
                 cpus: int = 8, mem: str = "64G", time: str = "02:00:00",
                 python_bin: str = "/opt/matclaw/bin/python") -> None:
        self.sif = sif
        self.tests_src = Path(tests_src)
        self.apptainer = apptainer
        self.scratch_base = scratch_base
        self.partition = partition
        self.gres = gres
        self.account = account
        self.cpus = cpus
        self.mem = mem
        self.time = time
        self.python_bin = python_bin

    def _render_script(self, submission: Path, profile: str,
                       out_dir: Path) -> str:
        code = (
            "import json,sys; sys.path.insert(0,'/tests'); "
            "from verifier import verify; "
            f"r=verify(__import__('pathlib').Path('/app'),{profile!r}); "
            "json.dump(r, open('/out/verifier_report.json','w'), "
            "ensure_ascii=False); print('valid=', r['valid'])"
        )
        account_line = f"#SBATCH --account={self.account}\n" if self.account else ""
        gres_line = f"#SBATCH --gres={self.gres}\n" if self.gres else ""
        return (
            "#!/bin/bash\n"
            "# Frozen finalize verifier over a curated/restored submission.\n"
            f"#SBATCH --job-name=finalize-verify-{uuid.uuid4().hex[:8]}\n"
            f"#SBATCH --partition={self.partition}\n"
            f"{gres_line}"
            f"{account_line}"
            f"#SBATCH --cpus-per-task={self.cpus}\n"
            f"#SBATCH --mem={self.mem}\n"
            f"#SBATCH --time={self.time}\n"
            f"#SBATCH --output={out_dir}/slurm.out\n"
            f"#SBATCH --error={out_dir}/slurm.err\n"
            "set -euo pipefail\n"
            f"'{self.apptainer}' exec --containall "
            f"--bind '{submission}:/app:ro' --bind '{self.tests_src}:/tests:ro' "
            f"--bind '{out_dir}:/out:rw' '{self.sif}' {self.python_bin} "
            f"-c \"{code}\"\n"
        )

    def run(self, submission: Path, profile: str) -> dict[str, Any]:
        tag = uuid.uuid4().hex[:10]
        out_dir = Path(self.scratch_base) / f"finalize-verify-{tag}"
        out_dir.mkdir(parents=True, exist_ok=True)
        script = out_dir / "run.slurm"
        script.write_text(self._render_script(submission, profile, out_dir),
                          encoding="utf-8")
        proc = subprocess.run(
            ["sbatch", "--wait", "--parsable", str(script)],
            capture_output=True, text=True)
        if proc.returncode != 0:
            raise FinalizeError(
                f"sbatch failed (rc={proc.returncode}): {proc.stderr.strip()}")
        report_path = out_dir / "verifier_report.json"
        if not report_path.is_file():
            slurm_err = (out_dir / "slurm.err").read_text(encoding="utf-8") \
                if (out_dir / "slurm.err").is_file() else ""
            raise CurateError(
                f"verifier job {proc.stdout.strip()} produced no verifier_report.json "
                f"in {report_path}; slurm.err: {slurm_err[-2000:]}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        return report


class FinalizeError(RuntimeError):
    """The transaction cannot advance (injected failure or precondition)."""


class SealedObjectError(FinalizeError):
    """A sealed object or manifest already exists and must not be overwritten."""


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SifVerifierRuntime:
    """Run the frozen case verifier inside the CPU SIF on a Slurm host.

    Mirrors the v1 finalize command: rsync the case tests + submission to a
    scratch dir on the host, run ``apptainer exec`` with the submission mounted
    read-only at /app, fetch the verifier_report.json back.
    """

    def __init__(self, host: str, sif: str, tests_src: Path, apptainer: str,
                 scratch_base: str, python_bin: str = "/opt/matclaw/bin/python") -> None:
        self.host = host
        self.sif = sif
        self.tests_src = Path(tests_src)
        self.apptainer = apptainer
        self.scratch_base = scratch_base
        self.python_bin = python_bin

    def run(self, submission: Path, profile: str) -> dict[str, Any]:
        tag = uuid.uuid4().hex[:10]
        remote = f"{self.scratch_base}/verifier-{tag}"
        remote_sub = f"{remote}/submission"
        subprocess.run(
            ["ssh", "-o", "BatchMode=yes", self.host, f"mkdir -p '{remote_sub}'"],
            check=True)
        subprocess.run(
            ["rsync", "-a", f"{self.tests_src}/", f"{self.host}:{remote}/tests/"],
            check=True)
        subprocess.run(
            ["rsync", "-a", f"{submission}/", f"{self.host}:{remote_sub}/"],
            check=True)
        code = (
            f"import sys; sys.path.insert(0, '/tests'); import json, verifier; "
            f"r = verifier.verify('/app', {profile!r}); "
            f"json.dump(r, open('/out/verifier_report.json', 'w'), ensure_ascii=False); "
            f"print('valid=', r['valid'])"
        )
        subprocess.run(
            ["ssh", "-o", "BatchMode=yes", self.host,
             f"'{self.apptainer}' exec --containall "
             f"--bind '{remote_sub}:/app:ro' --bind '{remote}/tests:/tests:ro' "
             f"--bind '{remote}:/out:rw' '{self.sif}' {self.python_bin} -c \"{code}\""],
            check=True)
        report_path = Path(tempfile.mkdtemp()) / "verifier_report.json"
        subprocess.run(
            ["rsync", "-a", f"{self.host}:{remote}/verifier_report.json", str(report_path)],
            check=True)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        subprocess.run(["ssh", "-o", "BatchMode=yes", self.host, f"rm -rf '{remote}'"],
                       check=False)
        return report


@dataclass
class FinalizeTransaction:
    run_dir: Path
    workspace: Path
    case_dir: Path
    policy: dict[str, Any]
    verifier_runtime: Any
    primary_uri: str
    replica_uri: str
    manifest_args: dict[str, Any]
    profile: str = "paper"
    fail_after: str | None = None

    def __post_init__(self) -> None:
        self.run_dir = Path(self.run_dir).resolve()
        self.workspace = Path(self.workspace).resolve()
        self.case_dir = Path(self.case_dir).resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.store = EvidenceStore(self.primary_uri, self.replica_uri)
        self.state_path = self.run_dir / STATE_FILE
        self.state: dict[str, Any] = self._load_state()

    # -- state persistence ------------------------------------------------

    def _load_state(self) -> dict[str, Any]:
        if self.state_path.is_file():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if state.get("state") not in STATES:
                raise FinalizeError(f"unknown state {state.get('state')!r}")
            return state
        return {"state": "FROZEN"}

    def _persist(self, payload: dict[str, Any]) -> None:
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _advance(self, state: str, **payload: Any) -> None:
        self.state.update({"state": state, **payload})
        self._persist(self.state)
        if self.fail_after == state:
            raise FinalizeError(f"injected failure after {state}")

    def current(self) -> str:
        return self.state["state"]

    def _at_or_after(self, state: str) -> bool:
        return STATES.index(self.current()) >= STATES.index(state)

    # -- steps -------------------------------------------------------------

    def _freeze(self) -> None:
        if self.state.get("frozen"):
            return
        if not self.workspace.is_dir():
            raise FinalizeError(f"workspace missing: {self.workspace}")
        if not self.workspace.joinpath("result.json").is_file():
            raise FinalizeError(f"workspace has no result.json: {self.workspace}")
        self._advance("FROZEN", frozen=True, workspace=str(self.workspace),
                      case_dir=str(self.case_dir))

    def _curate(self) -> None:
        if self._at_or_after("CURATED"):
            return
        files = resolve_required_artifacts(self.workspace, self.policy)
        if not files:
            raise FinalizeError("policy resolved no artifacts")
        staging = stage_files(self.workspace, files, self.run_dir / "bundle-staging")
        self._advance("CURATED", staging=str(staging),
                      files=[{"path": f.path, "role": f.role,
                              "size_bytes": f.size_bytes, "sha256": f.sha256} for f in files])

    def _verify(self) -> None:
        if self._at_or_after("VERIFIED"):
            return
        staging = Path(self.state["staging"])
        case_id = self._case_id()
        try:
            original, curated, metrics = verify_curated(
                case_id, self.workspace, staging, self.verifier_runtime, self.profile)
        except CurateError as exc:
            raise FinalizeError(str(exc)) from exc
        self._advance("VERIFIED", original_report=original,
                      curated_report=curated, metrics=metrics)

    def _bundle(self) -> None:
        if self._at_or_after("BUNDLED"):
            return
        files = [EvidenceFile(**f) for f in self.state["files"]]
        staging = Path(self.state["staging"])
        bundle_path = self.run_dir / "bundle.tar.zst"
        descriptor = build_bundle([f.__dict__ for f in files], bundle_path,
                                  manifest=self._embedded_manifest(), base_dir=staging)
        self._advance("BUNDLED",
                      bundle={"sha256": descriptor.sha256,
                              "size_bytes": descriptor.size_bytes,
                              "zstd_version": descriptor.zstd_version,
                              "path": str(bundle_path)})

    def _store_primary(self) -> None:
        if self._at_or_after("PRIMARY_VERIFIED"):
            return
        bundle_path = Path(self.state["bundle"]["path"])
        digest = self.state["bundle"]["sha256"]
        data = bundle_path.read_bytes()
        obj = self.store.put_primary(data, digest)
        self._advance("PRIMARY_VERIFIED", primary_uri=obj.primary_uri,
                      primary_version=obj.primary_version)

    def _store_replica(self) -> None:
        if self._at_or_after("REPLICA_VERIFIED"):
            return
        bundle_path = Path(self.state["bundle"]["path"])
        digest = self.state["bundle"]["sha256"]
        data = bundle_path.read_bytes()
        obj = self.store.put_replica(data, digest)
        self._advance("REPLICA_VERIFIED", replica_uri=obj.replica_uri,
                      replica_version=obj.replica_version)

    def _seal(self) -> None:
        if self._at_or_after("SEALED"):
            return
        digest = self.state["bundle"]["sha256"]
        size = self.state["bundle"]["size_bytes"]
        restored = self.run_dir / "restored"
        if restored.exists():
            shutil.rmtree(restored)  # fresh restore — never merge into stale bytes
        extracted = extract_bundle(Path(self.state["bundle"]["path"]), restored,
                                   expected_sha256=digest)
        if {f["path"] for f in extracted} != {f["path"] for f in self.state["files"]}:
            raise FinalizeError("restored bundle does not match curated file set")
        case_id = self._case_id()
        try:
            _, resealed, metrics = verify_curated(
                case_id, restored, restored, self.verifier_runtime, self.profile)
        except CurateError as exc:
            raise FinalizeError(f"restored tree failed re-verification: {exc}") from exc
        self.store.verify(digest)  # both stores must independently return digest+size
        self._advance("SEALED", restored=str(restored), metrics=metrics,
                      sealed_sha256=digest, sealed_size_bytes=size)

    def _manifest(self) -> None:
        if self._at_or_after("MANIFEST_COMMITTED"):
            return
        restored = Path(self.state["restored"])
        files = self.state["files"]
        bundle = self.state["bundle"]
        digest = bundle["sha256"]
        files_listing = self.run_dir / "curated-files.json"
        files_listing.write_text(json.dumps(files), encoding="utf-8")
        bundle_listing = self.run_dir / "bundle-descriptor.json"
        bundle_listing.write_text(json.dumps({
            "format": "tar.zst",
            "sha256": digest,
            "size_bytes": bundle["size_bytes"],
            "primary_uri": self.state.get("primary_uri"),
            "primary_version": str(self.state.get("primary_version")),
            "replica_uri": self.state.get("replica_uri"),
            "replica_version": str(self.state.get("replica_version")),
            "verified_at": self.state.get("sealed_at") or _now_utc(),
        }), encoding="utf-8")

        from scripts.reference.write_evidence_manifest import main as write_manifest
        argv = [
            "--case-dir", str(self.case_dir),
            "--restored", str(restored),
            "--files-json", str(files_listing),
            "--verifier-report", str(self.run_dir / "curated-report.json"),
            "--bundle-json", str(bundle_listing),
        ]
        for key, value in self.manifest_args.items():
            if value is None:
                continue
            argv.extend([f"--{key.replace('_', '-')}", str(value)])
        report_path = self.run_dir / "curated-report.json"
        report_path.write_text(json.dumps(self.state.get("curated_report") or self.state.get("metrics")),
                               encoding="utf-8")
        rc = write_manifest(argv)
        if rc != 0:
            raise FinalizeError("manifest writer rejected the sealed evidence")
        self._advance("MANIFEST_COMMITTED",
                      sealed_at=_now_utc(), manifest=str(restored.parent / "manifest.json"))

    # -- driver -------------------------------------------------------------

    def _case_id(self) -> str:
        case_id = self.policy.get("case_id")
        if not case_id:
            raise FinalizeError("policy has no case_id")
        return str(case_id)

    def _embedded_manifest(self) -> dict[str, Any]:
        return {
            "schema_version": "2.0",
            "case": self._case_id(),
            "run_id": self.manifest_args.get("run_id"),
            "seed": int(self.manifest_args.get("seed", 0)),
            "profile": self.profile,
            "state": "curated",
        }

    def run(self) -> None:
        if self.current() == "MANIFEST_COMMITTED":
            raise SealedObjectError(
                f"{self.run_dir} is already MANIFEST_COMMITTED; sealed objects are immutable")
        self._freeze()
        self._curate()
        self._verify()
        self._bundle()
        self._store_primary()
        self._store_replica()
        self._seal()
        self._manifest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--workspace", required=True, type=Path)
    ap.add_argument("--case-dir", required=True, type=Path)
    ap.add_argument("--policy", required=True, type=Path)
    ap.add_argument("--primary", required=True)
    ap.add_argument("--replica", required=True)
    ap.add_argument("--fail-after", choices=STATES, default=None)
    # verifier runtime
    ap.add_argument("--verifier-host", default=None)
    ap.add_argument("--verifier-slurm", action="store_true",
                    help="submit verifier jobs via sbatch on this cluster (ER8 cluster-native)")
    ap.add_argument("--verifier-sif", default=None)
    ap.add_argument("--verifier-tests", default=None, type=Path)
    ap.add_argument("--verifier-apptainer", default=None)
    ap.add_argument("--verifier-scratch", default=None)
    ap.add_argument("--verifier-partition", default="gpu")
    ap.add_argument("--verifier-gres", default="gpu:1")
    ap.add_argument("--verifier-account", default=None)
    ap.add_argument("--verifier-cpus", default=8, type=int)
    ap.add_argument("--verifier-mem", default="64G")
    ap.add_argument("--verifier-time", default="02:00:00")
    # manifest identity fields
    ap.add_argument("--seed", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--git-commit", required=True)
    ap.add_argument("--git-clean", default="true")
    ap.add_argument("--gpu-image", required=True)
    ap.add_argument("--gpu-image-digest", required=True)
    ap.add_argument("--cpu-verifier-image", required=True)
    ap.add_argument("--cpu-verifier-image-digest", required=True)
    ap.add_argument("--workspace-identity", default=None)
    ap.add_argument("--started-at", default=None)
    ap.add_argument("--finished-at", default=None)
    ap.add_argument("--hardware-json", default="{}")
    ap.add_argument("--software-json", default="{}")
    ap.add_argument("--profile", default="paper")
    args = ap.parse_args(argv)

    policy = json.loads(args.policy.read_text(encoding="utf-8"))
    if args.verifier_slurm:
        runtime = SlurmVerifierRuntime(
            args.verifier_sif, args.verifier_tests, args.verifier_apptainer,
            args.verifier_scratch, partition=args.verifier_partition,
            gres=args.verifier_gres, account=args.verifier_account,
            cpus=args.verifier_cpus, mem=args.verifier_mem,
            time=args.verifier_time)
    elif args.verifier_host:
        runtime = SifVerifierRuntime(
            args.verifier_host, args.verifier_sif, args.verifier_tests,
            args.verifier_apptainer, args.verifier_scratch)
    else:
        from scripts.evidence.curate_and_verify import LocalVerifierRuntime
        runtime = LocalVerifierRuntime(args.case_dir)

    manifest_args = {
        "seed": args.seed, "run_id": args.run_id,
        "git_commit": args.git_commit, "git_clean": args.git_clean,
        "gpu_image": args.gpu_image, "gpu_image_digest": args.gpu_image_digest,
        "cpu_verifier_image": args.cpu_verifier_image,
        "cpu_verifier_image_digest": args.cpu_verifier_image_digest,
        "workspace_identity": args.workspace_identity,
        "started_at": args.started_at, "finished_at": args.finished_at,
        "hardware_json": args.hardware_json, "software_json": args.software_json,
    }
    tx = FinalizeTransaction(
        run_dir=args.run_dir, workspace=args.workspace, case_dir=args.case_dir,
        policy=policy, verifier_runtime=runtime,
        primary_uri=args.primary, replica_uri=args.replica,
        manifest_args=manifest_args, profile=args.profile,
        fail_after=args.fail_after,
    )
    tx.run()
    print(f"finalized {tx.current()} at {tx.run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
