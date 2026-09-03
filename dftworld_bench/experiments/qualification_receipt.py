"""Dispatcher qualification receipt: contract, builder, derivation verifier.

The D11 receipt binds a real-site dispatcher canary to independently
recomputable anchors.  Design rule (fail-closed): **the receipt carries no
verdict fields**.  ``qualification_status``, ``formal_qualified`` and any
per-gate labels do not exist in the document — they are derived by
:func:`verify_receipt` from evidence primitives, each anchored offline:

- runtime/SIF identity      -> on-disk compute-runtime lock file
- site identity             -> SiteProfile digest rebuilt from the private
                               ``cluster_profile.toml``
- code identity             -> sha256 over every trusted module on the path,
                               recomputed under ``root``
- provenance                -> git source commit recorded at production time
- operation/attempt/job IDs -> replayed against the run's hash-chained
                               ``audit.jsonl`` (GatewayAudit)
- terminal state/exit code  -> raw ``sacct`` accounting embedded per job
- ReqTRES/AllocTRES         -> re-parsed through :mod:`dftworld_bench.hpc.tres`
- containment/GPU probes    -> re-parsed from the embedded ``stdout_tail``
                               (BENCH_PROBE protocol) and cross-checked against
                               the structured probe results
- settlement                -> digest recomputed from the embedded report
- fetch manifest            -> artifact bytes re-hashed from disk
- CP2K ENERGY gate          -> a distinct runtime lock (own SIF + pinned
                               binary/version), the same full job-record
                               derivation, the declared input bound both by
                               self-hash and to the staged artifact bytes, and
                               an independent re-parse of the manifest-anchored
                               ``cp2k.out`` vs the claimed version / total
                               energy / SCF convergence (:func:`parse_cp2k_output`)

Derivation ladder (capability matrix, revised 2026-09-03): derivation is
PER-JOB — :func:`_derive_job` returns its own gate buckets, so a broken cpu
canary never fails ``dispatcher.gpu`` (and vice versa); the shared anchors
(schema, content digest, code identity, SiteProfile rebuild) are an explicit
overlay applied equally to every capability; ``runtime.cp2k`` /
``runtime.ai2kit`` / ``dispatcher.cancel`` derive from their own optional
evidence blocks.
Aggregate: INVALID if any capability FAILs (or the overlay is broken);
PARTIAL if a runtime canary is NOT_RUN — the legacy meaning is restored, so
an old consumer never misreads "CP2K not run" as full PASS; PASS only when
every capability PASSes.  A case gates on its own
``[hpc.qualification].requires`` via :func:`case_requirements_satisfied`,
never on the site-wide aggregate.  The verdict is always computed here —
never read from the receipt.

Offline bound (documented honestly): scheduler-side facts cannot be re-queried
at verification time; they are bound via strictly-parsed raw accounting lines
plus internal cross-consistency with the append-only ledger.  Tampering with
any single field breaks at least one anchor even after re-hashing the receipt.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

SCHEMA_ID = (
    "https://mlip-bench.example/schemas/"
    "dispatcher-qualification-receipt.schema.json"
)
RECEIPT_KIND = "hpc-dispatcher-qualification/site-v1"

# Trusted modules whose bytes are pinned into every receipt.
CODE_IDENTITY_PATHS: tuple[str, ...] = (
    "scripts/infra/qualify_hpc_dispatcher.py",
    "dftworld_bench/experiments/qualification_receipt.py",
    "dftworld_bench/hpc/dispatcher.py",
    "dftworld_bench/hpc/gateway.py",
    "dftworld_bench/hpc/gateway_runtime.py",
    "dftworld_bench/hpc/adapters/slurm.py",
    "dftworld_bench/hpc/adapters/base.py",
    "dftworld_bench/hpc/site_profile.py",
    "dftworld_bench/hpc/tres.py",
    "dftworld_bench/hpc/audit.py",
    "schemas/dispatcher-qualification-receipt.schema.json",
)

DEFAULT_PROFILE_RELPATH = "scripts/hpc/cluster_profile.toml"

# Capability namespace (spec §3): every name the verifier derives.
# dispatcher.cpu / dispatcher.gpu / runtime.matclaw-gpu ride the two canary
# jobs (cpu-class + gpu-class); dispatcher.cancel rides the explicit cancel
# probe (P4 step 7, merged by --phase cancel); runtime.cp2k / runtime.ai2kit
# derive from their own optional evidence blocks — absent evidence is NOT_RUN.
CAPABILITY_NAMESPACE: tuple[str, ...] = (
    "dispatcher.cpu",
    "dispatcher.gpu",
    "dispatcher.cancel",
    "runtime.matclaw-gpu",
    "runtime.ai2kit",
    "runtime.cp2k",
)

# BENCH_PROBE stdout protocol (emitted by the canary command, parsed both by
# the trusted producer at collection time and independently by the verifier).
PROBE_PREFIX = "BENCH_PROBE "
GPU_PREFIX = "BENCH_GPU_DEVICE "
MARKER_CONTAINMENT = "CONTAINMENT_OK"
MARKER_GPU = "GPU_DEVICE_OK"

_PROBE_KEYS = (
    "workspace_rw",
    "home_sentinel_absent",
    "credential_sentinel_absent",
    "other_run_dir_absent",
    "solution_absent",
    "reference_absent",
    "runs_root_not_listable",
)

# Deterministic minimal ENERGY canary input: one water molecule,
# PBE/SZV-MOLOPT-GTH, 400 Ry, MT Poisson (non-periodic).  Structure mirrors
# the proven working inputs (019-cp2k-eps-scf/environment/H2O.inp): inline
# &XC_FUNCTIONAL PBE, explicit BASIS_SET_FILE_NAME/POTENTIAL_FILE_NAME
# (resolved via the image's baked-in CP2K_DATA_DIR -- --cleanenv strips host
# env but not image ENV), and the correct GTH-PBE-q1 potential for hydrogen.
# Its exact bytes are hashed into the receipt (input.sha256) and must equal
# the staged artifact bytes, so this text is a frozen part of the contract
# once a receipt exists.  Smoke-validated in the locked CP2K runtime SIF
# (cp2k.psmp, 2025.2 — the ai2kit-stack base image; reference/runtime).
CP2K_INPUT_NAME = "cp2k-energy.inp"
CP2K_OUTPUT_NAME = "cp2k.out"
CP2K_INPUT_TEXT = """\
&GLOBAL
  PROJECT cp2k-energy-canary
  RUN_TYPE ENERGY
  PRINT_LEVEL MEDIUM
&END GLOBAL
&FORCE_EVAL
  METHOD Quickstep
  &DFT
    BASIS_SET_FILE_NAME BASIS_MOLOPT
    POTENTIAL_FILE_NAME GTH_POTENTIALS
    &QS
      METHOD GPW
      EPS_DEFAULT 1.0E-10
    &END QS
    &MGRID
      CUTOFF 400
      REL_CUTOFF 40
    &END MGRID
    &SCF
      SCF_GUESS ATOMIC
      EPS_SCF 1.0E-6
      MAX_SCF 50
      &OT
        MINIMIZER DIIS
        PRECONDITIONER FULL_SINGLE_INVERSE
      &END OT
    &END SCF
    &XC
      &XC_FUNCTIONAL PBE
      &END XC_FUNCTIONAL
    &END XC
    &POISSON
      PERIODIC NONE
      POISSON_SOLVER MT
    &END POISSON
  &END DFT
  &SUBSYS
    &CELL
      ABC 15.0 15.0 15.0
      PERIODIC NONE
    &END CELL
    &COORD
      O  0.00000000  0.00000000  0.11926200
      H  0.00000000  0.76323900 -0.47704700
      H  0.00000000 -0.76323900 -0.47704700
    &END COORD
    &KIND O
      BASIS_SET SZV-MOLOPT-GTH
      POTENTIAL GTH-PBE-q6
    &END KIND
    &KIND H
      BASIS_SET SZV-MOLOPT-GTH
      POTENTIAL GTH-PBE-q1
    &END KIND
  &END SUBSYS
&END FORCE_EVAL
"""

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_GPU_LINE_RE = re.compile(r"^mem_mib=(\d+)\s+name=(.+)$")

# CP2K output facts (validated against real 2025.2 output; `[hartree]` is the
# current unit rendering, `(a.u.)` the legacy one — both accepted).
_CP2K_VERSION_RE = re.compile(r"CP2K\|\s*version string:\s*(.+?)\s*$", re.M)
_CP2K_ENERGY_RE = re.compile(
    r"ENERGY\| Total FORCE_EVAL \( [\w-]+ \) energy "
    r"(?:\[hartree\]|\(a\.u\.\))\s+(-?\d+\.\d+)"
)
_CP2K_CONVERGED_RE = re.compile(r"SCF run converged in\s+\d+ steps")


class QualificationReceiptError(RuntimeError):
    """Receipt construction violated the evidence contract."""


# -- canonical digests ---------------------------------------------------------


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def canonical_digest(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def compute_settlement_digest(report_dict: dict[str, Any]) -> str:
    """The SettlementReport digest formula (dispatcher.py), importable-free."""
    return canonical_digest(report_dict)


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


# -- production-side helpers ---------------------------------------------------


def source_commit(root: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(root),
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise QualificationReceiptError(f"git rev-parse HEAD failed: {proc.stderr}")
    return proc.stdout.strip()


def code_identity(root: Path) -> dict[str, str]:
    identity = {}
    for rel in CODE_IDENTITY_PATHS:
        path = Path(root) / rel
        if not path.is_file():
            raise QualificationReceiptError(f"code identity file missing: {rel}")
        identity[rel] = sha256_file(path)
    return identity


def parse_probe_stdout(stdout: str) -> dict[str, Any]:
    """Parse BENCH_PROBE/BENCH_GPU_DEVICE lines out of canary stdout."""
    probes: dict[str, str] = {}
    gpu_name = ""
    gpu_mem_mb = 0
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith(PROBE_PREFIX):
            _, _, rest = line.partition(PROBE_PREFIX)
            key, sep, value = rest.partition("=")
            if sep:
                probes[key.strip()] = value.strip()
        elif line.startswith(GPU_PREFIX):
            # Format: BENCH_GPU_DEVICE mem_mib=<int> name=<device name>;
            # the name may contain spaces, so it is the trailing value.
            match = _GPU_LINE_RE.match(line[len(GPU_PREFIX):])
            if match:
                gpu_mem_mb = int(match.group(1))
                gpu_name = match.group(2).strip()
    return {
        "probes": probes,
        "gpu_device_name": gpu_name,
        "gpu_memory_total_mb": gpu_mem_mb,
    }


def parse_cp2k_output(text: str) -> dict[str, Any]:
    """Extract the qualification-relevant facts from a CP2K output file.

    Single source of truth for the format: the trusted producer parses the
    fetched ``cp2k.out`` with this function at collection time, and the
    verifier independently re-parses the same bytes from the manifest-anchored
    artifact.  A claim that disagrees with the artifact cannot derive.
    """
    versions = _CP2K_VERSION_RE.findall(text)
    energies = _CP2K_ENERGY_RE.findall(text)
    return {
        "version_string": versions[0].strip() if versions else "",
        "energy_eh": energies[-1] if energies else None,
        "scf_converged": bool(_CP2K_CONVERGED_RE.search(text)),
    }


# -- receipt builder -----------------------------------------------------------


def seal_receipt(body: dict[str, Any]) -> dict[str, Any]:
    """Validate the verdict-free body against the schema and seal its digest."""
    _validate_schema(body)
    body["digest"] = canonical_digest(body)
    return body


def build_receipt_body(
    *,
    profile_site: str,
    site_profile_digest: str,
    runtime_lock: dict[str, str],
    authorization_scope: str,
    evidence: dict[str, Any],
    root: Path,
) -> dict[str, Any]:
    """Assemble the verdict-free receipt body and seal it with its digest.

    ``runtime_lock`` carries ``path`` (relative to root), ``sif_path_remote``
    and bare-hex ``sif_sha256`` exactly as frozen in the lock file.
    """
    body = {
        "kind": RECEIPT_KIND,
        "schema_id": SCHEMA_ID,
        "profile_site": profile_site,
        "site_profile_digest": site_profile_digest,
        "source_commit": source_commit(root),
        "code_identity": code_identity(root),
        "runtime_lock": dict(runtime_lock),
        "authorization_scope": authorization_scope,
        "evidence": evidence,
    }
    return seal_receipt(body)


# -- derivation verifier -------------------------------------------------------


def verify_receipt(
    receipt: dict[str, Any],
    *,
    root: Path,
    receipt_dir: Path,
    profile_path: Path | None = None,
) -> dict[str, Any]:
    """Derive the qualification status purely from the bound evidence.

    Returns::

        {"receipt_dir": ..., "digest_ok": bool, "problems": [...],
         "derived": {"qualification_status": "PASS"|"PARTIAL"|"INVALID",
                     "formal_qualified": bool,
                     "capabilities": {...} (per-capability PASS/FAIL/NOT_RUN),
                     "gates": {...}}}

    ``capabilities`` is the PER-JOB capability-matrix derivation (spec §4):
    ``dispatcher.cpu`` reads only the cpu canary job's own buckets and
    ``dispatcher.gpu`` the gpu job's — a broken route never fails the other
    route.  The shared anchors (schema, content digest, code identity,
    SiteProfile rebuild) are an explicit overlay applied equally to every
    capability.  ``runtime.cp2k`` / ``runtime.ai2kit`` derive from their own
    optional evidence blocks (absent => NOT_RUN).

    Aggregate (§5a): INVALID if any capability FAILs or the overlay is broken;
    PARTIAL if a runtime canary is NOT_RUN — the legacy meaning is restored,
    so an old consumer never misreads "CP2K not run" as full PASS; PASS only
    when every capability PASSes.  A case gates on its own
    ``[hpc.qualification].requires`` via
    :func:`case_requirements_satisfied`, never on the site-wide aggregate.
    """
    root = Path(root)
    receipt_dir = Path(receipt_dir)

    # -- layer 1: shared anchors (overlay) ---------------------------------
    # Schema, content digest, source commit, code identity, the receipt-level
    # runtime-lock binding and the rebuilt SiteProfile facts are envelope-wide:
    # a broken overlay fails EVERY capability (anti-forgery), never one route.
    overlay: dict[str, list[str]] = {}

    def problem(gate: str, message: str) -> None:
        overlay.setdefault(gate, []).append(message)

    # -- schema + content digest ------------------------------------------
    schema_errors = _schema_errors(receipt)
    for err in schema_errors:
        problem("schema", f"schema violation at {err.json_path}: {err.message}")
    declared = receipt.get("digest")
    recomputed = canonical_digest(
        {k: v for k, v in receipt.items() if k != "digest"}
    )
    digest_ok = declared == recomputed
    if not digest_ok:
        problem("schema", "digest mismatch: receipt is not content-addressed")

    # -- provenance anchors -------------------------------------------------
    commit = receipt.get("source_commit")
    if not isinstance(commit, str) or not _COMMIT_RE.match(commit):
        problem("provenance", f"source_commit malformed: {commit!r}")

    identity = receipt.get("code_identity") or {}
    for rel, want in sorted(identity.items()):
        path = root / rel
        if not path.is_file():
            problem("provenance", f"code identity file missing: {rel}")
            continue
        have = sha256_file(path)
        if have != want:
            problem(
                "provenance",
                f"code identity changed since qualification: {rel} "
                f"(receipt={want} disk={have})",
            )

    lock_block = receipt.get("runtime_lock") or {}
    lock_path = root / lock_block.get("path", "<missing>")
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        sif = lock.get("runtime", {})
        for field in ("sif_path_remote", "sif_sha256"):
            if lock_block.get(field) != sif.get(field):
                problem(
                    "provenance",
                    f"runtime_lock.{field} != on-disk lock "
                    f"(receipt={lock_block.get(field)!r} lock={sif.get(field)!r})",
                )
        lock_gpu = (lock.get("qualification") or {}).get("gpu")
    except FileNotFoundError:
        lock_gpu = None
        problem("provenance", f"runtime lock unreadable: {lock_path}")
    except json.JSONDecodeError as exc:
        lock_gpu = None
        problem("provenance", f"runtime lock malformed: {exc}")

    # -- site anchor: rebuild SiteProfile from the private config -----------
    evidence = receipt.get("evidence") or {}
    workloads = evidence.get("resolved_workloads") or {}
    site_digest = receipt.get("site_profile_digest")
    profile_file = profile_path or (root / DEFAULT_PROFILE_RELPATH)
    try:
        import tomllib

        config = tomllib.loads(profile_file.read_text(encoding="utf-8"))
        from dftworld_bench.hpc.site_profile import HpcSiteProfile

        site = HpcSiteProfile.from_cluster_config(config)
        if site.digest != site_digest:
            problem(
                "provenance",
                f"SiteProfile digest mismatch (receipt={site_digest} "
                f"rebuilt={site.digest}); site policy changed since qualification",
            )
        if evidence.get("site_profile_digest") != site_digest:
            problem("provenance", "evidence/site_profile_digest != top-level digest")
        for workload_type in ("cpu", "gpu"):
            claimed = workloads.get(workload_type) or {}
            try:
                live_resolved = site.resolve_workload(workload_type)
            except Exception as exc:  # noqa: BLE001 — unmappable cpu is a fact
                problem(
                    "provenance",
                    f"workload {workload_type!r} does not resolve against the "
                    f"current SiteProfile: {exc}",
                )
                continue
            if not claimed:
                problem(
                    "provenance",
                    f"resolved_workloads.{workload_type} missing from receipt",
                )
                continue
            for field in ("queue_name", "partition", "account", "qos", "gres"):
                if claimed.get(field) != getattr(live_resolved, field):
                    problem(
                        "provenance",
                        f"resolved_workloads.{workload_type}.{field} drifted "
                        f"from current SiteProfile (receipt={claimed.get(field)!r} "
                        f"now={getattr(live_resolved, field)!r})",
                    )
        mapping_cpu_queue = (site.resource_mapping.get("cpu") or {}).get("queue")
        cpu_accessible_derived = mapping_cpu_queue == "cpu"
        if evidence.get("native_cpu_partition_accessible") != cpu_accessible_derived:
            problem(
                "provenance",
                "native_cpu_partition_accessible inconsistent with the "
                "SiteProfile resource mapping",
            )
    except FileNotFoundError:
        problem(
            "provenance",
            f"cluster profile unavailable, site anchor undecidable: {profile_file}",
        )
    except Exception as exc:  # noqa: BLE001 — resolver/schema failures stay fail-closed
        problem("provenance", f"SiteProfile rebuild failed: {exc}")

    # -- layer 2: evidence-envelope (canary) gates ---------------------------
    # Facts about the jobs ARRAY as a whole (coverage, duplicates) apply to
    # both dispatcher capabilities, never to the runtime capabilities.
    canary_gates: dict[str, list[str]] = {}
    jobs = evidence.get("jobs") or []

    # -- layer 3: per-job derivation ------------------------------------------
    # Each canary job derives into ITS OWN buckets — a problem in the cpu job
    # never fails dispatcher.gpu and vice versa (spec §4).
    job_buckets: list[tuple[dict[str, Any], dict[str, list[str]]]] = []
    cpu_buckets: dict[str, list[str]] | None = None
    gpu_buckets: dict[str, list[str]] | None = None
    seen_ids: set[tuple[str, str]] = set()
    for index, job in enumerate(jobs):
        label = f"jobs[{index}]({job.get('canary', '?')})"
        own: dict[str, list[str]] = {}
        _derive_job(
            job, label, root=root, receipt_dir=receipt_dir,
            expected_sif_sha=(lock_block.get("sif_sha256") or ""),
            lock_gpu=lock_gpu, gates=own,
        )
        workload = workloads.get(job.get("probe_class")) or {}
        allowed_partitions = {
            value.strip()
            for value in str(workload.get("partition", "")).split(",")
            if value.strip()
        }
        actual_partition = (job.get("accounting") or {}).get("partition", "")
        if actual_partition and actual_partition not in allowed_partitions:
            own.setdefault("scheduler_facts", []).append(
                f"{label}: actual partition {actual_partition!r} is outside "
                f"the resolved set {sorted(allowed_partitions)}"
            )
        key = (job.get("run_id", ""), job.get("job_id", ""))
        if key in seen_ids:
            canary_gates.setdefault("audit_ledger", []).append(
                f"{label}: duplicate run_id/job_id"
            )
        seen_ids.add(key)
        probe_class = job.get("probe_class")
        if probe_class == "cpu":
            cpu_buckets = own
        elif probe_class == "gpu":
            gpu_buckets = own
        job_buckets.append((job, own))

    if not jobs:
        canary_gates.setdefault("scheduler_facts", []).append(
            "no canary jobs recorded"
        )

    # Canary coverage: the qualification must prove BOTH routes — a cpu-class
    # job on the native cpu queue and a gpu-class job under gres.
    canary_gates.setdefault("canary_coverage", [])
    classes_seen = {job.get("probe_class") for job in jobs}
    if not {"cpu", "gpu"} <= classes_seen:
        canary_gates["canary_coverage"].append(
            "canary set must include both probe classes; got "
            f"{sorted(c for c in classes_seen if c)}"
        )

    # P4 pass condition "no orphan job" at the envelope level: the canary
    # phase must record a settled scheduler sweep over its own run
    # directories (an orphaned allocation fails BOTH dispatcher routes).
    # The cancel probe carries its own sweep inside cancel_gate and derives
    # dispatcher.cancel independently.
    orphan = evidence.get("orphan_check")
    if not isinstance(orphan, dict) or not orphan.get("method"):
        canary_gates.setdefault("orphan_check", []).append(
            "evidence.orphan_check missing: the canary phase must record a "
            "terminal squeue/WorkDir sweep over its qualification runs "
            "(P4 pass condition 'no orphan job')"
        )
    else:
        runs_map = orphan.get("runs") or {}
        dirty = {rid: jid for rid, jid in runs_map.items() if jid is not None}
        if dirty:
            canary_gates.setdefault("orphan_check", []).append(
                f"orphan sweep found active jobs after collection: {dirty}"
            )
        elif orphan.get("active_total") != 0:
            canary_gates.setdefault("orphan_check", []).append(
                "orphan_check is internally inconsistent: no run is active "
                f"but active_total={orphan.get('active_total')!r}"
            )

    # -- layer 4: optional runtime evidence gates -----------------------------
    # Derived from their own bound evidence blocks, never declared.  A missing
    # block is NOT_RUN; present evidence derives PASS/FAIL.
    cp2k_block = evidence.get("cp2k_gate") or {}
    if cp2k_block.get("evidence") is None:
        cp2k_result = "NOT_RUN"
        cp2k_gates: dict[str, list[str]] = {}
    else:
        cp2k_result, cp2k_gates = _derive_cp2k(
            cp2k_block, root=root, receipt_dir=receipt_dir, lock_gpu=lock_gpu,
        )

    ai2kit_block = evidence.get("ai2kit_gate") or {}
    if ai2kit_block.get("evidence") is None:
        ai2kit_result = "NOT_RUN"
        ai2kit_gates: dict[str, list[str]] = {}
    else:
        ai2kit_result, ai2kit_gates = _derive_ai2kit(
            ai2kit_block, root=root, receipt_dir=receipt_dir,
        )

    cancel_block = evidence.get("cancel_gate") or {}
    if cancel_block.get("evidence") is None:
        cancel_result = "NOT_RUN"
        cancel_gates: dict[str, list[str]] = {}
    else:
        cancel_result, cancel_gates = _derive_cancel(
            cancel_block, root=root, receipt_dir=receipt_dir,
            expected_sif_sha=lock_block.get("sif_sha256") or "",
        )

    # -- assemble the public problem ledger -----------------------------------
    # Union of the overlay + canary + per-job + runtime buckets.  Bucket names
    # and message shapes are unchanged from the pre-matrix ledger; which
    # capability a message fails was resolved per job above — the union is the
    # informational view only, never the attribution source.
    gates: dict[str, list[str]] = {}
    for bucket in (overlay, canary_gates):
        for name, msgs in bucket.items():
            gates.setdefault(name, []).extend(msgs)
    for _job, own in job_buckets:
        for name, msgs in own.items():
            gates.setdefault(name, []).extend(msgs)
    for runtime_gates in (cp2k_gates, ai2kit_gates, cancel_gates):
        for name, msgs in runtime_gates.items():
            gates.setdefault(name, []).extend(msgs)

    gate_results = {
        name: ("FAIL" if msgs else "PASS")
        for name, msgs in gates.items()
    }
    gate_results["cp2k_gate"] = "FAIL" if gates.get("cp2k_gate") else cp2k_result
    gate_results["ai2kit_gate"] = (
        "FAIL" if gates.get("ai2kit_gate") else ai2kit_result
    )
    gate_results["cancel_gate"] = (
        "FAIL" if gates.get("cancel_gate") else cancel_result
    )

    # -- capability projection (spec §4) --------------------------------------
    # Overlay failures fail EVERY capability (the envelope is untrustworthy).
    # Canary-level (coverage / array) failures fail the dispatcher
    # capabilities.  Each per-job bucket set fails only its own dispatcher
    # route.  runtime.* caps read only their own evidence: matclaw-gpu rides
    # the gpu job's own buckets (absent gpu job => its evidence is absent, so
    # the claimed-qualified runtime fails closed too).  Verdict-free
    # invariant: capabilities are DERIVED here, never stored on the receipt.
    def clean(buckets: dict[str, list[str]] | None) -> bool:
        return buckets is not None and not any(buckets.values())

    overlay_broken = any(overlay.values())
    canary_broken = any(canary_gates.values())
    capabilities = {
        "dispatcher.cpu": (
            "PASS"
            if not overlay_broken and not canary_broken and clean(cpu_buckets)
            else "FAIL"
        ),
        "dispatcher.gpu": (
            "PASS"
            if not overlay_broken and not canary_broken and clean(gpu_buckets)
            else "FAIL"
        ),
        "runtime.matclaw-gpu": (
            "PASS" if not overlay_broken and clean(gpu_buckets) else "FAIL"
        ),
        "runtime.ai2kit": ai2kit_result if not overlay_broken else "FAIL",
        "runtime.cp2k": cp2k_result if not overlay_broken else "FAIL",
        # The cancel probe is an INDEPENDENT cell: it cannot rescue a broken
        # canary, and a missing probe never downgrades the canary routes —
        # it only keeps the aggregate from claiming full PASS.
        "dispatcher.cancel": cancel_result if not overlay_broken else "FAIL",
    }

    # -- aggregate (§5a) -------------------------------------------------------
    # INVALID when any capability FAILs (a broken overlay fails every
    # capability, so an envelope breach is exactly this).  PARTIAL when a
    # runtime canary is NOT_RUN — the legacy status meaning is restored, so
    # "CP2K not run" never reads as full PASS to an old consumer.  PASS only
    # when every capability PASSes; new consumers gate per case on the matrix,
    # never on this aggregate.
    if any(value == "FAIL" for value in capabilities.values()):
        status = "INVALID"
    elif any(value == "NOT_RUN" for value in capabilities.values()):
        status = "PARTIAL"
    else:
        status = "PASS"
    formal_qualified = status == "PASS"
    # BLOCKED_SITE_ACL (the old PARTIAL ∧ cpu→gpu ACL branch) is gone with the
    # ACL era: the collector refuses to qualify a profile that maps cpu
    # workloads off the native queue (QualifyError up front), and the overlay
    # provenance gate re-checks native_cpu_partition_accessible against the
    # rebuilt SiteProfile — a contradictory ACL claim fails every capability.

    problems = [
        f"[{gate}] {message}" for gate, msgs in sorted(gates.items())
        for message in msgs
    ]
    return {
        "receipt_dir": str(receipt_dir),
        "consistent": not problems,
        "digest_ok": digest_ok,
        "problems": problems,
        "derived": {
            "qualification_status": status,
            "formal_qualified": formal_qualified,
            "capabilities": capabilities,
            "gates": gate_results,
        },
    }


def case_requirements_satisfied(
    derived: dict[str, Any], requires: list[str] | tuple[str, ...]
) -> bool:
    """Gate a case on the derived capability matrix.

    ``requires`` is the case manifest's ``[hpc.qualification].requires`` list
    (capability names such as ``dispatcher.gpu`` or ``runtime.matclaw-gpu``).
    Returns True only when every named capability derives PASS — NOT_RUN or
    FAIL blocks the case.  Unknown names block too (fail-closed: a typo must
    never read as satisfied).  This reads only the verifier's *derived* view;
    the receipt itself carries no verdict fields.  A case gates on ITS OWN
    requires (spec §5b) — the site-wide aggregate PARTIAL does not block a case
    whose requires are all PASS.
    """
    capabilities = derived.get("capabilities") or {}
    return all(capabilities.get(name) == "PASS" for name in requires)


def _derive_cp2k(
    block: dict[str, Any],
    *,
    root: Path,
    receipt_dir: Path,
    lock_gpu: str | None,
) -> tuple[str, dict[str, list[str]]]:
    """Derive the cp2k gate from bound evidence. Returns (PASS|FAIL, gates).

    ``gates`` is scoped to THIS capability only (spec §4 layer 4): cp2k anchor
    breaks land in the ``cp2k_gate`` bucket with a ``cp2k: `` prefix, and the
    job record derives into its own per-job buckets under the ``cp2k-job: ``
    label which are folded into ``cp2k_gate`` as aggregation messages.  Nothing
    here can touch a dispatcher capability — a broken cp2k canary fails
    ``runtime.cp2k`` alone.

    Anchors, in order: the cp2k runtime lock on disk (a distinct SIF and a
    pinned binary/version), the full job-record derivation (state, accounting,
    TRES, probes, settlement, audit ledger, artifacts — same rules as a canary
    job), the input binding (declared text hashes to the recorded digest AND
    equals the staged artifact bytes), and an independent re-parse of the
    manifest-anchored ``cp2k.out`` against the claimed version/energy/
    convergence facts.
    """
    ev = block.get("evidence") or {}
    gates: dict[str, list[str]] = {}

    def problem(message: str) -> None:
        gates.setdefault("cp2k_gate", []).append(f"cp2k: {message}")

    broken = False

    # Own runtime anchor.
    lock_block = ev.get("runtime_lock") or {}
    lock_path = root / lock_block.get("path", "<missing>")
    expected_version = None
    binary = None
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        runtime = lock.get("runtime") or {}
        for field in ("sif_path_remote", "sif_sha256"):
            if lock_block.get(field) != runtime.get(field):
                problem(
                    f"runtime_lock.{field} != on-disk cp2k lock "
                    f"(receipt={lock_block.get(field)!r} "
                    f"lock={runtime.get(field)!r})"
                )
                broken = True
        cp2k_cfg = lock.get("cp2k") or {}
        expected_version = cp2k_cfg.get("version")
        binary = cp2k_cfg.get("binary")
    except FileNotFoundError:
        problem(f"cp2k runtime lock unreadable: {lock_path}")
        broken = True
    except json.JSONDecodeError as exc:
        problem(f"cp2k runtime lock malformed: {exc}")
        broken = True
    if not binary:
        problem("cp2k binary not pinned by its lock")
        broken = True

    # The job record must derive like any canary job; surface which buckets
    # it polluted so the overall verdict fails closed with visibility.  The
    # record derives into its OWN buckets (never the caller's), so a broken
    # cp2k canary cannot contaminate the dispatcher derivations; the polluted
    # buckets are folded into this capability's gate as aggregation messages.
    job = ev.get("job") or {}
    own: dict[str, list[str]] = {}
    _derive_job(
        job, "cp2k-job", root=root, receipt_dir=receipt_dir,
        expected_sif_sha=lock_block.get("sif_sha256") or "",
        lock_gpu=lock_gpu, gates=own,
    )
    for name, msgs in own.items():
        gates.setdefault(name, []).extend(msgs)
        problem(f"job evidence failed gate {name!r} (+{len(msgs)} problem(s))")
        broken = True

    # Input binding: declared text is self-hashed and byte-identical to the
    # staged artifact.
    inp = ev.get("input") or {}
    text = inp.get("text") or ""
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != inp.get("sha256"):
        problem("input.sha256 does not match sha256(input.text)")
        broken = True
    if not text.strip():
        problem("input text empty")
        broken = True

    manifest_paths = {
        entry.get("path")
        for entry in ((job.get("fetch_manifest") or {}).get("entries") or [])
    }
    out_rel = ev.get("output_artifact") or ""
    if not inp.get("name"):
        problem("input artifact name missing")
        broken = True
    elif inp["name"] not in manifest_paths:
        problem(f"input artifact {inp['name']!r} not in fetch_manifest")
        broken = True
    if not out_rel:
        problem("output_artifact missing")
        broken = True
    elif out_rel not in manifest_paths:
        problem(f"output artifact {out_rel!r} not in fetch_manifest")
        broken = True

    artifacts_dir = receipt_dir / job.get("artifacts_dir", "")
    raw = ""
    try:
        staged = (artifacts_dir / (inp.get("name") or "<missing>")).read_bytes()
        if staged.decode("utf-8") != text:
            problem("staged input artifact differs from declared input text")
            broken = True
    except OSError:
        problem(f"input artifact unreadable: {inp.get('name')!r}")
        broken = True
    try:
        raw = (artifacts_dir / out_rel).read_text(
            encoding="utf-8", errors="replace"
        ) if out_rel else ""
    except OSError:
        problem(f"output artifact unreadable: {artifacts_dir / out_rel}")
        broken = True

    # Independent re-parse of the anchored artifact bytes vs the claims.
    claimed = ev.get("parsed") or {}
    if claimed.get("scf_converged") is not True:
        problem("parsed.scf_converged must be true")
        broken = True
    if raw:
        parsed_out = parse_cp2k_output(raw)
        if not parsed_out["version_string"]:
            problem("no CP2K version line in output artifact")
            broken = True
        elif claimed.get("version_string") != parsed_out["version_string"]:
            problem(
                f"version mismatch (claimed={claimed.get('version_string')!r} "
                f"artifact={parsed_out['version_string']!r})"
            )
            broken = True
        if (
            expected_version
            and parsed_out["version_string"]
            and expected_version not in parsed_out["version_string"]
        ):
            problem(
                f"locked cp2k version drift (lock={expected_version!r} "
                f"artifact={parsed_out['version_string']!r})"
            )
            broken = True
        if parsed_out["energy_eh"] is None:
            problem("no total energy line in output artifact")
            broken = True
        elif claimed.get("energy_eh") != parsed_out["energy_eh"]:
            problem(
                f"energy mismatch (claimed={claimed.get('energy_eh')!r} "
                f"artifact={parsed_out['energy_eh']!r})"
            )
            broken = True
        if not parsed_out["scf_converged"]:
            problem("SCF convergence marker absent from output artifact")
            broken = True

    return ("FAIL" if broken else "PASS"), gates


def _derive_ai2kit(
    block: dict[str, Any],
    *,
    root: Path,
    receipt_dir: Path,
) -> tuple[str, dict[str, list[str]]]:
    """Derive the ai2kit gate from bound evidence. Returns (PASS|FAIL, gates).

    Runtime-canary mirror of ``_derive_cp2k`` minus the domain re-parse: an
    ai2kit canary has no CP2K-style input/output artifact for this verifier to
    re-parse, so the anchors are the runtime lock on disk (the pinned
    ai2kit runtime) and the full job-record derivation (state, accounting,
    TRES, probes, settlement, audit ledger, artifacts) under the ``ai2kit-job:``
    label.  Problems are scoped to THIS capability only — the job record
    derives into its own buckets which are folded into the ``ai2kit_gate``
    bucket as aggregation messages, exactly like the cp2k gate.  Absent
    evidence (no block / null ``evidence``) is NOT_RUN, handled by the caller.
    """
    ev = block.get("evidence") or {}
    gates: dict[str, list[str]] = {}

    def problem(message: str) -> None:
        gates.setdefault("ai2kit_gate", []).append(f"ai2kit: {message}")

    broken = False

    # Own runtime anchor: the evidence block pins the ai2kit runtime lock on
    # disk; the pinned SIF fields must equal the on-disk lock's runtime record.
    lock_block = ev.get("runtime_lock") or {}
    lock_path = root / lock_block.get("path", "<missing>")
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        runtime = lock.get("runtime") or {}
        for field in ("sif_path_remote", "sif_sha256"):
            if lock_block.get(field) != runtime.get(field):
                problem(
                    f"runtime_lock.{field} != on-disk ai2kit lock "
                    f"(receipt={lock_block.get(field)!r} "
                    f"lock={runtime.get(field)!r})"
                )
                broken = True
    except FileNotFoundError:
        problem(f"ai2kit runtime lock unreadable: {lock_path}")
        broken = True
    except json.JSONDecodeError as exc:
        problem(f"ai2kit runtime lock malformed: {exc}")
        broken = True

    # The job record must derive like any canary job, into its own buckets.
    job = ev.get("job") or {}
    own: dict[str, list[str]] = {}
    _derive_job(
        job, "ai2kit-job", root=root, receipt_dir=receipt_dir,
        expected_sif_sha=lock_block.get("sif_sha256") or "",
        lock_gpu=None, gates=own,
    )
    for name, msgs in own.items():
        gates.setdefault(name, []).extend(msgs)
        problem(f"job evidence failed gate {name!r} (+{len(msgs)} problem(s))")
        broken = True

    return ("FAIL" if broken else "PASS"), gates


def _derive_cancel(
    block: dict[str, Any],
    *,
    root: Path,
    receipt_dir: Path,
    expected_sif_sha: str,
) -> tuple[str, dict[str, list[str]]]:
    """Derive the explicit cancel probe (P4 step 7). Returns (PASS|FAIL, gates).

    Mirror of ``_derive_ai2kit`` for a fundamentally DIFFERENT job shape: a
    SUCCEEDED canary proves the forward path; this record proves the teardown
    path — a RUNNING containerized job reaches terminal CANCELLED through
    ``dispatcher.cancel`` and leaves no orphan.  So it cannot ride
    :func:`_derive_job` (which demands SUCCEEDED, exit 0 and containment
    probe lines that a killed sleep does not have).  The anchors: the digest
    suffix binding the pinned SIF, scheduler accounting normalized to
    CANCELLED with an AllocTRES that proves the job WAS allocated (a
    pending-state cancel would prove strictly less and is refused), the
    cancel_ops triple re-checked independently of the schema, the settlement
    ledger recording exactly this attempt as CANCELLED with a recomputed
    report digest, the hash-chained audit ledger replayed for the
    submit/settle lineage PLUS the gateway's own ``cancel`` event, the
    re-hashed fetched artifacts, and the post-cancel orphan sweep.  Absent
    evidence is NOT_RUN, handled by the caller.
    """
    from scripts.ablation.transport.slurm_transport import normalize_state
    from dftworld_bench.hpc.tres import parse_tres

    ev = block.get("evidence") or {}
    gates: dict[str, list[str]] = {}

    def problem(gate: str, message: str) -> None:
        gates.setdefault(gate, []).append(f"cancel-probe: {message}")

    job = ev.get("job") or {}
    label_run = job.get("run_id", "?")

    def fact(message: str) -> None:
        problem("cancel_probe_facts", message)

    # Runtime binding: the probe ran the SAME pinned SIF as the canaries.
    decl = job.get("runtime_decl", "")
    if expected_sif_sha and not decl.endswith(f"@sha256:{expected_sif_sha}"):
        problem(
            "provenance",
            f"runtime_decl {decl!r} does not bind the frozen SIF "
            f"@sha256:{expected_sif_sha}",
        )

    # Terminal state triangulation: record, raw accounting, and cancel_ops.
    if job.get("state") != "CANCELLED":
        fact(f"state={job.get('state')!r}, probe did not end CANCELLED")
    if job.get("cancel_proved") is not True:
        fact(f"cancel_proved={job.get('cancel_proved')!r}")
    accounting = job.get("accounting") or {}
    raw_state = accounting.get("raw_state", "")
    normalized = normalize_state(raw_state)
    if normalized is None or normalized.name != "CANCELLED":
        fact(
            f"accounting.raw_state={raw_state!r} is not scheduler-CANCELLED; "
            "the teardown claim is unanchored"
        )
    for field in ("source", "partition", "node_list"):
        if not accounting.get(field):
            fact(f"accounting.{field} is missing")
    exit_raw = str(accounting.get("exit_code_raw", ""))
    exit_code = job.get("exit_code")
    try:
        # Signal-killed exits are site-dependent (schema demands no const),
        # but the recorded code must equal the raw prefix recomputed here.
        if not exit_raw or int(exit_raw.split(":")[0]) != int(exit_code):
            fact(
                f"exit_code={exit_code!r} inconsistent with "
                f"accounting.exit_code_raw={exit_raw!r}"
            )
    except (TypeError, ValueError):
        fact(f"exit_code={exit_code!r} / exit_code_raw={exit_raw!r} unparseable")

    # CPU-only teardown, and it must be an ALLOCATED teardown: AllocTRES
    # proving cpu is what distinguishes "cancelled a RUNNING job" from
    # "cancelled a queued job".
    requested = job.get("requested_resources") or {}
    if requested.get("gpus") != 0 or job.get("gpus_requested") != 0:
        fact("cancel probe must be gpu-free")
    req_parsed = parse_tres(accounting.get("req_tres", ""))
    alloc_parsed = parse_tres(accounting.get("alloc_tres", ""))
    if req_parsed.count or alloc_parsed.count:
        fact("cancel probe carries GPU TRES")
    if not requested.get("cpus") or not requested.get("memory_gb"):
        fact("requested_resources.cpus / memory_gb must be explicit")
    alloc_cpu = _tres_fields(accounting.get("alloc_tres", "")).get("cpu", "")
    try:
        allocated = int(alloc_cpu) > 0
    except ValueError:
        allocated = False
    if not allocated:
        fact(
            f"AllocTRES cpu={alloc_cpu!r}: no allocation recorded, so the "
            "job cannot be shown to have reached RUNNING — a pending-state "
            "cancel proves strictly less and is refused"
        )

    # Cancel operation re-checked independently of the schema consts.
    ops = job.get("cancel_ops") or {}
    if ops.get("cancelled_via") != "dispatcher.cancel":
        fact(
            f"cancelled_via={ops.get('cancelled_via')!r} "
            "(must go through the dispatcher)"
        )
    if ops.get("state_at_cancel") != "RUNNING":
        fact(
            f"state_at_cancel={ops.get('state_at_cancel')!r} "
            "(probe must cancel a RUNNING job)"
        )
    if ops.get("status_after_cancel") != "CANCELLED":
        fact(
            f"status_after_cancel={ops.get('status_after_cancel')!r} != "
            "CANCELLED: scheduler never acknowledged the teardown"
        )
    usage_op = ops.get("usage_op")
    if not isinstance(usage_op, dict) or not usage_op:
        fact("cancel_ops.usage_op missing: post-cancel usage was not probed")

    # Settlement: exact single-attempt lineage, attempt state CANCELLED.
    settlement = job.get("settlement") or {}
    report = settlement.get("report") or {}
    if settlement.get("digest") != compute_settlement_digest(report):
        problem("settlement_integrity", "settlement digest does not match report")
    if report.get("run_id") != job.get("run_id"):
        problem(
            "settlement_integrity",
            f"settlement.run_id={report.get('run_id')!r} != job.run_id",
        )
    attempts = report.get("attempts") or []
    expected_attempt = {
        "operation_id": job.get("operation_id"),
        "attempt": job.get("attempt"),
        "job_id": job.get("job_id"),
        "state": "CANCELLED",
    }
    if attempts != [expected_attempt]:
        problem(
            "settlement_integrity",
            f"settlement attempts != [{expected_attempt}] (got {attempts})",
        )
    if report.get("cancelled_jobs"):
        # The probe job was already terminal at settle time; anything in
        # cancelled_jobs means settle itself killed a stray — an orphan.
        problem(
            "settlement_integrity",
            f"settle had to cancel strays: {report.get('cancelled_jobs')}",
        )

    # Audit ledger: replay the chain + bind submit/settle lineage, then
    # require the gateway's OWN cancel event (the raw-scancel forgery gap).
    audit_rel = job.get("audit_log", "")
    audit_path = receipt_dir / audit_rel
    prefixer = lambda gate_, msg_: problem(gate_, f"cancel-probe: {msg_}")  # noqa: E731
    entries = _load_audit(audit_path, prefixer)
    if entries is not None:
        _check_audit(entries, job, prefixer)
        cancels = [
            e for e in entries
            if (e.get("event") or {}).get("kind") == "cancel"
            and (e.get("event") or {}).get("run_id") == job.get("run_id")
            and (e.get("event") or {}).get("job_id") == job.get("job_id")
        ]
        if len(cancels) < 1:
            problem(
                "audit_ledger",
                f"no gateway cancel event for {label_run} / "
                f"{job.get('job_id')!r}; a cancel that never passed the "
                "gateway's token+ownership checks is not dispatcher proof",
            )

    # Fetch manifest: re-hash the fetched bytes on disk.
    manifest = job.get("fetch_manifest") or {}
    artifacts_dir = receipt_dir / job.get("artifacts_dir", "")
    for entry in manifest.get("entries") or []:
        path = artifacts_dir / entry.get("path", "")
        if not path.is_file():
            problem("artifact_manifest", f"artifact missing: {path}")
            continue
        data = path.read_bytes()
        if len(data) != entry.get("size_bytes"):
            problem(
                "artifact_manifest",
                f"artifact size drift: {entry.get('path')} "
                f"(manifest={entry.get('size_bytes')} disk={len(data)})",
            )
        have = hashlib.sha256(data).hexdigest()
        if have != entry.get("sha256"):
            problem(
                "artifact_manifest",
                f"artifact digest drift: {entry.get('path')} "
                f"(manifest={entry.get('sha256')} disk={have})",
            )

    # Post-cancel orphan sweep, recomputed from its own runs map.
    orphan = ev.get("orphan_check") or {}
    if not orphan.get("method"):
        fact("orphan_check records no sweep method")
    runs_map = orphan.get("runs") or {}
    if job.get("run_id") not in runs_map:
        fact(f"orphan_check does not cover the probe run {job.get('run_id')!r}")
    dirty = {rid: jid for rid, jid in runs_map.items() if jid is not None}
    if dirty:
        fact(f"orphan sweep found active jobs after the cancel: {dirty}")
    elif orphan.get("active_total") != 0:
        fact(
            f"orphan_check inconsistent: no run active but "
            f"active_total={orphan.get('active_total')!r}"
        )

    return ("FAIL" if any(gates.values()) else "PASS"), gates


def _tres_fields(raw: str) -> dict[str, str]:
    """Parse scalar Slurm TRES entries without interpreting GPU subtypes."""
    fields: dict[str, str] = {}
    for entry in raw.split(","):
        key, sep, value = entry.strip().partition("=")
        if sep and key:
            fields[key] = value
    return fields


def _memory_mib(raw: str) -> int | None:
    """Normalize Slurm memory quantities (K/M/G/T) to MiB."""
    match = re.fullmatch(r"([0-9]+)([KMGT]?)", raw.strip(), re.IGNORECASE)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).upper()
    factors = {"": 1, "K": 1 / 1024, "M": 1, "G": 1024, "T": 1024 * 1024}
    return int(amount * factors[unit])


def _derive_job(
    job: dict[str, Any],
    label: str,
    *,
    root: Path,
    receipt_dir: Path,
    expected_sif_sha: str,
    lock_gpu: str | None,
    gates: dict[str, list[str]],
) -> None:
    """Derive ONE job record into PER-JOB buckets (spec §4 layer 3).

    ``gates`` is always the calling job's own dict: the canary loop hands a
    fresh dict per canary job, and the runtime gate derives
    (``_derive_cp2k``/``_derive_ai2kit``) hand a fresh dict per runtime job
    record.  A problem therefore lands only in the owning capability's set —
    the bucket names and ``{label}: {message}`` shapes are unchanged from the
    shared-ledger era, only the attribution is now structural.
    """
    def problem(gate: str, message: str) -> None:
        gates.setdefault(gate, []).append(f"{label}: {message}")

    # Runtime declaration binding: image-name agnostic, digest-bound.
    decl = job.get("runtime_decl", "")
    if expected_sif_sha and not decl.endswith(f"@sha256:{expected_sif_sha}"):
        problem(
            "provenance",
            f"runtime_decl {decl!r} does not bind the frozen SIF "
            f"@sha256:{expected_sif_sha}",
        )

    # Terminal state + exit code, cross-checked against raw accounting.
    state = job.get("state")
    exit_code = job.get("exit_code")
    accounting = job.get("accounting") or {}
    raw_state = accounting.get("raw_state", "")
    exit_raw = accounting.get("exit_code_raw", "")
    if state != "SUCCEEDED":
        problem("scheduler_facts", f"state={state!r}, canary did not succeed")
    if exit_code != 0:
        problem("scheduler_facts", f"exit_code={exit_code!r}")
    from scripts.ablation.transport.slurm_transport import normalize_state

    normalized = normalize_state(raw_state)
    if normalized is None or normalized.name != "COMPLETED":
        problem(
            "scheduler_facts",
            f"accounting.raw_state={raw_state!r} is not scheduler-COMPLETED",
        )
    if not exit_raw.startswith("0:"):
        problem("scheduler_facts", f"accounting.exit_code_raw={exit_raw!r}")
    if not accounting.get("source"):
        problem("scheduler_facts", "accounting.source command not recorded")
    if not accounting.get("partition"):
        problem("scheduler_facts", "accounting.partition is missing")
    if not accounting.get("node_list"):
        problem("scheduler_facts", "accounting.node_list is missing")

    # TRES reconciliation: exact GPU-count agreement per submitted spec
    # (C4 semantics for GPU jobs; MIG-classified allocations are judged by
    # the in-container device probe, matching the known <site-alias> artifact).
    from dftworld_bench.hpc.tres import parse_tres, verify_full_gpu

    req_tres = accounting.get("req_tres", "")
    alloc_tres = accounting.get("alloc_tres", "")
    requested = job.get("requested_resources") or {}
    expected_gpus = requested.get("gpus")
    if expected_gpus is None:
        problem("tres_reconciliation", "requested_resources.gpus is missing")
        expected_gpus = job.get("gpus_requested", 0)
    if job.get("gpus_requested") != expected_gpus:
        problem(
            "tres_reconciliation",
            "gpus_requested disagrees with requested_resources.gpus",
        )
    req_parsed = parse_tres(req_tres)
    alloc_parsed = parse_tres(alloc_tres)
    if req_parsed.count != expected_gpus:
        problem(
            "tres_reconciliation",
            f"ReqTRES shows {req_parsed.count} GPUs but the spec requested "
            f"{expected_gpus}",
        )
    if alloc_parsed.count != expected_gpus:
        problem(
            "tres_reconciliation",
            f"AllocTRES shows {alloc_parsed.count} GPUs but {expected_gpus} "
            "were requested",
        )
    if expected_gpus >= 1:
        ok, reason = verify_full_gpu(req_tres, alloc_tres)
        if not ok:
            problem("tres_reconciliation", reason)
    req_fields = _tres_fields(req_tres)
    alloc_fields = _tres_fields(alloc_tres)
    expected_cpus = requested.get("cpus")
    if expected_cpus is None:
        problem("tres_reconciliation", "requested_resources.cpus is missing")
    else:
        for label_, fields in (("ReqTRES", req_fields), ("AllocTRES", alloc_fields)):
            try:
                observed = int(fields.get("cpu", ""))
            except ValueError:
                observed = None
            if observed != expected_cpus:
                problem(
                    "tres_reconciliation",
                    f"{label_} cpu={observed!r}, expected {expected_cpus}",
                )
    expected_memory_gb = requested.get("memory_gb")
    if expected_memory_gb is None:
        problem("tres_reconciliation", "requested_resources.memory_gb is missing")
    else:
        expected_memory_mib = int(expected_memory_gb) * 1024
        for label_, fields in (("ReqTRES", req_fields), ("AllocTRES", alloc_fields)):
            observed = _memory_mib(fields.get("mem", ""))
            if observed != expected_memory_mib:
                problem(
                    "tres_reconciliation",
                    f"{label_} mem={fields.get('mem')!r} ({observed!r} MiB), "
                    f"expected {expected_memory_gb}G ({expected_memory_mib} MiB)",
                )

    # Containment probes: re-parse stdout independently, then require
    # agreement with the structured probe results (every job, any class).
    stdout = job.get("stdout_tail", "")
    parsed = parse_probe_stdout(stdout)
    probes = job.get("probe_results") or {}
    if MARKER_CONTAINMENT not in stdout:
        problem("containment_probe", f"{MARKER_CONTAINMENT} marker absent")
    for key in _PROBE_KEYS:
        observed = parsed["probes"].get(key)
        if observed != "pass":
            problem(
                "containment_probe",
                f"probe {key} not passing in stdout (observed={observed!r})",
            )
        claimed = probes.get(key)
        if claimed is not True:
            problem("containment_probe", f"probe_results.{key}={claimed!r}")

    # GPU device probe: only meaningful for gpu-class jobs; a cpu-partition
    # node has no GPU and must not fake one.
    if job.get("probe_class") == "gpu":
        if MARKER_GPU not in stdout:
            problem("gpu_device_probe", f"{MARKER_GPU} marker absent")
        gpu_name = parsed["gpu_device_name"]
        if not gpu_name:
            problem(
                "gpu_device_probe",
                f"{GPU_PREFIX.strip()} line absent/unparseable",
            )
        if probes.get("gpu_device_name") != gpu_name:
            problem(
                "gpu_device_probe",
                f"probe_results.gpu_device_name="
                f"{probes.get('gpu_device_name')!r} != stdout {gpu_name!r}",
            )
        if probes.get("gpu_memory_total_mb") != parsed["gpu_memory_total_mb"]:
            problem(
                "gpu_device_probe",
                "probe_results.gpu_memory_total_mb != stdout value",
            )
        if lock_gpu and gpu_name and lock_gpu.lower() not in gpu_name.lower():
            problem(
                "gpu_device_probe",
                f"probe device {gpu_name!r} does not match the runtime-lock "
                f"qualified GPU {lock_gpu!r}",
            )
    elif parsed["gpu_device_name"]:
        problem(
            "containment_probe",
            "cpu-class job stdout carries a BENCH_GPU_DEVICE line; "
            "cpu-partition nodes have no GPU",
        )

    # Settlement: recompute the immutable report digest and demand exact
    # single-attempt lineage with nothing cancelled.
    settlement = job.get("settlement") or {}
    report = settlement.get("report") or {}
    if settlement.get("digest") != compute_settlement_digest(report):
        problem("settlement_integrity", "settlement digest does not match report")
    if report.get("run_id") != job.get("run_id"):
        problem(
            "settlement_integrity",
            f"settlement.run_id={report.get('run_id')!r} != job.run_id",
        )
    attempts = report.get("attempts") or []
    expected_attempt = {
        "operation_id": job.get("operation_id"),
        "attempt": job.get("attempt"),
        "job_id": job.get("job_id"),
        "state": "SUCCEEDED",
    }
    if attempts != [expected_attempt]:
        problem(
            "settlement_integrity",
            f"settlement attempts != [{expected_attempt}] (got {attempts})",
        )
    if report.get("cancelled_jobs"):
        problem(
            "settlement_integrity",
            f"cancelled_jobs not empty: {report.get('cancelled_jobs')}",
        )

    # Audit ledger: replay the hash chain and bind (operation, attempt, job).
    audit_rel = job.get("audit_log", "")
    audit_path = receipt_dir / audit_rel
    entries = _load_audit(audit_path, problem)
    if entries is not None:
        _check_audit(entries, job, problem)

    # Fetch manifest: re-hash the fetched bytes on disk.
    manifest = job.get("fetch_manifest") or {}
    artifacts_dir = receipt_dir / job.get("artifacts_dir", "")
    for entry in manifest.get("entries") or []:
        path = artifacts_dir / entry.get("path", "")
        if not path.is_file():
            problem("artifact_manifest", f"artifact missing: {path}")
            continue
        data = path.read_bytes()
        if len(data) != entry.get("size_bytes"):
            problem(
                "artifact_manifest",
                f"artifact size drift: {entry.get('path')} "
                f"(manifest={entry.get('size_bytes')} disk={len(data)})",
            )
        have = hashlib.sha256(data).hexdigest()
        if have != entry.get("sha256"):
            problem(
                "artifact_manifest",
                f"artifact digest drift: {entry.get('path')} "
                f"(manifest={entry.get('sha256')} disk={have})",
            )


def _load_audit(audit_path: Path, problem) -> list[dict[str, Any]] | None:
    from dftworld_bench.hpc.audit import GatewayAudit

    if not audit_path.is_file():
        problem("audit_ledger", f"audit log missing: {audit_path}")
        return None
    try:
        ledger = GatewayAudit(audit_path)
    except Exception as exc:  # noqa: BLE001 — corrupt lines fail closed
        problem("audit_ledger", f"audit log unreadable: {exc}")
        return None
    broken = ledger.verify()
    for digest in broken:
        problem("audit_ledger", f"hash chain broken at entry {digest[:16]}…")
    return ledger.entries()


def _check_audit(
    entries: list[dict[str, Any]],
    job: dict[str, Any],
    problem,
) -> None:
    run_id = job.get("run_id")

    def event_kind(entry: dict[str, Any]) -> str:
        return (entry.get("event") or {}).get("kind", "")

    accepted: list[tuple[str, int, str]] = []
    settlement_begins = 0
    voided = 0
    for entry in entries:
        event = entry.get("event") or {}
        kind = event.get("kind")
        if event.get("run_id") != run_id:
            continue
        if kind == "SUBMIT_ACCEPTED":
            accepted.append(
                (
                    event.get("operation_id"),
                    event.get("attempt"),
                    event.get("job_id"),
                )
            )
        elif kind == "SETTLEMENT_BEGIN":
            settlement_begins += 1
        elif kind == "SUBMIT_VOIDED":
            voided += 1
    triple = (job.get("operation_id"), job.get("attempt"), job.get("job_id"))
    if triple not in accepted:
        problem(
            "audit_ledger",
            f"(operation, attempt, job) {triple} never accepted in the "
            f"durable ledger (accepted={accepted})",
        )
    extra = [a for a in accepted if a != triple]
    if extra:
        # Orphan scheduler jobs: submitted under this run but absent from
        # settlement — exactly what "no orphan jobs" forbids.
        problem("audit_ledger", f"orphan submissions outside settlement: {extra}")
    if settlement_begins < 1:
        problem(
            "audit_ledger",
            f"no SETTLEMENT_BEGIN for run {run_id!r}; submissions were never frozen",
        )
    if voided:
        problem(
            "audit_ledger", f"{voided} SUBMIT_VOIDED intents in the qualification run"
        )


# -- schema plumbing -------------------------------------------------------------

_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "schemas"
    / "dispatcher-qualification-receipt.schema.json"
)


def _load_schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


def _schema_errors(receipt: dict[str, Any]) -> list[Any]:
    import jsonschema

    validator = jsonschema.Draft202012Validator(_load_schema())
    return sorted(validator.iter_errors(receipt), key=lambda e: list(e.path))


def _validate_schema(body: dict[str, Any]) -> None:
    errors = _schema_errors(body)
    if errors:
        first = errors[0]
        where = ".".join(str(p) for p in first.path) or "$"
        raise QualificationReceiptError(
            f"receipt body violates {SCHEMA_ID} at {where}: {first.message}"
        )
