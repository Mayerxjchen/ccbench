"""SlurmAdapter: map bench-hpc JobSpec onto a real Slurm site.

The adapter is the site-owned half of the trusted-gateway contract. Given a
validated JobSpec and a validated site config, it produces:

- typed scheduler directives (``SubmitOpts``) whose partition/account/gres come
  ONLY from the validated site config / platform profile — never from the
  Candidate, and
- a contained scientific script that launches the digest-pinned frozen runtime
  with ``--contain --cleanenv --no-home`` and binds only the declared
  input/output paths.

The JobSpec ``command`` is argv and is serialized with ``shlex.join`` — never
parsed, so a Candidate token like ``#SBATCH`` can never become a directive.
Remote workspaces are namespaced as
``<workspace_root>/<case_id>/<run_id>/<job_id>/``.

The legacy ``SshSlurmTransport`` is used only through this adapter, inside the
trusted gateway; Candidate-facing code never imports it.
"""

from __future__ import annotations

import json
import shlex
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
from referencing import Registry, Resource

from dftworld_bench.hpc.adapters.base import JOB_STATES
from scripts.ablation.transport.slurm_transport import JobState, SubmitOpts

_SCHEMAS_DIR = Path(__file__).resolve().parents[3] / "schemas"
_SITE_ID = "https://mlip-bench.example/schemas/site-config.schema.json"

_STATE = {
    JobState.PENDING: "QUEUED",
    JobState.RUNNING: "RUNNING",
    JobState.COMPLETED: "SUCCEEDED",
    JobState.FAILED: "FAILED",
    JobState.CANCELLED: "CANCELLED",
    JobState.UNKNOWN: "LOST",
}


class SlurmAdapterError(Exception):
    """A Slurm adapter contract rule was violated."""


@dataclass(frozen=True)
class RenderedJob:
    """One JobSpec rendered for the scheduler."""

    job_id: str
    workspace: str  # remote workspace <root>/<case_id>/<run_id>/<job_id>/
    script: str
    opts: SubmitOpts  # typed directives; the script body carries no #SBATCH


class SlurmAdapter:
    """HpcAdapter backed by the legacy Slurm transport."""

    validates_job_spec: bool = True  # Gateway runs typed JobSpec validation

    def __init__(
        self,
        site_config: dict[str, Any],
        transport: Any,
        *,
        case_id: str,
        resource_profile: dict[str, Any] | None = None,
        runtime_wrapper: Any = None,
        script_dir: str | Path | None = None,
    ) -> None:
        _validate_site_config(site_config)
        self._site = site_config
        self._transport = transport
        self._case_id = case_id
        # Optional local directory where rendered batch scripts are
        # materialized before transport.submit — file-oriented transports
        # (ssh/rsync) need a path, not the rendered text.
        self._script_dir = Path(script_dir) if script_dir is not None else None
        # Optional trusted runtime wrapper (Task 6): when set, the exec line
        # comes from render_runtime_wrapper — one rw run bind, sealed argv —
        # instead of the legacy per-input bind construction below.
        self._runtime_wrapper = runtime_wrapper
        platform = site_config["platform_profile"]
        queues = platform["queues"]
        self._profile = resource_profile or queues[0]
        self._jobs: dict[str, dict[str, Any]] = {}
        self._idem: dict[str, str] = {}
        self._ops: dict[tuple[str, str], str] = {}  # (run_id, operation_id) -> job_id
        self._markers: dict[str, str] = {}  # submit marker -> job_id
        self._seq = 0

    # -- the seven operations --------------------------------------------

    def capabilities(self) -> dict[str, Any]:
        platform = self._site["platform_profile"]
        return {
            "adapter": "slurm",
            "states": list(JOB_STATES),
            "queues": [q["name"] for q in platform["queues"]],
            "partition": platform["default_queue"],
            "gres": f"gpu:{self._profile['max_gpus']}",
            "supports_cancel": True,
        }

    def find_by_operation_id(self, run_id: str, operation_id: str) -> str | None:
        return self._ops.get((run_id, operation_id))

    def find_by_marker(self, marker: str) -> str | None:
        """Exact-match the opaque submit marker recorded at submission."""
        return self._markers.get(marker)

    def submit(
        self,
        spec: dict[str, Any],
        *,
        run_id: str,
        operation_id: str,
        marker: str | None = None,
    ) -> dict[str, Any]:
        if marker is not None and marker in self._markers:
            # Exact-marker replay of an already-accepted submission.
            return {"job_id": self._markers[marker], "duplicate": True}
        prior = self._ops.get((run_id, operation_id))
        if prior is not None and marker is None:
            # v1 single-job-per-operation semantics; v2 attempts carry an
            # explicit marker and are guarded by it instead.
            return {"job_id": prior, "duplicate": True}
        key = spec["idempotency_key"]
        if key in self._idem:
            return {"job_id": self._idem[key], "duplicate": True}
        self._seq += 1
        job_id = f"job-{self._seq:04d}"
        rendered = self.render(
            spec, job_id=job_id, run_id=run_id, operation_id=operation_id
        )
        script_ref: Any = rendered.script
        if self._script_dir is not None:
            self._script_dir.mkdir(parents=True, exist_ok=True)
            script_ref = self._script_dir / f"{job_id}.slurm"
            script_ref.write_text(rendered.script, encoding="utf-8")
        try:
            slurm_id = self._transport.submit(script_ref, rendered.opts)
        except Exception as exc:  # noqa: BLE001 — transport errors surface verbatim
            raise SlurmAdapterError(f"sbatch rejected job {job_id}: {exc}") from exc
        self._jobs[job_id] = {"slurm_id": slurm_id, "workspace": rendered.workspace}
        if marker is not None:
            self._markers[marker] = job_id
        self._idem[key] = job_id
        self._ops[(run_id, operation_id)] = job_id
        return {"job_id": job_id, "duplicate": False}

    def status(self, job_id: str) -> dict[str, Any]:
        state = self._map(self._transport.status(self._slurm_id(job_id)))
        # C4: reconcile AllocTRES against ReqTRES for GPU jobs — a MIG or
        # zero-GPU allocation after a full-GPU request must surface.
        alloc_tres = self._transport.alloc_tres(self._slurm_id(job_id))
        if alloc_tres:
            from dftworld_bench.hpc.tres import verify_full_gpu
            ok, reason = verify_full_gpu(
                req_tres=self._jobs[job_id].get("req_tres", "gpu:1"),
                alloc_tres=alloc_tres,
            )
            if not ok:
                state = "FAILED"
        return {"job_id": job_id, "state": state}

    def logs(self, job_id: str) -> dict[str, Any]:
        return {"job_id": job_id, "log": self._transport.log(self._slurm_id(job_id))}

    def fetch(self, job_id: str) -> dict[str, Any]:
        state = self._map(self._transport.status(self._slurm_id(job_id)))
        if state != "SUCCEEDED":
            raise SlurmAdapterError(f"fetch before success (state {state})")
        job = self._jobs[job_id]
        local = self._local_fetch_dir(job_id)
        local.mkdir(parents=True, exist_ok=True)
        self._transport.fetch([job["workspace"]], local)
        return {"job_id": job_id, "state": "SUCCEEDED", "files": sorted(p.name for p in local.iterdir())}

    def cancel(self, job_id: str) -> dict[str, Any]:
        self._transport.cancel(self._slurm_id(job_id))
        return {"job_id": job_id, "state": "CANCELLED"}

    def usage(self) -> dict[str, Any]:
        return {"jobs": len(self._jobs), "submitted": len(self._idem)}

    # -- rendering --------------------------------------------------------

    def render(
        self,
        spec: dict[str, Any],
        *,
        job_id: str,
        run_id: str | None,
        operation_id: str,
    ) -> RenderedJob:
        workspace = self._workspace(run_id, job_id)
        opts = self._directives(spec, workspace, job_id, run_id=run_id,
                                operation_id=operation_id)
        script = self._script(spec, workspace, operation_id=operation_id)
        return RenderedJob(job_id=job_id, workspace=workspace, script=script, opts=opts)

    def _workspace(self, run_id: str | None, job_id: str) -> str:
        root = self._site["scratch"].rstrip("/")
        run = run_id or "run"
        return f"{root}/{self._case_id}/{run}/{job_id}"

    def _directives(
        self,
        spec: dict[str, Any],
        workspace: str,
        job_id: str,
        *,
        run_id: str | None = None,
        operation_id: str = "",
    ) -> SubmitOpts:
        resources = spec["resources"]
        platform = self._site["platform_profile"]
        # C7: operation identity rides on the scheduler directive so a crashed
        # gateway can reconcile the run by operation id — always present.
        extra = (
            f"--comment=bench:{self._case_id}:{run_id or 'run'}:{operation_id}",
        )
        return SubmitOpts(
            job_name=f"{self._case_id}-{job_id}",
            cpus_per_task=str(resources["cpus"]),
            memory_per_node=f"{resources['memory_gb']}G",
            partition=platform["default_queue"],  # validated site config only
            qos=self._profile.get("qos"),  # from SiteProfile resolved resource
            # Typed resources drive gres: a zero-GPU request submits without
            # the flag instead of asking the scheduler for gpu:0.
            gres=(f"gpu:{self._profile['max_gpus']}"
                  if self._profile.get("max_gpus") else None),
            time=_hh_mm_ss(resources["walltime_minutes"]),
            output=f"{workspace}/stdout.log",
            error=f"{workspace}/stderr.log",
            chdir=workspace,
            account=self._site.get("account"),
            extra=extra,
        )

    def _script(
        self,
        spec: dict[str, Any],
        workspace: str,
        *,
        operation_id: str = "",
    ) -> str:
        if self._runtime_wrapper is not None:
            rendered = self._runtime_wrapper(spec, workspace)
            return rendered
        lines = ["#!/bin/bash", "set -euo pipefail", ""]
        # C7: remote marker — the job writes its operation identity into the
        # workspace so a reaper can attribute the job after the fact
        marker = f"{workspace}/.bench-operation-id"
        lines.append(
            f"printf '%s\\n' {shlex.quote(operation_id)} > {shlex.quote(marker)}"
        )
        argv = ["apptainer", "run", "--contain", "--cleanenv", "--no-home"]
        argv += ["--bind", f"{workspace}:/workspace:rw"]
        for raw in spec.get("inputs", []):
            if isinstance(raw, dict):
                # a bound input: source was contained by the gateway
                argv += ["--bind", f"{raw['source']}:{raw['destination']}:ro"]
            else:
                argv += ["--bind", f"{workspace}/{raw}:{raw}:ro"]
        for pattern in spec.get("outputs", []):
            prefix = _literal_prefix(pattern)
            if prefix:
                argv += ["--bind", f"{workspace}/{prefix}:/workspace-results"]
        argv += [spec["runtime"]]  # digest-pinned frozen runtime
        argv += list(spec["command"])  # argv, serialized verbatim
        lines.append(shlex.join(argv))
        return "\n".join(lines) + "\n"

    # -- internal ---------------------------------------------------------

    def _slurm_id(self, job_id: str) -> str:
        job = self._jobs.get(job_id)
        if job is None:
            raise SlurmAdapterError(f"unknown job {job_id!r}")
        return job["slurm_id"]

    @staticmethod
    def _map(state: JobState) -> str:
        return _STATE.get(state, "LOST")

    def _local_fetch_dir(self, job_id: str) -> Path:
        # remote results land in a gateway-local staging dir, never on the site
        # scratch (which may be read-only from the gateway) and never in-place
        return Path(tempfile.mkdtemp(prefix=f"slurm-fetch-{job_id}-"))


def _schema_store() -> dict[str, dict[str, Any]]:
    store: dict[str, dict[str, Any]] = {}
    for path in sorted(_SCHEMAS_DIR.glob("*.schema.json")):
        schema = json.loads(path.read_text(encoding="utf-8"))
        if schema.get("$id"):
            store[schema["$id"]] = schema
    return store


def _validate_site_config(site_config: dict[str, Any]) -> None:
    # Resolve local ``$ref``s (platform-profile -> resource-profile) from the
    # in-repo store; never reach out to a remote schema server.
    store = _schema_store()
    schema = store.get(_SITE_ID)
    if schema is None:
        raise SlurmAdapterError(f"site-config schema {_SITE_ID!r} not in store")
    registry = Registry().with_resources(
        (uid, Resource.from_contents(body)) for uid, body in store.items()
    )
    errors = sorted(
        jsonschema.Draft202012Validator(schema, registry=registry).iter_errors(site_config),
        key=lambda e: e.path,
    )
    if errors:
        first = errors[0]
        where = ".".join(str(p) for p in first.path) or "$"
        raise SlurmAdapterError(f"invalid site config at {where}: {first.message}")


def _hh_mm_ss(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}:00"


def _literal_prefix(pattern: str) -> str:
    """Leading literal of an output pattern, up to the first glob metachar."""
    for marker in ("*", "?", "["):
        pattern = pattern.split(marker, 1)[0]
    return pattern.rstrip("/")
