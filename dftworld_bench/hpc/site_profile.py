"""HpcSiteProfile: the trusted, immutable site policy identity.

Two projections fall out of one frozen dict:

- ``digest`` — sha256 over the canonical policy (sanitized target, account,
  queues/QOS, remote-root policy, runtime store). Credential bytes are never
  part of the profile, so rotating an equivalent key cannot change it.
- ``public_capabilities`` — what a Candidate may see: abstract resource
  classes and ceilings. Account/partition/QOS names, SSH target/user, remote
  root, and credential profile ID are stripped by construction.

The credential provider is injected separately by the trusted Harness and is
never serializable.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import jsonschema

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "schemas" / "hpc-site-profile.schema.json"
)

# Field names that must never appear anywhere in a profile payload.
_FORBIDDEN_KEYS = frozenset(
    {
        "ssh_private_key",
        "private_key",
        "secret",
        "password",
        "token",
        "api_key",
    }
)


class SiteProfileError(Exception):
    """A site profile violates the frozen contract."""


class SiteProfileBlockedError(SiteProfileError):
    """The live site ACL rejects the configured account on the target partition.

    Attributes:
        partition: The Slurm partition that rejected the account.
        account:   The account that was checked.
        reason:    Human-readable explanation (e.g. AllowAccounts whitelist).
    """

    def __init__(self, partition: str, account: str, reason: str) -> None:
        self.partition = partition
        self.account = account
        self.reason = reason
        super().__init__(
            f"BLOCKED_SITE_ACL: account {account!r} not allowed on "
            f"partition {partition!r}: {reason}"
        )


@dataclasses.dataclass(frozen=True)
class ResolvedResource:
    """Concrete scheduler parameters for one workload type.

    Produced by :meth:`HpcSiteProfile.resolve_workload`.  The account,
    partition, QOS and GRES ceiling come from the frozen profile and its
    resource mapping — never from the Candidate or Case.
    """

    account: str
    partition: str
    qos: str
    max_cpus: int
    max_memory_gb: int
    max_walltime_minutes: int
    max_gpus: int
    workload_type: str
    queue_name: str
    mapping_note: str = ""

    @property
    def gres(self) -> str | None:
        """GRES flag value derived from max_gpus; None for zero-GPU queues."""
        return f"gpu:{self.max_gpus}" if self.max_gpus else None


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclasses.dataclass(frozen=True)
class _QueuePolicy:
    partition: str
    qos: str
    max_cpus: int
    max_memory_gb: int
    max_walltime_minutes: int
    max_gpus: int = 0


@dataclasses.dataclass(frozen=True)
class HpcSiteProfile:
    """Immutable site policy; construct only via :meth:`from_dict`."""

    site_id: str
    scheduler: str
    connection: dict[str, str] = field(default_factory=dict)
    account: str = ""
    queues: dict[str, dict[str, Any]] = field(default_factory=dict)
    resource_mapping: dict[str, dict[str, Any]] = field(default_factory=dict)
    runtime_policy: dict[str, Any] = field(default_factory=dict)
    digest: str = ""

    @classmethod
    def from_cluster_config(cls, config: dict[str, Any]) -> "HpcSiteProfile":
        """Build from a ``cluster_profile.toml`` dict (legacy private config).

        Bridges the TOML format used by the qualification script and the
        MatClaw controller to the canonical SiteProfile schema.  When the
        profile declares ``slurm.cpu_partition``, cpu workloads route to that
        native queue (gpus=0) and gpu workloads to ``slurm.partition``; the
        ACL-era fallback of mapping cpu workloads onto the gpu queue is gone
        — without ``cpu_partition`` a cpu workload simply fails resolution
        (fail-closed), it never silently lands on GPUs.
        """
        slurm = config["slurm"]
        ssh = config["ssh"]
        paths = config["paths"]

        mem_str = slurm.get("mem", "64G")
        mem_gb = int(mem_str.rstrip("Gg"))
        time_paper = slurm.get("time_paper", "08:00:00")
        hh, mm, _ss = time_paper.split(":")
        walltime_minutes = int(hh) * 60 + int(mm)

        queues: dict[str, Any] = {
            "gpu": {
                "partition": slurm["partition"],
                "qos": slurm.get("qos", "normal"),
                "max_cpus": int(slurm.get("cpus_per_task", 8)),
                "max_memory_gb": mem_gb,
                "max_gpus": 1,
                "max_walltime_minutes": walltime_minutes,
            },
        }
        resource_mapping: dict[str, Any] = {"gpu": {"queue": "gpu"}}
        cpu_partition = slurm.get("cpu_partition")
        if cpu_partition:
            queues["cpu"] = {
                "partition": cpu_partition,
                "qos": slurm.get("qos", "normal"),
                "max_cpus": int(slurm.get("cpus_per_task", 8)),
                "max_memory_gb": mem_gb,
                "max_gpus": 0,
                "max_walltime_minutes": walltime_minutes,
            }
            resource_mapping["cpu"] = {"queue": "cpu"}

        payload: dict[str, Any] = {
            "schema_version": 1,
            "site_id": ssh["host"],
            "scheduler": "slurm",
            "connection": {
                "credential_profile_id": "cluster-profile-toml",
                "target_binding": (
                    f"ssh://{ssh['host']}:{int(ssh.get('port') or 22)}"
                ),
                "remote_user": ssh.get("user") or "<site-user>",
                "remote_root_policy": paths["remote_root"],
            },
            "account": slurm["account"],
            "queues": queues,
            "resource_mapping": resource_mapping,
            "runtime_policy": {
                "requires_apptainer": True,
                "runtime_store": paths["remote_root"],
            },
        }
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "HpcSiteProfile":
        if not isinstance(payload, dict):
            raise SiteProfileError("site profile must be a mapping")
        _reject_credential_bytes(payload)
        schema = _load_schema()
        errors = sorted(
            jsonschema.Draft202012Validator(schema).iter_errors(payload),
            key=lambda err: list(err.path),
        )
        if errors:
            first = errors[0]
            where = ".".join(str(p) for p in first.path) or "$"
            raise SiteProfileError(
                f"site profile violates hpc-site-profile.schema.json at "
                f"{where}: {first.message}"
            )
        frozen = _freeze(payload)
        return cls(
            site_id=payload["site_id"],
            scheduler=payload["scheduler"],
            connection=frozen["connection"],
            account=payload["account"],
            queues=frozen["queues"],
            resource_mapping=frozen.get("resource_mapping", {}),
            runtime_policy=frozen["runtime_policy"],
            digest=_digest(_canonical(frozen)),
        )

    def public_identity(self) -> dict[str, Any]:
        """Digest-bearing identity for records; no hostnames, no secrets."""
        return {"site_id": self.site_id, "scheduler": self.scheduler,
                "digest": self.digest}

    def public_capabilities(self) -> dict[str, Any]:
        """Candidate-visible view: abstract classes + ceilings only."""
        classes: dict[str, Any] = {}
        for name, queue in self.queues.items():
            entry = {
                "max_cpus": queue["max_cpus"],
                "max_memory_gb": queue["max_memory_gb"],
                "max_walltime_minutes": queue["max_walltime_minutes"],
            }
            if "max_gpus" in queue:
                entry["max_gpus"] = queue["max_gpus"]
            classes[name] = entry
        return {
            "site_id": self.site_id,
            "resource_classes": classes,
            "operations": ["capabilities", "submit", "status", "logs",
                           "fetch", "cancel", "usage"],
            "containerized": self.runtime_policy.get("requires_apptainer", True),
        }

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "site_id": self.site_id,
            "scheduler": self.scheduler,
            "account": self.account,
            "queues": _deep_copy(self.queues),
            "resource_mapping": _deep_copy(self.resource_mapping),
            "runtime_policy": _deep_copy(self.runtime_policy),
            "digest": self.digest,
        }

    # -- resource resolution ---------------------------------------------------

    def resolve_workload(self, workload_type: str) -> ResolvedResource:
        """Map an abstract workload type to concrete scheduler parameters.

        ``workload_type`` is ``"cpu"`` or ``"gpu"``.  The resource mapping
        translates it to a physical queue; when no mapping exists, the
        workload type is used as the queue name directly (identity mapping).

        Returns a frozen :class:`ResolvedResource` carrying account, partition,
        QOS, GRES ceiling and queue metadata.  Does **not** query the live
        site — call :meth:`check_acl` separately before submission.
        """
        mapping = self.resource_mapping.get(workload_type)
        if mapping is not None:
            queue_name = mapping["queue"]
            note = mapping.get("note", "")
        else:
            queue_name = workload_type
            note = ""
        queue = self.queues.get(queue_name)
        if queue is None:
            raise SiteProfileError(
                f"queue {queue_name!r} (mapped from workload {workload_type!r}) "
                f"not in profile queues {sorted(self.queues)}"
            )
        return ResolvedResource(
            account=self.account,
            partition=queue["partition"],
            qos=queue["qos"],
            max_cpus=queue["max_cpus"],
            max_memory_gb=queue["max_memory_gb"],
            max_walltime_minutes=queue["max_walltime_minutes"],
            max_gpus=queue.get("max_gpus", 0),
            workload_type=workload_type,
            queue_name=queue_name,
            mapping_note=note,
        )

    @staticmethod
    def check_acl(
        resolved: ResolvedResource,
        *,
        ssh_fn: Callable[[str], str],
    ) -> None:
        """Query the live site and verify the account is allowed on the partition.

        ``ssh_fn(command) -> stdout`` executes a read-only command on the
        login node.  Raises :class:`SiteProfileBlockedError` if the account
        is not in the partition's ``AllowAccounts`` whitelist.  Does nothing
        (returns ``None``) when the ACL accepts the account.

        The parser handles the real the site output format where multiple
        key=value fields appear on one line::

            AllowGroups=ALL AllowAccounts=acct-alpha,... AllowQos=normal,long

        It also verifies the user has a live Slurm association for the
        account on the target partition.
        """
        # -- 1. Partition AllowAccounts check ----------------------------------
        # resolved.partition may be a comma-separated multi-partition list
        # (sbatch semantics: run on whichever listed partition satisfies the
        # request).  The ACL gate passes if ANY listed partition admits the
        # account; partitions that reject it are simply skipped by the
        # scheduler.
        acl_error: Exception | None = None
        acl_passed = False
        for part in [p.strip() for p in resolved.partition.split(",")
                     if p.strip()]:
            try:
                output = ssh_fn(f"scontrol show partition {part}")
            except Exception as exc:
                raise SiteProfileError(
                    f"ACL check failed: could not query partition "
                    f"{part!r}: {exc}"
                ) from exc
            accounts_value = _extract_field(output, "AllowAccounts")
            if accounts_value is None:
                acl_error = SiteProfileBlockedError(
                    part, resolved.account,
                    "AllowAccounts field missing from scontrol output")
                continue
            if accounts_value.strip().upper() == "ALL":
                acl_passed = True
                break
            allowed = {a.strip() for a in accounts_value.split(",") if a.strip()}
            if resolved.account in allowed:
                acl_passed = True
                break
            acl_error = SiteProfileBlockedError(
                part, resolved.account,
                f"account not in AllowAccounts ({len(allowed)} accounts listed)")
        if not acl_passed and acl_error is not None:
            raise acl_error
        if not acl_passed:
            raise SiteProfileError(
                f"ACL check failed: no partitions in {resolved.partition!r}")

        # -- 2. User association check -----------------------------------------
        try:
            # Filter by the invoking user FIRST: a cluster-wide dump truncated
            # to N lines hides this user's rows on any shared cluster and
            # fails closed for the wrong reason (real <site-alias> case, where
            # the first 20 rows are all root/acct-delta).
            assoc_output = ssh_fn(
                "sacctmgr -nP show assoc user=$USER "
                "format=Cluster,Account,Partition,QOS 2>/dev/null"
            )
        except Exception:
            # Association query failure is non-fatal; partition ACL already
            # verified.  Some sites restrict sacctmgr on login nodes.
            return

        if not _has_association(
            assoc_output, resolved.account, resolved.partition
        ):
            raise SiteProfileBlockedError(
                resolved.partition,
                resolved.account,
                "user has no live Slurm association for this "
                "account/partition combination",
            )

    def to_adapter_config(
        self,
        resolved: ResolvedResource,
        *,
        workspace_root: str,
        gateway_url: str = "local://dispatcher",
        run_token: str = "",
    ) -> dict[str, Any]:
        """Build the site_config dict consumed by :class:`SlurmAdapter`.

        The output carries account, partition, QOS and queue ceilings from the
        resolved resource.  It never contains SSH credentials, remote root
        paths, or Candidate-visible identifiers.
        """
        return {
            "site": self.site_id,
            "gateway_url": gateway_url,
            "run_token": run_token,
            "ssh_alias": self.connection.get("target_binding", ""),
            "scratch": workspace_root,
            "account": resolved.account,
            "platform_profile": {
                "name": self.site_id,
                "default_queue": resolved.partition,
                "queues": [
                    {
                        "name": resolved.partition,
                        "qos": resolved.qos,
                        "max_cpus": resolved.max_cpus,
                        "max_memory_gb": resolved.max_memory_gb,
                        "max_gpus": resolved.max_gpus,
                        "max_walltime_minutes": resolved.max_walltime_minutes,
                    }
                ],
            },
        }


def _reject_credential_bytes(payload: Any, path: str = "$") -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(key, str) and key.lower() in _FORBIDDEN_KEYS:
                raise SiteProfileError(
                    f"credential material forbidden in site profile at "
                    f"{path}.{key}"
                )
            _reject_credential_bytes(value, f"{path}.{key}")
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            _reject_credential_bytes(value, f"{path}[{index}]")


def _freeze(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "connection": dict(payload["connection"]),
        "queues": {name: dict(queue) for name, queue in payload["queues"].items()},
        "resource_mapping": {
            name: dict(mapping)
            for name, mapping in payload.get("resource_mapping", {}).items()
        },
        "runtime_policy": dict(payload["runtime_policy"]),
        # Policy identity binds everything except the site label itself.
        "_bind": {
            "target_binding": payload["connection"]["target_binding"],
            "remote_user": payload["connection"]["remote_user"],
            "remote_root_policy": payload["connection"]["remote_root_policy"],
            "account": payload["account"],
        },
    }


def _deep_copy(value: Any) -> Any:
    return json.loads(_canonical(value))


def _load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _extract_field(scontrol_output: str, field: str) -> str | None:
    """Extract a key=value field from scontrol output.

    Handles the real the site format where multiple fields appear on one line::

        AllowGroups=ALL AllowAccounts=acct-alpha,... AllowQos=normal,long

    Returns the value string, or ``None`` if the field is not present.
    """
    import re

    # Match field=value where value runs to the next whitespace or end of line.
    pattern = re.compile(rf"(?:^|\s){re.escape(field)}=(\S+)")
    for line in scontrol_output.splitlines():
        m = pattern.search(line)
        if m:
            return m.group(1)
    return None


def _has_association(
    sacctmgr_output: str, account: str, partition: str
) -> bool:
    """Check if sacctmgr output contains an association for account+partition.

    Handles pipe-delimited output::

        ce_cluster|acct-blocked||long,normal|

    An empty partition field means "all partitions" (unrestricted).
    """
    for line in sacctmgr_output.splitlines():
        parts = line.strip().split("|")
        if len(parts) < 4:
            continue
        _cluster, assoc_account, assoc_partition, _qos = parts[:4]
        if assoc_account.strip() != account:
            continue
        # Empty partition = all partitions.
        if not assoc_partition.strip() or assoc_partition.strip() == partition:
            return True
    return False
