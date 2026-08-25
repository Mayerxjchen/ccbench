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

Derivation ladder: no cp2k evidence => PARTIAL; cp2k evidence deriving clean
=> PASS (formal_qualified); broken evidence anywhere => INVALID.  The verdict
is always computed here — never read from the receipt.

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
# once a receipt exists.  Smoke-validated in
# dftworld-base-ai2kit:0.1.0-cpu-controller (cp2k.psmp, 2025.2).
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
         "derived": {"qualification_status": "PARTIAL"|"PASS"|"INVALID",
                     "formal_qualified": bool,
                     "site_acl_blocked": bool,
                     "gates": {...}}}
    """
    root = Path(root)
    receipt_dir = Path(receipt_dir)
    gates: dict[str, list[str]] = {}

    def problem(gate: str, message: str) -> None:
        gates.setdefault(gate, []).append(message)

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

    # -- per-job derivation ---------------------------------------------------
    jobs = evidence.get("jobs") or []
    seen_ids: set[tuple[str, str]] = set()
    for index, job in enumerate(jobs):
        label = f"jobs[{index}]({job.get('canary', '?')})"
        _derive_job(
            job, label, root=root, receipt_dir=receipt_dir,
            expected_sif_sha=(lock_block.get("sif_sha256") or ""),
            lock_gpu=lock_gpu, gates=gates,
        )
        key = (job.get("run_id", ""), job.get("job_id", ""))
        if key in seen_ids:
            problem("audit_ledger", f"{label}: duplicate run_id/job_id")
        seen_ids.add(key)

    if not jobs:
        problem("scheduler_facts", "no canary jobs recorded")

    # Canary coverage: the qualification must prove BOTH routes — a cpu-class
    # job on the native cpu queue and a gpu-class job under gres.
    gates.setdefault("canary_coverage", [])
    classes_seen = {job.get("probe_class") for job in jobs}
    if not {"cpu", "gpu"} <= classes_seen:
        problem(
            "canary_coverage",
            f"canary set must include both probe classes; got "
            f"{sorted(c for c in classes_seen if c)}",
        )

    # -- cp2k gate: derived from its bound evidence, never declared -----------
    cp2k_block = evidence.get("cp2k_gate") or {}
    if cp2k_block.get("evidence") is None:
        gates.setdefault("cp2k_gate", [])
        cp2k_result = "NOT_RUN"
    else:
        cp2k_result = _derive_cp2k(
            cp2k_block, root=root, receipt_dir=receipt_dir,
            lock_gpu=lock_gpu, gates=gates,
        )

    # -- assemble derived verdict ---------------------------------------------
    gate_results = {
        name: ("FAIL" if msgs else "PASS")
        for name, msgs in gates.items()
    }
    gate_results["cp2k_gate"] = (
        "FAIL" if gates.get("cp2k_gate") else cp2k_result
    )
    canary_ok = all(
        result == "PASS"
        for name, result in gate_results.items()
        if name not in ("schema", "cp2k_gate")
    )
    schema_ok = "schema" not in gates
    if not schema_ok or not canary_ok:
        status = "INVALID"
    elif cp2k_result == "NOT_RUN":
        status = "PARTIAL"
    elif cp2k_result == "PASS":
        status = "PASS"
    else:
        # CP2K evidence present but broken — the receipt cannot be trusted
        # far enough to distinguish PARTIAL from forgery.
        status = "INVALID"
    formal_qualified = status == "PASS"
    site_acl_blocked = status == "PARTIAL" and (
        evidence.get("native_cpu_partition_accessible") is False
    )

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
            "site_acl_blocked": site_acl_blocked,
            "gates": gate_results,
        },
    }


def _derive_cp2k(
    block: dict[str, Any],
    *,
    root: Path,
    receipt_dir: Path,
    lock_gpu: str | None,
    gates: dict[str, list[str]],
) -> str:
    """Derive the cp2k gate from bound evidence. Returns PASS or FAIL.

    Anchors, in order: the cp2k runtime lock on disk (a distinct SIF and a
    pinned binary/version), the full job-record derivation (state, accounting,
    TRES, probes, settlement, audit ledger, artifacts — same rules as a canary
    job), the input binding (declared text hashes to the recorded digest AND
    equals the staged artifact bytes), and an independent re-parse of the
    manifest-anchored ``cp2k.out`` against the claimed version/energy/
    convergence facts.
    """
    ev = block.get("evidence") or {}

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
    # it polluted so the overall verdict fails closed with visibility.
    job = ev.get("job") or {}
    before = {name: len(msgs) for name, msgs in gates.items()}
    _derive_job(
        job, "cp2k-job", root=root, receipt_dir=receipt_dir,
        expected_sif_sha=lock_block.get("sif_sha256") or "",
        lock_gpu=lock_gpu, gates=gates,
    )
    for name in list(gates):
        msgs = gates[name]
        if len(msgs) > before.get(name, 0):
            problem(f"job evidence failed gate {name!r} "
                    f"(+{len(msgs) - before.get(name, 0)} problem(s))")
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

    return "FAIL" if broken else "PASS"


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

    # TRES reconciliation: exact GPU-count agreement per submitted spec
    # (C4 semantics for GPU jobs; MIG-classified allocations are judged by
    # the in-container device probe, matching the known <site-alias> artifact).
    from dftworld_bench.hpc.tres import parse_tres, verify_full_gpu

    req_tres = accounting.get("req_tres", "")
    alloc_tres = accounting.get("alloc_tres", "")
    expected_gpus = job.get("gpus_requested", 0)
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
    if "cpu=" not in req_tres or "mem=" not in req_tres:
        problem(
            "tres_reconciliation",
            f"req_tres lacks cpu/mem accounting terms: {req_tres!r}",
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
