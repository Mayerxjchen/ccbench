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
import uuid
from typing import Any


SIDECAR_ROLE_LABEL = "mlffbench.role=model-gateway-sidecar"
NETWORK_ROLE_LABEL = "mlffbench.role=internal-network"
CANDIDATE_ROLE_LABEL = "mlffbench.role=candidate-agent"

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

    def __init__(self, image: str, run_uid: str | None = None) -> None:
        self.image = image
        self.run_uid = run_uid or uuid.uuid4().hex[:10]
        self.network_name: str | None = None
        self.sidecar_cid: str | None = None
        self.sidecar_name: str | None = None
        self.candidate_cid: str | None = None
        self.host_port: int | None = None
        self.is_closed: bool = False

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

    async def create_network(self, prefix: str = "mlffbench-net") -> str:
        """Create an OS-level isolated internal network with audit labels."""
        if self.network_name:
            return self.network_name

        net_name = f"{prefix}-{self.run_uid}"
        cmd = [
            "docker", "network", "create",
            "--internal",
            "--label", NETWORK_ROLE_LABEL,
            "--label", f"mlffbench.run_id={self.run_uid}",
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

        self.host_port = host_port
        self.sidecar_name = f"{prefix}{self.run_uid}"
        script = forwarder_script or _SIDECAR_TCP_FORWARDER_PY

        cmd = [
            "docker", "run", "-d", "--rm",
            "--name", self.sidecar_name,
            "--network", "bridge",
            "--label", SIDECAR_ROLE_LABEL,
            "--label", f"mlffbench.run_id={self.run_uid}",
            "--add-host", "host.docker.internal:host-gateway",
        ]
        if extra_docker_args:
            cmd.extend(extra_docker_args)
        cmd.extend([
            self.image,
            "python3", "-c", script,
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

    async def rollback(self) -> None:
        """Rollback all allocated resources on any initialization failure."""
        errors: list[str] = []

        if self.candidate_cid:
            cid = self.candidate_cid
            self.candidate_cid = None
            rc_stop, _, err_stop = await self._run_cmd(["docker", "stop", "-t", "2", cid])
            rc_rm, _, err_rm = await self._run_cmd(["docker", "rm", "-f", cid])
            if rc_stop != 0 and "No such container" not in err_stop:
                errors.append(f"stop candidate {cid}: {err_stop}")
            if rc_rm != 0 and "No such container" not in err_rm:
                errors.append(f"rm candidate {cid}: {err_rm}")

        if self.sidecar_cid:
            scid = self.sidecar_cid
            self.sidecar_cid = None
            rc_stop, _, err_stop = await self._run_cmd(["docker", "stop", "-t", "2", scid])
            rc_rm, _, err_rm = await self._run_cmd(["docker", "rm", "-f", scid])
            if rc_stop != 0 and "No such container" not in err_stop:
                errors.append(f"stop sidecar {scid}: {err_stop}")
            if rc_rm != 0 and "No such container" not in err_rm:
                errors.append(f"rm sidecar {scid}: {err_rm}")

        if self.network_name:
            net = self.network_name
            self.network_name = None
            rc_net, _, err_net = await self._run_cmd(["docker", "network", "rm", net])
            if rc_net != 0 and "No such network" not in err_net:
                errors.append(f"rm network {net}: {err_net}")

        if errors:
            raise TopologyRollbackError(f"Errors occurred during topology rollback: {errors}")

    async def close(self, stop_timeout_sec: int = 5) -> None:
        """Strict, idempotent cleanup of all containers and internal network."""
        errors: list[str] = []

        if self.candidate_cid:
            cid = self.candidate_cid
            self.candidate_cid = None
            rc_stop, _, err_stop = await self._run_cmd(["docker", "stop", "-t", str(stop_timeout_sec), cid])
            rc_rm, _, err_rm = await self._run_cmd(["docker", "rm", "-f", cid])
            if rc_stop != 0 and "No such container" not in err_stop:
                errors.append(f"docker stop candidate '{cid}' failed: {err_stop}")
            if rc_rm != 0 and "No such container" not in err_rm:
                errors.append(f"docker rm candidate '{cid}' failed: {err_rm}")

        if self.sidecar_cid:
            scid = self.sidecar_cid
            self.sidecar_cid = None
            rc_stop, _, err_stop = await self._run_cmd(["docker", "stop", "-t", str(stop_timeout_sec), scid])
            rc_rm, _, err_rm = await self._run_cmd(["docker", "rm", "-f", scid])
            if rc_stop != 0 and "No such container" not in err_stop:
                errors.append(f"docker stop sidecar '{scid}' failed: {err_stop}")
            if rc_rm != 0 and "No such container" not in err_rm:
                errors.append(f"docker rm sidecar '{scid}' failed: {err_rm}")

        if self.network_name:
            net = self.network_name
            self.network_name = None
            rc_net, _, err_net = await self._run_cmd(["docker", "network", "rm", net])
            if rc_net != 0 and "No such network" not in err_net:
                await asyncio.sleep(0.5)
                rc_retry, _, err_retry = await self._run_cmd(["docker", "network", "rm", net])
                if rc_retry != 0 and "No such network" not in err_retry:
                    errors.append(f"docker network rm '{net}' failed: {err_retry}")

        self.is_closed = True
        if errors:
            raise TopologyCleanupError(f"Errors occurred during topology teardown: {'; '.join(errors)}")
