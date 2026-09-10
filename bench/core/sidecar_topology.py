"""Production Sidecar & Internal Network Topology Manager.

Encapsulates the lifecycle of:
1. Docker --internal isolated network (zero external egress).
2. Dual-homed model-gateway sidecar (bridge + internal alias model-gateway).
3. Candidate container registration.
4. Fail-closed atomic rollback on any startup failure.
5. Idempotent and strict cleanup with error propagation.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import Any


SIDECAR_ROLE_LABEL = "bench.role=model-gateway-sidecar"
NETWORK_ROLE_LABEL = "bench.role=internal-network"
CANDIDATE_ROLE_LABEL = "bench.role=candidate-agent"

_RUN_UID_RE = re.compile(r"^[0-9a-f]{32}$")
_RESOURCE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def validate_resource_run_uid(run_uid: str) -> str:
    """Validate the only identifier accepted by the recovery reconciler."""
    if not isinstance(run_uid, str) or _RUN_UID_RE.fullmatch(run_uid) is None:
        raise ValueError("resource run UID must be exactly 32 lowercase hexadecimal characters")
    return run_uid

_SIDECAR_TCP_FORWARDER_PY = """
import asyncio
import sys

TARGET_HOST = sys.argv[1]
TARGET_PORT = int(sys.argv[2])
LISTEN_PORT = int(sys.argv[3])

async def pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

async def handle_client(c_reader, c_writer):
    try:
        s_reader, s_writer = await asyncio.open_connection(TARGET_HOST, TARGET_PORT)
    except Exception:
        c_writer.close()
        return
    asyncio.create_task(pipe(c_reader, s_writer))
    asyncio.create_task(pipe(s_reader, c_writer))

async def main():
    server = await asyncio.start_server(handle_client, "0.0.0.0", LISTEN_PORT)
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
"""


class TopologyError(RuntimeError):
    """Base error for sidecar topology operations."""


class TopologyRollbackError(TopologyError):
    """Raised when rollback of resources fails during failure recovery."""


class TopologyCleanupError(TopologyError):
    """Raised when teardown/cleanup of resources encounters non-zero exits."""


class SidecarTopologyManager:
    """Manages the creation, verification, rollback, and cleanup of internal network and sidecar."""

    def __init__(self, image: str, run_uid: str | None = None, sidecar_image: str | None = None) -> None:
        # Keep the sidecar image explicit so it can later be qualified
        # independently from the scientific Candidate image. The current
        # minimal slice reuses the image only when it provides python3.
        self.image = image
        self.sidecar_image = sidecar_image or image
        self.run_uid = validate_resource_run_uid(run_uid) if run_uid is not None else uuid.uuid4().hex[:10]
        self.network_name: str | None = None
        self.sidecar_cid: str | None = None
        self.sidecar_name: str | None = None
        self.candidate_cid: str | None = None
        self.host_port: int | None = None
        self.is_closed: bool = False

    async def reconcile_run(self) -> dict[str, Any]:
        """Reconcile only this run's labelled resources, in dependency order.

        This is deliberately independent of in-memory handles so it can recover
        after SIGKILL.  Docker filters are exact label equality filters and
        every returned ID is inspected again before deletion.  No names,
        globs, image deletion, or prune operation is used here.
        """
        run_uid = validate_resource_run_uid(self.run_uid)
        roles = (
            ("candidate", CANDIDATE_ROLE_LABEL.split("=", 1)[1], False),
            ("sidecar", SIDECAR_ROLE_LABEL.split("=", 1)[1], False),
            ("network", NETWORK_ROLE_LABEL.split("=", 1)[1], True),
        )
        deleted: dict[str, list[str]] = {name: [] for name, _, _ in roles}
        errors: list[str] = []

        async def list_ids(role: str, *, network: bool) -> list[str]:
            kind = "network" if network else "ps"
            cmd = [
                "docker", kind, "ls" if network else "-aq",
                "--filter", f"label=bench.run_id={run_uid}",
                "--filter", f"label=bench.role={role}",
                "--format", "{{.ID}}",
            ]
            rc, out, err = await self._run_cmd(cmd)
            if rc != 0:
                raise TopologyCleanupError(f"reconcile list {kind} failed: {err}")
            ids: list[str] = []
            for raw in out.splitlines():
                resource_id = raw.strip()
                if not resource_id:
                    continue
                if _RESOURCE_ID_RE.fullmatch(resource_id) is None:
                    raise TopologyCleanupError("reconcile returned an unsafe Docker resource ID")
                ids.append(resource_id)
            return ids

        for name, role, is_network in roles:
            try:
                resource_ids = await list_ids(role, network=is_network)
            except TopologyCleanupError as exc:
                errors.append(str(exc))
                continue
            for resource_id in resource_ids:
                label_format = "{{json .Labels}}" if is_network else "{{json .Config.Labels}}"
                rc, out, err = await self._run_cmd([
                    "docker", "inspect", "--format", label_format, resource_id,
                ])
                if rc != 0:
                    # A concurrently completed cleanup is safe and idempotent.
                    if "No such" in err:
                        continue
                    errors.append(f"inspect {name} {resource_id} failed: {err}")
                    continue
                try:
                    labels = json.loads(out)
                except json.JSONDecodeError:
                    errors.append(f"inspect {name} {resource_id} returned invalid labels")
                    continue
                if not isinstance(labels, dict) or labels.get("bench.run_id") != run_uid \
                        or labels.get("bench.role") != role:
                    errors.append(f"refusing unverified {name} resource {resource_id}")
                    continue
                rm_cmd = ["docker", "network", "rm"] if is_network else ["docker", "rm", "-f"]
                rm_cmd.append(resource_id)
                rc_rm, _, err_rm = await self._run_cmd(rm_cmd)
                if rc_rm == 0 or "No such" in err_rm:
                    deleted[name].append(resource_id)
                else:
                    errors.append(f"remove {name} {resource_id} failed: {err_rm}")

        remaining: dict[str, list[str]] = {}
        for name, role, is_network in roles:
            try:
                ids = await list_ids(role, network=is_network)
            except TopologyCleanupError as exc:
                errors.append(str(exc))
                ids = []
            if ids:
                remaining[name] = ids
        if errors:
            raise TopologyCleanupError("run reconciliation failed: " + "; ".join(errors))
        return {"run_uid": run_uid, "deleted": deleted, "remaining": remaining, "cleanup_ok": not remaining}


    async def _run_cmd(self, cmd: list[str]) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return (
            proc.returncode or 0,
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
        )

    async def create_network(self, prefix: str = "bench-net") -> str:
        """Create an OS-level isolated internal network with audit labels."""
        if self.network_name:
            return self.network_name

        net_name = f"{prefix}-{self.run_uid}"
        cmd = [
            "docker", "network", "create",
            "--internal",
            "--label", NETWORK_ROLE_LABEL,
            "--label", f"bench.run_id={self.run_uid}",
            net_name,
        ]
        rc, out, err = await self._run_cmd(cmd)
        if rc != 0:
            raise TopologyError(f"Failed to create internal docker network '{net_name}': {err}")

        self.network_name = net_name
        return self.network_name

    async def start_sidecar(
        self,
        host_port: int,
        prefix: str = "sidecar-",
        forwarder_script: str | None = None,
        extra_docker_args: list[str] | None = None,
    ) -> str:
        """Launch the dual-homed sidecar attached to bridge and connected to internal network."""
        if not self.network_name:
            await self.create_network()

        if not isinstance(host_port, int) or not (1 <= host_port <= 65535):
            raise TopologyError("sidecar target port must be a valid single host port")
        if extra_docker_args:
            # The forwarder is deliberately fixed to the coordinator's
            # run-scoped proxy. Callers cannot add a second network, host
            # namespace, published port, volume, or entrypoint.
            forbidden = {"--network", "--net", "--privileged", "--pid=host",
                         "--network=host", "--publish", "-p", "--volume", "-v",
                         "--entrypoint"}
            if any(arg in forbidden or arg.startswith(("--network=", "--publish=", "--volume="))
                   for arg in extra_docker_args):
                raise TopologyError("sidecar overrides may not weaken its fixed network/entrypoint")

        self.host_port = host_port
        self.sidecar_name = f"{prefix}{self.run_uid}"
        script = forwarder_script or _SIDECAR_TCP_FORWARDER_PY

        cmd = [
            "docker", "run", "-d", "--rm",
            "--name", self.sidecar_name,
            "--network", "bridge",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit", "64",
            "--entrypoint", "python3",
            "--label", SIDECAR_ROLE_LABEL,
            "--label", f"bench.run_id={self.run_uid}",
            "--add-host", "host.docker.internal:host-gateway",
        ]
        if extra_docker_args:
            cmd.extend(extra_docker_args)
        cmd.extend([
            self.sidecar_image,
            "-c", script,
            "host.docker.internal", str(host_port), "8080",
        ])

        rc, out, err = await self._run_cmd(cmd)
        if rc != 0:
            await self.rollback()
            raise TopologyError(f"Failed to start model gateway sidecar '{self.sidecar_name}': {err}")

        self.sidecar_cid = out

        # Connect sidecar to internal network with alias model-gateway
        conn_cmd = [
            "docker", "network", "connect",
            "--alias", "model-gateway",
            self.network_name,
            self.sidecar_cid,
        ]
        rc_conn, _, err_conn = await self._run_cmd(conn_cmd)
        if rc_conn != 0:
            await self.rollback()
            raise TopologyError(
                f"Failed to connect sidecar '{self.sidecar_cid}' to internal network '{self.network_name}': {err_conn}"
            )

        return self.sidecar_cid

    def register_candidate(self, candidate_cid: str) -> None:
        """Register candidate container under topology management."""
        self.candidate_cid = candidate_cid

    async def verify_isolation(self) -> None:
        """Fail closed unless Candidate/sidecar attachments match the topology.

        This is intentionally an inspect-based health gate, not a best-effort
        log message: a Candidate with a bridge attachment would have internet
        egress and must never proceed.
        """
        if not self.network_name or not self.candidate_cid:
            raise TopologyError("cannot verify an incomplete topology")
        rc, out, err = await self._run_cmd([
            "docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", self.candidate_cid,
        ])
        if rc != 0:
            raise TopologyError(f"candidate isolation inspect failed: {err}")
        try:
            candidate_networks = json.loads(out)
        except json.JSONDecodeError as exc:
            raise TopologyError("candidate isolation inspect returned invalid JSON") from exc
        if set(candidate_networks) != {self.network_name}:
            raise TopologyError(
                f"candidate has unexpected network attachments: {sorted(candidate_networks)}"
            )
        if self.sidecar_cid:
            rc, out, err = await self._run_cmd([
                "docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", self.sidecar_cid,
            ])
            if rc != 0:
                raise TopologyError(f"sidecar isolation inspect failed: {err}")
            try:
                sidecar_networks = json.loads(out)
            except json.JSONDecodeError as exc:
                raise TopologyError("sidecar isolation inspect returned invalid JSON") from exc
            if "bridge" not in sidecar_networks or self.network_name not in sidecar_networks:
                raise TopologyError(
                    f"sidecar lacks required bridge/internal attachments: {sorted(sidecar_networks)}"
                )
            rc, out, err = await self._run_cmd([
                "docker", "inspect", "--format",
                "{{json .Config.Entrypoint}}|{{json .Config.Cmd}}", self.sidecar_cid,
            ])
            if rc != 0:
                raise TopologyError(f"sidecar command inspect failed: {err}")
            try:
                entrypoint_raw, cmd_raw = out.split("|", 1)
                entrypoint = json.loads(entrypoint_raw)
                sidecar_cmd = json.loads(cmd_raw)
            except (ValueError, json.JSONDecodeError) as exc:
                raise TopologyError("sidecar command inspect returned invalid JSON") from exc
            if (entrypoint != ["python3"] or not isinstance(sidecar_cmd, list)
                    or len(sidecar_cmd) < 5 or sidecar_cmd[0] != "-c"
                    or sidecar_cmd[2] != "host.docker.internal"):
                raise TopologyError("sidecar command is not the fixed host-proxy forwarder")
            if str(self.host_port) != str(sidecar_cmd[3]) or sidecar_cmd[4] != "8080":
                raise TopologyError("sidecar target is not the run-scoped proxy port")

    async def rollback(self) -> None:
        """Rollback all allocated resources on any initialization failure.

        Preserves resource handles if deletion fails so subsequent retries are possible.
        """
        errors: list[str] = []

        if self.candidate_cid:
            cid = self.candidate_cid
            rc_stop, _, err_stop = await self._run_cmd(["docker", "stop", "-t", "2", cid])
            rc_rm, _, err_rm = await self._run_cmd(["docker", "rm", "-f", cid])
            cand_clean = True
            if rc_stop != 0 and "No such container" not in err_stop:
                cand_clean = False
                errors.append(f"stop candidate {cid}: {err_stop}")
            if rc_rm != 0 and "No such container" not in err_rm:
                cand_clean = False
                errors.append(f"rm candidate {cid}: {err_rm}")
            if cand_clean:
                self.candidate_cid = None

        if self.sidecar_cid:
            scid = self.sidecar_cid
            rc_stop, _, err_stop = await self._run_cmd(["docker", "stop", "-t", "2", scid])
            rc_rm, _, err_rm = await self._run_cmd(["docker", "rm", "-f", scid])
            sc_clean = True
            if rc_stop != 0 and "No such container" not in err_stop:
                sc_clean = False
                errors.append(f"stop sidecar {scid}: {err_stop}")
            if rc_rm != 0 and "No such container" not in err_rm:
                sc_clean = False
                errors.append(f"rm sidecar {scid}: {err_rm}")
            if sc_clean:
                self.sidecar_cid = None

        if self.network_name:
            net = self.network_name
            rc_net, _, err_net = await self._run_cmd(["docker", "network", "rm", net])
            net_clean = True
            if rc_net != 0 and "No such network" not in err_net:
                net_clean = False
                errors.append(f"rm network {net}: {err_net}")
            if net_clean:
                self.network_name = None

        if errors:
            self.is_closed = False
            raise TopologyRollbackError(f"Errors occurred during topology rollback: {errors}")
        self.is_closed = True

    async def close(self, stop_timeout_sec: int = 5) -> None:
        """Strict, idempotent cleanup of all containers and internal network.

        CRITICAL INVARIANT:
        Resource IDs are cleared ONLY when deletion is confirmed successful or confirmed
        non-existent. If any command fails, remaining resource IDs are PRESERVED and
        is_closed remains False so that callers can retry cleanup.
        """
        if self.is_closed and not self.candidate_cid and not self.sidecar_cid and not self.network_name:
            return

        errors: list[str] = []

        if self.candidate_cid:
            cid = self.candidate_cid
            rc_stop, _, err_stop = await self._run_cmd(["docker", "stop", "-t", str(stop_timeout_sec), cid])
            rc_rm, _, err_rm = await self._run_cmd(["docker", "rm", "-f", cid])
            cand_clean = True
            if rc_stop != 0 and "No such container" not in err_stop:
                cand_clean = False
                errors.append(f"docker stop candidate '{cid}' failed: {err_stop}")
            if rc_rm != 0 and "No such container" not in err_rm:
                cand_clean = False
                errors.append(f"docker rm candidate '{cid}' failed: {err_rm}")
            if cand_clean:
                self.candidate_cid = None

        if self.sidecar_cid:
            scid = self.sidecar_cid
            rc_stop, _, err_stop = await self._run_cmd(["docker", "stop", "-t", str(stop_timeout_sec), scid])
            rc_rm, _, err_rm = await self._run_cmd(["docker", "rm", "-f", scid])
            sc_clean = True
            if rc_stop != 0 and "No such container" not in err_stop:
                sc_clean = False
                errors.append(f"docker stop sidecar '{scid}' failed: {err_stop}")
            if rc_rm != 0 and "No such container" not in err_rm:
                sc_clean = False
                errors.append(f"docker rm sidecar '{scid}' failed: {err_rm}")
            if sc_clean:
                self.sidecar_cid = None

        if self.network_name:
            net = self.network_name
            rc_net, _, err_net = await self._run_cmd(["docker", "network", "rm", net])
            net_clean = True
            if rc_net != 0 and "No such network" not in err_net:
                await asyncio.sleep(0.5)
                rc_retry, _, err_retry = await self._run_cmd(["docker", "network", "rm", net])
                if rc_retry != 0 and "No such network" not in err_retry:
                    net_clean = False
                    errors.append(f"docker network rm '{net}' failed: {err_retry}")
            if net_clean:
                self.network_name = None

        if errors:
            self.is_closed = False
            raise TopologyCleanupError(f"Errors occurred during topology teardown: {'; '.join(errors)}")

        self.is_closed = True


async def reconcile_run_resources(run_uid: str) -> dict[str, Any]:
    """Recover a run using only its strict label identity."""
    validate_resource_run_uid(run_uid)
    manager = SidecarTopologyManager(image="", run_uid=run_uid)
    return await manager.reconcile_run()
