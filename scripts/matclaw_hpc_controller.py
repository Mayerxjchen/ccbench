#!/usr/bin/env python3
"""Restricted sandbox-to-HPC controller for Cases 031/032/033 (MatClaw CIPS).

The agent inside the local Docker sandbox is ONLY a control layer.  It drives
real GPU work through this controller, which wraps the Case 034
``SshSlurmTransport``.  SSH credentials no longer travel into the agent: the
transport runs inside the trusted bench-hpc gateway on the site host, and this
controller image ships no SSH client, rsync, key, or config.  Scientific MD and
DeePMD training never run in the local controller container — they run on Slurm
``gpu`` under Apptainer on an A100.

The three cases share ONE GPU runtime: a single locked SIF plus its A100
qualification receipt.  ``submit`` for ``paper``/``smoke`` validates the shared
runtime lock AND the fetched qualification receipt before sbatch and fails
closed if either is missing or ineligible.  ``probe`` is a self-contained GPU
canary and never needs the lock.

Security model (fail closed):
  * the active case comes from the mounted ``case-policy.json`` (fixed read-only
    path), strictly cross-checked against the immutable in-image
    ``CASE_POLICIES`` table — an unknown, missing, or mismatched case is a
    ControllerError and nothing is submitted; ``MATCLAW_CASE`` env is blocked;
  * every case-specific fact (remote root, run-id prefix, Slurm template, job
    name prefix, expected solution dir) comes ONLY from the fixed
    ``CASE_POLICIES`` entry for that case;
  * site specifics (SSH target, Slurm policy, frozen SIF path/SHA, node arch,
    time limits) come from the cluster profile (TOML) — never from the agent;
  * the six transport operations only — no arbitrary remote-shell field, no key
    path, no password, no raw SSH option exposed to the agent;
  * remote run ids are validated (reject ``..``, absolute paths, another
    case's prefix);
  * Slurm resources are pinned to the profile's partition/QoS/gres;
  * ``--env`` is limited to the MATCLAW_PROFILE / MATCLAW_SEED /
    MATCLAW_OUTPUT allow-list;
  * paper/smoke submit requires a valid formal runtime lock plus a matching
    qualification receipt (shared GPU runtime);
  * fetch is permitted only after sacct COMPLETED, and only for this case's
    run ids (cross-case fetch is rejected);
  * the controller never invokes the scientific verifier and never creates a
    reward — an ``infrastructure_failed`` job is classified on the node and the
    verifier path is not reachable from the controller.

The CLI request/response JSON carries run ids, canonical states, log tails, and
artifact hashes only.

python3.11-compatible, standard library only (mirrors slurm_transport.py).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# The transport lives next to this file inside the controller image and as a
# package in the repo tree (for the local test suite).  Try both.
try:  # repo layout: scripts.ablation.transport.slurm_transport
    from scripts.ablation.transport.slurm_transport import (  # type: ignore[import-not-found]
        JobState,
        SshConfig,
        SshSlurmTransport,
        SubmitOpts,
        TransportError,
    )
except ImportError:  # controller image layout: sibling slurm_transport.py
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from slurm_transport import (  # type: ignore[import-not-found]
        JobState,
        SshConfig,
        SshSlurmTransport,
        SubmitOpts,
        TransportError,
    )

# Cluster profile: site-specific HPC facts (TOML, stdlib ``tomllib``).  Same
# repo-layout / image-layout fallback as the transport import above.
try:  # repo layout: scripts.cluster_profile
    from scripts.cluster_profile import (  # type: ignore[import-not-found]
        DEFAULT_PROFILE,
        ProfileError,
        load_profile,
        validate_profile,
    )
except ImportError:  # controller image layout: sibling cluster_profile.py
    from cluster_profile import (  # type: ignore[import-not-found]
        DEFAULT_PROFILE,
        ProfileError,
        load_profile,
        validate_profile,
    )

__version__ = "0.2.0"

RUN_KINDS = ("probe", "smoke", "paper")

# Environment keys the agent may export into the batch script.  The sandbox env
# is harness-fixed; only these are passed through as apptainer ``--env`` flags.
_ALLOWED_ENV_KEYS = frozenset({"MATCLAW_PROFILE", "MATCLAW_SEED", "MATCLAW_OUTPUT"})

# Security-critical facts must NEVER come from the agent-controllable process
# env (the agent spawns this CLI as a child process, so its environment is
# agent-controlled).  Every fixed fact — SIF identity, solution dir, lock and
# receipt paths, and the active case itself — comes from a read-only mount.
_TRUSTED_ENV_BLOCKLIST = frozenset({
    "MATCLAW_SIF",
    "MATCLAW_LOCKED_SIF_SHA",
    "MATCLAW_SOLUTION_DIR",
    "MATCLAW_RUNTIME_LOCK",
    "MATCLAW_QUALIFICATION_RECEIPT",
    "MATCLAW_CASE",
})

# Fixed read-only mounts the harness provides inside the controller container.
# The agent cannot choose any of them; a missing mount fails closed.
CASE_POLICY_MOUNT = "/run/dftworld/case-policy.json"
RUNTIME_LOCK_MOUNT = "/run/dftworld/runtime-lock.json"
QUALIFICATION_RECEIPT_MOUNT = "/run/dftworld/qualification-receipt.json"

_HPC_DIR = Path(__file__).resolve().parent / "hpc"

# Job ids are scheduler-assigned positive integers.
_JOB_ID_RE = re.compile(r"^\d+$")
# A remote run id is a single, safe path component under the case's prefix.
_CASE_ROOT_NAME_RE = re.compile(r"^matclaw-\d{3}$")

# Sentinel distinguishing "argument not provided" from an explicit ``None``.
# ``matclaw_sif=None`` / ``locked_sif_sha=None`` mean "deliberately absent"
# (render then fails closed); the default is resolved from the runtime lock at
# paper/smoke submit (F2), never from env or the profile.
_UNSET = object()

_PARTITION_RE = re.compile(r"^#SBATCH\s+--partition=(\S+)", re.MULTILINE)
_GRES_RE = re.compile(r"^#SBATCH\s+--gres=(\S+)", re.MULTILINE)


class ControllerError(RuntimeError):
    """Raised when a restricted controller operation is rejected or fails."""


@dataclass(frozen=True)
class CasePolicy:
    """Immutable, per-case controller facts (Cases 031/032/033).

    Every value is fixed in code and NEVER taken from an agent argument.  The
    ``remote_root`` is the canonical root for the the HPC site site; a deployment
    with a different shared parent overrides it through the cluster profile
    (see :meth:`MatClawHpcController._resolve_remote_root`).  ``solution_dir``
    is the fixed remote path the job expects to bind at ``/solution``.
    """

    case_id: str
    run_id_prefix: str
    remote_root: str
    job_prefix: str
    solution_dir: str
    slurm_template: str  # filename under scripts/hpc/
    allowed_run_kinds: Tuple[str, ...] = RUN_KINDS


CASE_POLICIES: Dict[str, CasePolicy] = {
    "031": CasePolicy(
        case_id="031",
        run_id_prefix="031-",
        remote_root="/public/home/<site-user>/dftworld2-runs/matclaw-031",
        job_prefix="matclaw-031",
        solution_dir="/public/home/<site-user>/dftworld2-runs/matclaw-031/_solution",
        slurm_template="031_matclaw_gpu.slurm",
    ),
    "032": CasePolicy(
        case_id="032",
        run_id_prefix="032-",
        remote_root="/public/home/<site-user>/dftworld2-runs/matclaw-032",
        job_prefix="matclaw-032",
        solution_dir="/public/home/<site-user>/dftworld2-runs/matclaw-032/_solution",
        slurm_template="032_matclaw_gpu.slurm",
    ),
    "033": CasePolicy(
        case_id="033",
        run_id_prefix="033-",
        remote_root="/public/home/<site-user>/dftworld2-runs/matclaw-033",
        job_prefix="matclaw-033",
        solution_dir="/public/home/<site-user>/dftworld2-runs/matclaw-033/_solution",
        slurm_template="033_matclaw_gpu.slurm",
    ),
}

# Backward-compatible legacy 031 remote root (the current the site site value that
# DEFAULT_PROFILE mirrors) — kept so existing tooling/tests keep their root.
REMOTE_ROOT = CASE_POLICIES["031"].remote_root


def sha256_file(path: Union[str, Path]) -> str:
    """SHA-256 of a file's bytes (streamed, 1 MiB chunks)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class MatClawHpcController:
    """Restricted controller wrapping ``SshSlurmTransport`` for Cases 031-033.

    All remote paths are confined below the case's remote root.  The agent
    interacts with exactly six operations; nothing else is reachable.  The
    active case is fixed by the mounted ``case-policy.json`` (F3) and looked up
    in the immutable ``CASE_POLICIES`` table — the caller never picks roots,
    mounts, Slurm directives, or commands.
    """

    def __init__(
        self,
        *,
        case_id: Optional[str] = None,
        transport: Optional[SshSlurmTransport] = None,
        remote_root: Optional[str] = None,
        workspace: Union[str, os.PathLike[str]] = "/app",
        matclaw_sif: Union[str, None, object] = _UNSET,
        solution_dir: Optional[str] = None,
        expected_node_arch: Optional[str] = None,
        locked_sif_sha: Union[str, None, object] = _UNSET,
        runtime_lock_path: Optional[Union[str, os.PathLike[str]]] = None,
        qualification_receipt_path: Optional[Union[str, os.PathLike[str]]] = None,
        case_policy_path: Optional[Union[str, os.PathLike[str]]] = None,
        profile: Optional[Union[str, os.PathLike[str], Dict[str, Any]]] = None,
    ) -> None:
        # F1/F3: no security-critical fact may come from the agent-controllable
        # process env.  The agent spawns this CLI as a child process, so its
        # environment is agent-controlled — fail closed here, before anything is
        # resolved, if any blocked env var is set (even to the "correct" value:
        # the fixed read-only mounts are the only authoritative source).
        self._reject_trusted_env()
        # F3: the active case is fixed by the mounted case-policy document, never
        # by MATCLAW_CASE env (blocked above) and never by a caller value.
        self.case_policy_path = (
            str(case_policy_path)
            if case_policy_path is not None
            else CASE_POLICY_MOUNT
        )
        self._policy = self._resolve_case(case_id)
        self.case_id = self._policy.case_id
        # Site-specific HPC facts come from the cluster profile (dict, or a path
        # to a TOML profile file).  Explicit constructor args and MATCLAW_* env
        # vars still override it (env is highest precedence for SIF/identity).
        self.profile = self._resolve_profile(profile)
        p = self.profile
        self.remote_root = self._resolve_remote_root()
        if remote_root is not None and remote_root.rstrip("/") != self.remote_root:
            raise ControllerError(
                f"remote_root is fixed by Case {self._policy.case_id} policy + "
                f"cluster profile ({self.remote_root!r}); caller-supplied "
                f"{remote_root!r} rejected"
            )
        self.workspace = str(workspace)
        self.slurm_template = self._policy_template()
        if matclaw_sif is not _UNSET:
            self.matclaw_sif = matclaw_sif  # type: ignore[assignment]
        else:
            # F2: SIF identity is the runtime lock's single truth, resolved at
            # paper/smoke submit; None until the lock is loaded and validated.
            self.matclaw_sif = None
        if solution_dir is not None and solution_dir != self._policy.solution_dir:
            raise ControllerError(
                f"solution_dir is fixed by Case {self._policy.case_id} policy "
                f"({self._policy.solution_dir!r}); caller-supplied "
                f"{solution_dir!r} rejected"
            )
        self.solution_dir = solution_dir or self._policy.solution_dir
        # The active lock's SIF SHA, rendered into the job script so the job
        # itself verifies the SIF it runs is the locked one.  Any mismatch is
        # infrastructure_failed (never an agent/solution failure).
        if locked_sif_sha is not _UNSET:
            self.locked_sif_sha = locked_sif_sha  # type: ignore[assignment]
        else:
            # F2: the locked SIF SHA comes from the runtime lock, resolved at
            # paper/smoke submit; None until then.
            self.locked_sif_sha = None
        # ``uname -m`` value the cluster must report for the locked SIF to run.
        # The lock records the image platform (linux/amd64); the node reports
        # its CPU arch (x86_64).  Mismatch ⇒ the image would not execute there.
        self.expected_node_arch = (
            expected_node_arch or p["runtime"]["expected_node_arch"]
        )
        # Fixed paths for the shared GPU runtime lock and the fetched A100
        # qualification receipt (paper/smoke gate).  Harness-fixed; the agent
        # cannot choose them.
        # F2: the harness mounts the shared runtime lock and qualification
        # receipt at these fixed read-only paths inside the controller
        # container.  The agent cannot choose them; tests inject their own.
        self.runtime_lock_path = (
            str(runtime_lock_path)
            if runtime_lock_path is not None
            else "/run/dftworld/runtime-lock.json"
        )
        self.qualification_receipt_path = (
            str(qualification_receipt_path)
            if qualification_receipt_path is not None
            else "/run/dftworld/qualification-receipt.json"
        )
        # Per-case run-id regex: a single safe component under the case prefix.
        self._run_id_re = re.compile(
            rf"^{re.escape(self._policy.run_id_prefix)}"
            r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,63})$"
        )
        self._transport = transport or self._build_transport()
        self._jobs: Dict[str, str] = {}  # remote_run_id -> job_id
        self._staged: Dict[str, str] = {}  # remote_run_id -> remote run dir

    # -- case / profile resolution ---------------------------------------------

    def _resolve_case(self, case_id: Optional[str]) -> CasePolicy:
        """Resolve the fixed case policy.

        An explicit ``case_id`` is the unit-test seam only — ``main()`` never
        passes it, so the production CLI resolves the active case exclusively
        from the mounted ``case-policy.json`` (F3).  The mounted document is
        strictly cross-checked against the immutable in-image ``CASE_POLICIES``;
        a missing, unknown, or mismatched document fails closed before any
        operation can run.
        """
        if case_id is None:
            self._load_case_policy_mount()
            case_id = self._case_policy_doc["case_id"]
        if not case_id:
            raise ControllerError(
                "no MatClaw case configured: the case-policy mount is missing "
                "its case_id"
            )
        try:
            return CASE_POLICIES[case_id]
        except KeyError:
            raise ControllerError(
                f"unknown MatClaw case {case_id!r}; expected one of "
                f"{sorted(CASE_POLICIES)}"
            ) from None

    def _load_case_policy_mount(self) -> None:
        """Read and strictly validate the mounted case-policy document.

        Stores the validated document on ``self._case_policy_doc``.  The mount
        pins the active case; the image-frozen ``CASE_POLICIES`` entry is what
        the controller actually uses, so the document's case facts must match it
        exactly — a stale or tampered document fails closed.
        """
        path = Path(self.case_policy_path)
        if not path.is_file():
            raise ControllerError(
                f"case-policy mount not found: {self.case_policy_path}; "
                "the controller cannot determine the active case"
            )
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ControllerError(
                f"case-policy mount unreadable ({path}): {exc}"
            ) from exc
        if not isinstance(doc, dict):
            raise ControllerError("case-policy mount must be a JSON object")
        case_id = doc.get("case_id")
        if not isinstance(case_id, str) or case_id not in CASE_POLICIES:
            raise ControllerError(
                f"case-policy mount names unknown case {case_id!r}; expected "
                f"one of {sorted(CASE_POLICIES)}"
            )
        policy = CASE_POLICIES[case_id]
        for key, value in {
            "case_id": policy.case_id,
            "run_id_prefix": policy.run_id_prefix,
            "solution_dir": policy.solution_dir,
            "slurm_template": policy.slurm_template,
            "job_prefix": policy.job_prefix,
        }.items():
            if doc.get(key) != value:
                raise ControllerError(
                    f"case-policy mount {path} mismatches the immutable policy "
                    f"for case {case_id}: {key}={doc.get(key)!r} != {value!r}"
                )
        self._case_policy_doc = doc

    def _resolve_remote_root(self) -> str:
        """Per-case remote root: profile site root + the case's immutable suffix.

        The cluster profile's ``paths.remote_root`` is the site root.  If it
        already names the active case (the legacy canonical ``.../matclaw-031``
        layout) it is used as-is; if it names a different case it is stripped to
        its parent; otherwise it is treated as a shared base.  The immutable
        ``matclaw-{case}`` suffix is then appended — the agent never chooses a
        root.
        """
        site = str(self.profile["paths"]["remote_root"]).rstrip("/")
        expected = f"matclaw-{self._policy.case_id}"
        if Path(site).name == expected:
            return site
        if _CASE_ROOT_NAME_RE.match(Path(site).name):
            # The profile names another case's root; its parent is the base.
            site = str(Path(site).parent)
        return f"{site.rstrip('/')}/{expected}"

    def _policy_template(self) -> Path:
        """The repository-fixed Slurm template for this case."""
        path = Path(self._policy.slurm_template)
        if not path.is_absolute():
            path = _HPC_DIR / path
        return path

    @staticmethod
    def _resolve_profile(
        profile: Optional[Union[str, os.PathLike[str], Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Normalize ``profile`` to a validated dict (default if None).

        Accepts a TOML path (str/Path — loaded via ``load_profile``) or an
        already-built dict (schema-validated).  Missing/invalid profiles fail
        fast with a clear ``ProfileError`` rather than a silent fallback.
        """
        if profile is None:
            return DEFAULT_PROFILE
        if isinstance(profile, dict):
            return validate_profile(profile)
        return load_profile(profile)

    # -- transport construction ------------------------------------------------

    def _build_transport(self) -> SshSlurmTransport:
        """Build the scheduler transport for the controller.

        When the trusted gateway env (``BENCH_HPC_GATEWAY_URL`` /
        ``BENCH_HPC_RUN_TOKEN``) is present, return a
        :class:`GatewaySlurmTransport` that drives the remote Slurm through the
        host-side gateway over HTTP — the controller image carries no SSH
        client, rsync, key, or config.  Without those env vars (unit tests,
        direct CLI use), fall back to the SSH transport built from the cluster
        profile so the same controller code serves both paths.
        """
        try:
            from scripts.matclaw_hpc_gateway import GatewaySlurmTransport  # type: ignore[import-not-found]
        except ImportError:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from matclaw_hpc_gateway import GatewaySlurmTransport  # type: ignore[import-not-found]

        gateway = GatewaySlurmTransport.from_env()
        if gateway is not None:
            return gateway
        ssh_p = self.profile["ssh"]
        ssh = SshConfig(
            host=ssh_p["host"],
            user=ssh_p["user"] or None,
            port=ssh_p["port"] or None,
            options=dict(ssh_p["options"]),
        )
        return SshSlurmTransport(
            ssh=ssh,
            workspace=self.workspace,
            remote_workspace=self.remote_root,
            sync=self.profile["runtime"]["sync_strategy"],
        )

    # -- validation ------------------------------------------------------------

    def _validate_run_id(self, remote_run_id: str) -> str:
        if not isinstance(remote_run_id, str) or not remote_run_id:
            raise ControllerError("remote_run_id must be a non-empty string")
        if ".." in remote_run_id:
            raise ControllerError(
                f"remote_run_id must not contain '..': {remote_run_id!r}"
            )
        if remote_run_id.startswith("/") or remote_run_id.startswith("~"):
            raise ControllerError(
                f"remote_run_id must be relative: {remote_run_id!r}"
            )
        prefix = self._policy.run_id_prefix
        if not remote_run_id.startswith(prefix):
            raise ControllerError(
                f"remote_run_id must be a Case {self._policy.case_id} run "
                f"(prefix {prefix!r}): {remote_run_id!r}"
            )
        if not self._run_id_re.match(remote_run_id):
            raise ControllerError(
                f"remote_run_id has invalid characters: {remote_run_id!r}"
            )
        # Path confinement: even though the id is a single safe component, verify
        # the resolved remote path stays below the configured remote root.
        resolved = (Path(self.remote_root) / remote_run_id).resolve()
        root = Path(self.remote_root).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            raise ControllerError(
                f"remote_run_id escapes the remote root: {remote_run_id!r}"
            )
        return remote_run_id

    def _validate_run_kind(self, run_kind: str) -> str:
        if not isinstance(run_kind, str) or run_kind not in self._policy.allowed_run_kinds:
            raise ControllerError(
                f"run_kind must be one of {self._policy.allowed_run_kinds}, "
                f"got {run_kind!r}"
            )
        return run_kind

    def _validate_env_keys(self, env: Optional[Dict[str, str]]) -> None:
        """Fail closed unless every env key is on the fixed allow-list."""
        bad = sorted(key for key in (env or {}) if key not in _ALLOWED_ENV_KEYS)
        if bad:
            raise ControllerError(
                f"--env keys must be one of {sorted(_ALLOWED_ENV_KEYS)}; "
                f"got {bad}"
            )

    def _reject_trusted_env(self) -> None:
        """Fail closed if any security-critical fact is supplied by env.

        The agent spawns this CLI as a child process, so its environment is
        agent-controlled.  None of the fixed facts (SIF, SIF SHA, solution dir,
        and — after F2/F3 — runtime lock, qualification receipt, case id) may
        come from that env.  Even an env value equal to the fixed one is
        rejected: the fixed read-only source is authoritative, and presence
        alone signals a stale or leaky caller.
        """
        present = sorted(n for n in _TRUSTED_ENV_BLOCKLIST if os.environ.get(n))
        if present:
            raise ControllerError(
                "security-critical env must not be set on the controller "
                f"process: {present} (facts come from fixed read-only mounts)"
            )

    def _check_node_arch(self, run_kind: str) -> None:
        """Pre-submit arch gate: the cluster must run the locked SIF's arch.

        ``probe`` is self-contained (no SIF), so it skips the gate.  ``smoke``
        and ``paper`` carry the locked SIF; comparing before sbatch catches an
        un-runnable image (arm64 SIF vs amd64 cluster) instead of failing on
        the node.  A failed or unanswerable arch check fails closed.
        """
        if run_kind == "probe":
            return
        try:
            actual = self._transport.remote_arch()
        except TransportError as exc:
            raise ControllerError(
                f"cannot verify cluster arch before {run_kind} submit: {exc}"
            ) from exc
        if not actual or actual != self.expected_node_arch:
            raise ControllerError(
                f"cluster arch {actual!r} != expected {self.expected_node_arch!r}; "
                f"locked SIF would not run there — refusing to submit {run_kind}"
            )

    def _validate_job_id(self, job_id: str) -> str:
        job_id = str(job_id)
        if not _JOB_ID_RE.match(job_id):
            raise ControllerError(f"job_id must be a scheduler integer: {job_id!r}")
        return job_id

    # -- shared GPU runtime lock (paper/smoke gate; fails closed) ---------------

    def _import_runtime_lock(self) -> Tuple[Any, Any, Any]:
        """Import the fail-closed runtime-lock validator (repo or image layout).

        Deferred so a missing module in the controller image fails paper/smoke
        submit closed but never breaks ``probe``/``stage``/``status``/etc.
        """
        try:
            from scripts.matclaw_runtime_lock import (  # type: ignore[import-not-found]
                load_runtime_lock,
                validate_qualification,
                validate_runtime_lock,
            )
            return load_runtime_lock, validate_qualification, validate_runtime_lock
        except ImportError:
            try:
                from matclaw_runtime_lock import (  # type: ignore[import-not-found]
                    load_runtime_lock,
                    validate_qualification,
                    validate_runtime_lock,
                )
                return load_runtime_lock, validate_qualification, validate_runtime_lock
            except ImportError:
                raise ControllerError(
                    "matclaw_runtime_lock module unavailable in the controller "
                    "image; paper/smoke submit is impossible until it is added"
                ) from None

    def _check_runtime_lock(self, run_kind: str) -> None:
        """Gate paper/smoke submit on the shared formal runtime lock.

        ``probe`` is self-contained and never requires the lock.  ``paper``/
        ``smoke`` must pass ``validate_runtime_lock(require_formal=True)`` AND
        the fetched A100 qualification receipt must match the lock via
        ``validate_qualification`` — otherwise the submit fails closed before
        sbatch (the lock is issued only after the A100 qualification passes).
        """
        if run_kind == "probe":
            return
        load_runtime_lock, validate_qualification, validate_runtime_lock = (
            self._import_runtime_lock()
        )
        lock_path = self.runtime_lock_path or "/run/dftworld/runtime-lock.json"
        if not Path(lock_path).is_file():
            raise ControllerError(
                f"runtime lock not found: {lock_path}; paper/smoke submit is "
                "impossible until the A100 qualification issues the lock"
            )
        try:
            lock = load_runtime_lock(lock_path)
        except Exception as exc:
            raise ControllerError(f"cannot load runtime lock {lock_path}: {exc}") from exc
        errors = validate_runtime_lock(lock, require_formal=True)
        if errors:
            raise ControllerError(
                f"runtime lock ineligible for {run_kind}: " + "; ".join(errors)
            )
        receipt = self._load_qualification_receipt(lock)
        receipt_errors = validate_qualification(lock, receipt)
        if receipt_errors:
            raise ControllerError(
                f"qualification receipt does not match the locked SIF: "
                + "; ".join(receipt_errors)
            )
        # F2: the validated lock is the single truth for the SIF path + SHA;
        # _render_slurm resolves them from here when not caller-injected.
        self._active_lock = lock

    def _load_qualification_receipt(self, lock: Dict[str, Any]) -> Dict[str, Any]:
        """The A100 qualification result for the shared GPU runtime.

        Prefers the receipt mounted at the fixed harness path (F2) when present;
        otherwise uses the receipt embedded in the lock itself (the lock's
        ``formal_eligible`` gate already requires a valid embedded receipt, so
        the two agree by construction).  Missing/unreadable fails closed.
        """
        path = self.qualification_receipt_path
        if path and Path(path).is_file():
            rp = Path(path)
            try:
                receipt = json.loads(rp.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                raise ControllerError(
                    f"qualification receipt unreadable ({rp}): {exc}"
                ) from exc
            if not isinstance(receipt, dict):
                raise ControllerError("qualification receipt must be a JSON object")
            return receipt
        embedded = lock.get("qualification")
        if isinstance(embedded, dict):
            return embedded
        raise ControllerError(
            "no qualification receipt available: the fixed mount "
            f"{path!r} is absent and the runtime lock embeds none"
        )

    # -- Slurm policy (profile-driven; fail closed) ---------------------------

    def _slurm_opts(self) -> SubmitOpts:
        """Slurm policy from the cluster profile: partition/gres/nodes/cpus.

        ``%j`` logs land in the remote submit dir (remote_root/.matclaw).  The
        controller never accepts caller-supplied partition/GPU counts.
        """
        s = self.profile["slurm"]
        return SubmitOpts(
            partition=s["partition"],
            gres=s["gres"],
            cpus_per_task=str(s["cpus_per_task"]),
            nodes=str(s["nodes"]),
            output="slurm-%j.out",
            error="slurm-%j.out",
            chdir=f"{self.remote_root}/.matclaw",
        )

    def _assert_slurm_policy(self, slurm_text: str) -> None:
        """Fail closed unless every directive pins the profile's partition/gres.

        The rendered script must match the profile exactly — an agent cannot
        switch the partition or GPU count behind the profile's back.
        """
        s = self.profile["slurm"]
        partitions = _PARTITION_RE.findall(slurm_text)
        if partitions != [s["partition"]]:
            raise ControllerError(
                f"Slurm policy requires --partition={s['partition']} "
                f"(got {partitions!r})"
            )
        gres = _GRES_RE.findall(slurm_text)
        if gres != [s["gres"]]:
            raise ControllerError(
                f"Slurm policy requires exactly --gres={s['gres']} (got {gres!r})"
            )

    # -- script rendering ------------------------------------------------------

    @staticmethod
    def _render_env_flags(env: Optional[Dict[str, str]]) -> str:
        """Render apptainer ``--env KEY='VALUE'`` flags (empty if none).

        Host ``export`` lines never reach the container under ``--containall``
        (it implies ``--cleanenv``); the job's solve.sh must receive its
        profile/seed through explicit ``--env`` flags instead.  Keys are sorted
        for determinism and values shell-quoted; only allow-listed env keys are
        accepted upstream (see the submit CLI), so nothing here can smuggle a
        shell directive into the job script.
        """
        if not env:
            return ""
        return " ".join(
            f"--env {key}={shlex.quote(str(value))}"
            for key, value in sorted(env.items())
        )

    def _render_slurm(
        self,
        run_dir: str,
        env: Optional[Dict[str, str]] = None,
        run_kind: str = "paper",
    ) -> str:
        """Render the fixed Apptainer entry point for a paper/smoke run.

        SBATCH directives, the apptainer path, the node-arch gate, and the time
        limit all come from the cluster profile (run_kind selects
        time_paper / time_smoke); the job name comes from the case policy.  Any
        template placeholder left unresolved is an error — never a
        silently-skipped directive.
        """
        # F2: SIF identity is the runtime lock's single truth.  A caller-injected
        # value (unit-test seam) wins; otherwise the validated lock resolved in
        # ``_check_runtime_lock`` supplies the SIF path and its SHA.
        lock = getattr(self, "_active_lock", None) or {}
        matclaw_sif = self.matclaw_sif or lock.get("sif_path_remote")
        locked_sif_sha = self.locked_sif_sha or lock.get("sif_sha256")
        if not matclaw_sif or not self.solution_dir:
            raise ControllerError(
                "submit paper/smoke requires MATCLAW_SIF and MATCLAW_SOLUTION_DIR "
                "to be configured (or a validated runtime lock providing the SIF)"
            )
        if not locked_sif_sha:
            raise ControllerError(
                "submit paper/smoke requires the locked SIF SHA "
                "(MATCLAW_LOCKED_SIF_SHA); the job verifies it at start"
            )
        if not self.slurm_template.is_file():
            raise ControllerError(f"slurm template missing: {self.slurm_template}")
        s = self.profile["slurm"]
        time_limit = s.get(f"time_{run_kind}", s["time_paper"])
        text = self.slurm_template.read_text(encoding="utf-8")
        text = (
            text.replace("__RUN_DIR__", shlex.quote(str(run_dir)))
            .replace("__SOLUTION_DIR__", shlex.quote(str(self.solution_dir)))
            .replace("__MATCLAW_SIF__", shlex.quote(str(matclaw_sif)))
            .replace("__MATCLAW_LOCKED_SIF_SHA__", locked_sif_sha)
            .replace("__MATCLAW_ENV_FLAGS__", self._render_env_flags(env))
            .replace(
                "__SBATCH_JOB_NAME__",
                f"{self._policy.job_prefix}-{run_kind}",
            )
            .replace("__SBATCH_ACCOUNT__", s["account"])
            .replace("__SBATCH_PARTITION__", s["partition"])
            .replace("__SBATCH_QOS__", s["qos"])
            .replace("__SBATCH_GRES__", s["gres"])
            .replace("__SBATCH_CPUS__", str(s["cpus_per_task"]))
            .replace("__SBATCH_MEM__", s["mem"])
            .replace("__SBATCH_TIME__", time_limit)
            .replace(
                "__APPTAINER_PATH__",
                shlex.quote(self.profile["paths"]["apptainer"]),
            )
            .replace(
                "__EXPECTED_NODE_ARCH__",
                self.profile["runtime"]["expected_node_arch"],
            )
        )
        if any(tok in text for tok in (
            "__RUN_DIR__", "__SOLUTION_DIR__", "__MATCLAW_SIF__",
            "__MATCLAW_LOCKED_SIF_SHA__", "__MATCLAW_ENV_FLAGS__",
            "__SBATCH_JOB_NAME__", "__SBATCH_ACCOUNT__", "__SBATCH_PARTITION__",
            "__SBATCH_QOS__", "__SBATCH_GRES__", "__SBATCH_CPUS__",
            "__SBATCH_MEM__", "__SBATCH_TIME__", "__APPTAINER_PATH__",
            "__EXPECTED_NODE_ARCH__",
        )):
            raise ControllerError("slurm template left an unresolved placeholder")
        self._assert_slurm_policy(text)
        return text

    def _render_probe_script(self) -> str:
        """The one-minute GPU canary batch entry point (Step 7).

        Every job pins account + partition + QoS + gres + time from the cluster
        profile; these directives are the site policy and must not be dropped.
        """
        s = self.profile["slurm"]
        return f"""#!/bin/bash
#SBATCH --job-name=matclaw-{self._policy.case_id}-probe
#SBATCH --account={s['account']}
#SBATCH --partition={s['partition']}
#SBATCH --qos={s['qos']}
#SBATCH --gres={s['gres']}
#SBATCH --cpus-per-task={s['cpus_per_task']}
#SBATCH --mem={s['mem']}
#SBATCH --time={s['time_probe']}
#SBATCH --output=slurm-%j.out
set -euo pipefail
echo "hostname=$(hostname)"
echo "CUDA_VISIBLE_DEVICES=${{CUDA_VISIBLE_DEVICES:-<unset>}}"
nvidia-smi --query-gpu=name --format=csv,noheader | head -n1
"""

    def _render_probe_slurm(self) -> str:
        text = self._render_probe_script()
        self._assert_slurm_policy(text)
        return text

    # -- the six restricted operations ------------------------------------------

    def stage(self, local_run: Path, remote_run_id: str) -> str:
        """Checksum-stage a local run tree up to the HPC run root.

        Returns the remote path of the staged run (the RUN_DIR for submit).
        """
        self._validate_run_id(remote_run_id)
        local_run = Path(local_run).resolve()
        if not local_run.is_dir():
            raise ControllerError(f"local run dir does not exist: {local_run}")
        remote_paths = self._transport.stage([str(local_run)], remote_run_id)
        if not remote_paths:
            raise ControllerError(f"stage returned no remote path for {remote_run_id!r}")
        remote_run = remote_paths[0]
        self._staged[remote_run_id] = remote_run
        return remote_run

    def submit(self, remote_run_id: str, run_kind: str,
               env: Optional[Dict[str, str]] = None) -> str:
        """Stage the fixed batch script and submit to Slurm; return the job id.

        ``run_kind`` selects the script: ``probe`` is a self-contained GPU probe
        (no runtime lock needed); ``smoke``/``paper`` render the Apptainer entry
        point and require a prior ``stage()`` plus a valid formal runtime lock
        and a matching A100 qualification receipt.  ``env`` exports KEY=VALUE
        lines (only the MATCLAW_PROFILE/MATCLAW_SEED/MATCLAW_OUTPUT allow-list)
        into the batch script; ``probe`` ignores it (the canary is
        self-contained).
        """
        self._validate_run_id(remote_run_id)
        run_kind = self._validate_run_kind(run_kind)
        self._validate_env_keys(env)
        self._check_node_arch(run_kind)
        self._check_runtime_lock(run_kind)
        if run_kind == "probe":
            text = self._render_probe_slurm()
        else:
            run_dir = self._staged.get(remote_run_id)
            if run_dir is None:
                raise ControllerError(
                    f"stage() must precede submit() for remote_run_id {remote_run_id!r}"
                )
            text = self._render_slurm(run_dir, env, run_kind=run_kind)
        script_dir = Path(self.workspace) / ".matclaw"
        script_dir.mkdir(parents=True, exist_ok=True)
        local_script = script_dir / f"{remote_run_id}.slurm"
        local_script.write_text(text, encoding="utf-8")
        job_id = self._transport.submit(str(local_script), self._slurm_opts())
        self._jobs[remote_run_id] = job_id
        return job_id

    def status(self, job_id: str) -> JobState:
        """Canonical scheduler state; UNKNOWN is non-terminal (keep polling)."""
        job_id = self._validate_job_id(job_id)
        return self._transport.status(job_id)

    def log(self, job_id: str, tail: int = 200) -> str:
        """Best-effort merged stdout/stderr, tail-limited ("" when absent)."""
        job_id = self._validate_job_id(job_id)
        return self._transport.log(job_id, tail=int(tail))

    def cancel(self, job_id: str) -> None:
        """Idempotent cancellation; status() then converges to CANCELLED."""
        job_id = self._validate_job_id(job_id)
        self._transport.cancel(job_id)

    def fetch(self, remote_run_id: str, local_run: Path) -> Dict[str, str]:
        """Fetch a completed run back and verify artifact hashes vs the manifest.

        Permitted only after ``sacct`` reports COMPLETED for the job submitted
        against this remote_run_id.  Cross-case run ids are rejected by
        ``_validate_run_id``.  Returns ``{artifact_path: sha256}`` for the
        verified artifacts.  Raises on any hash mismatch or missing manifest.
        """
        self._validate_run_id(remote_run_id)
        job_id = self._jobs.get(remote_run_id)
        if job_id is None:
            raise ControllerError(
                f"no job tracked for remote_run_id {remote_run_id!r} "
                "(submit() must precede fetch())"
            )
        state = self._transport.status(job_id)
        if state is not JobState.COMPLETED:
            raise ControllerError(
                f"cannot fetch before COMPLETED (state={state.value})"
            )
        local_run = Path(local_run).resolve()
        local_run.mkdir(parents=True, exist_ok=True)
        remote_run = self._staged.get(remote_run_id)
        if remote_run is None:
            remote_run = f"{self.remote_root}/{remote_run_id}"
        self._transport.fetch([remote_run], str(local_run))
        # transport.fetch writes <local_run>/<remote-basename>/...; normalize the
        # run tree contents up into <local_run> so the caller's dir is the tree.
        fetched = local_run / Path(remote_run).name
        if fetched.is_dir() and fetched != local_run:
            for child in fetched.iterdir():
                dest = local_run / child.name
                if dest.exists():
                    if dest.is_dir():
                        import shutil as _shutil

                        _shutil.rmtree(dest)
                    else:
                        dest.unlink()
                child.replace(dest)
            fetched.rmdir()
        return self._verify_manifest(local_run)

    def _verify_manifest(self, run_tree: Path) -> Dict[str, str]:
        """Verify every declared artifact's SHA-256 against the remote manifest."""
        manifest_path = run_tree / "manifest.json"
        if not manifest_path.is_file():
            raise ControllerError(f"remote manifest missing after fetch: {manifest_path}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ControllerError(f"remote manifest unreadable: {exc}")
        entries = manifest.get("artifacts")
        if not isinstance(entries, list):
            raise ControllerError("remote manifest must declare an 'artifacts' list")
        verified: Dict[str, str] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ControllerError(f"manifest artifact entry is not an object: {entry!r}")
            rel = entry.get("path")
            expected = entry.get("sha256")
            if not isinstance(rel, str) or not isinstance(expected, str):
                raise ControllerError(f"manifest artifact entry malformed: {entry!r}")
            rel_path = Path(rel)
            if rel_path.is_absolute() or ".." in rel_path.parts:
                raise ControllerError(
                    f"manifest artifact path escapes the run tree: {rel!r}"
                )
            artifact = run_tree / rel_path
            if not artifact.is_file():
                raise ControllerError(
                    f"manifest artifact missing after fetch: {rel!r}"
                )
            actual = sha256_file(artifact)
            if actual != expected:
                raise ControllerError(
                    f"SHA-256 mismatch for {rel!r}: manifest={expected} fetched={actual}"
                )
            verified[rel] = actual
        return verified

    # -- polling helper (not part of the six-operation CLI surface) -------------

    def wait_for(
        self,
        job_id: str,
        poll: float = 10.0,
        timeout: Optional[float] = None,
    ) -> JobState:
        """Poll status() until a terminal state; UNKNOWN keeps waiting.

        A transient query failure (TransportError) also keeps waiting — never a
        terminal state.  A controller timeout cancels the job and raises, so an
        orphaned allocation is never left behind.
        """
        import time

        deadline = None if timeout is None else time.monotonic() + timeout
        last = JobState.UNKNOWN
        while True:
            try:
                last = self.status(job_id)
            except (ControllerError, TransportError):
                last = JobState.UNKNOWN
            if last.is_terminal:
                return last
            if deadline is not None and time.monotonic() >= deadline:
                self.cancel(job_id)
                raise TimeoutError(
                    f"job {job_id} not terminal after {timeout}s; "
                    f"cancellation requested (last={last.value})"
                )
            time.sleep(poll)


# --------------------------------------------------------------------------- #
# Restricted CLI
# --------------------------------------------------------------------------- #

_FLAGS: Dict[str, List[str]] = {
    "stage": ["--local-run", "--remote-run-id"],
    "submit": ["--remote-run-id", "--run-kind", "--env"],
    "status": ["--job-id"],
    "log": ["--job-id", "--tail"],
    "cancel": ["--job-id"],
    "fetch": ["--remote-run-id", "--local-run"],
}

# Flags that may repeat (e.g. --env KEY=VALUE) and are never required.
_REPEATABLE = frozenset({"--env"})


def _parse_flags(argv: List[str], allowed: List[str]) -> Dict[str, Union[str, List[str]]]:
    """Parse ``--key value`` pairs; reject positionals, ``--``, unknown flags.

    This is what keeps the surface free of arbitrary shell fields: an
    unexpected token is an error, never passed through to anything.
    ``_REPEATABLE`` flags collect every occurrence (as a list) and are never
    required; every other allowed flag must appear exactly once.
    """
    out: Dict[str, Union[str, List[str]]] = {}
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in allowed:
            if i + 1 >= len(argv):
                raise ControllerError(f"missing value for {tok}")
            value = argv[i + 1]
            if tok in _REPEATABLE:
                out.setdefault(tok, []).append(value)
            else:
                out[tok] = value
            i += 2
        else:
            raise ControllerError(f"unexpected argument: {tok!r}")
    for flag in allowed:
        if flag not in out:
            if flag in _REPEATABLE:
                out[flag] = []
            else:
                raise ControllerError(f"missing required flag {flag}")
    return out


def _parse_env_flags(env_list: List[str]) -> Dict[str, str]:
    """Validate ``KEY=VALUE`` pairs from repeatable --env flags into a dict."""
    env: Dict[str, str] = {}
    for pair in env_list:
        key, sep, value = pair.partition("=")
        if not sep or not key or not value or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ControllerError(
                f"--env must be KEY=VALUE with a plain non-empty env key (got {pair!r})"
            )
        env[key] = value
    return env


def _emit(payload: Dict[str, object], status: int = 0) -> int:
    print(json.dumps(payload, sort_keys=True))
    return status


def main(argv: Optional[List[str]] = None) -> int:
    """Run one restricted controller operation; always answers with JSON."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return _emit({"error": "usage: matclaw-hpc-controller <op> [--flag value ...]"}, status=2)
    op = argv[0]
    if op not in _FLAGS:
        return _emit(
            {"error": f"unsupported operation: {op!r}; allowed={sorted(_FLAGS)}"},
            status=2,
        )
    try:
        ctl = MatClawHpcController()
        flags = _parse_flags(argv[1:], _FLAGS[op])
        if op == "stage":
            remote = ctl.stage(Path(flags["--local-run"]), flags["--remote-run-id"])
            return _emit({"remote_path": remote})
        if op == "submit":
            env = _parse_env_flags(flags["--env"] or [])
            job_id = ctl.submit(flags["--remote-run-id"], flags["--run-kind"], env=env)
            return _emit({"job_id": job_id})
        if op == "status":
            state = ctl.status(flags["--job-id"])
            return _emit({"state": state.value})
        if op == "log":
            tail = int(flags.get("--tail") or 200)
            return _emit({"log": ctl.log(flags["--job-id"], tail=tail)})
        if op == "cancel":
            ctl.cancel(flags["--job-id"])
            return _emit({"cancelled": flags["--job-id"]})
        if op == "fetch":
            artifacts = ctl.fetch(flags["--remote-run-id"], Path(flags["--local-run"]))
            return _emit({"artifacts": artifacts})
    except ControllerError as exc:
        return _emit({"error": str(exc)}, status=1)
    except (ValueError, OSError) as exc:
        return _emit({"error": f"{type(exc).__name__}: {exc}"}, status=1)
    return _emit({"error": "internal error"}, status=1)


if __name__ == "__main__":
    raise SystemExit(main())
