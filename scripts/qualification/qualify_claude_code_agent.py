#!/usr/bin/env python3
"""MLFFBench Candidate Agent Physical Qualification: Claude Code.

Executes a 5-level real-machine Canary battery inside physical Docker containers
to qualify Claude Code as the sole formal Candidate Agent for MLFFBench:

  Canary 1: Real Container Image Isolation & Internal Probes (qualify_agent.sh)
  Canary 2: Real Adversarial Containment & High-risk Command Interception (126 Access Denied)
  Canary 3: Skills Topology & Canonical Workspace Path (/app/.claude/skills/)
  Canary 4: Live Host Model Gateway Proxy, Ephemeral Token Auth & Live Budget Cut-off
  Canary 5: Deterministic RunLock Binding & Verifier Cryptographic Sealing

Strict Producer-Verifier Separation:
Evidence collected from live commands is independently validated and signed
by CandidateAgentVerifier before producing a PROMOTED qualification receipt.
"""

from __future__ import annotations

import asyncio
import http.server
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dftworld_bench.agents import ClaudeCodeAdapter
from dftworld_bench.config.profiles import ProfileRegistry
from dftworld_bench.config.resolver import construct_experiment, resolve_formal
from dftworld_bench.contracts.case import CaseSpec
from dftworld_bench.core.budgets import BUDGET_DOMAINS, BudgetLedger, BudgetPolicy
from dftworld_bench.core.model_proxy import ModelGatewayProxy
from dftworld_bench.core.sidecar_topology import (
    NETWORK_ROLE_LABEL,
    SIDECAR_ROLE_LABEL,
    SidecarTopologyManager,
)
from dftworld_bench.verifiers.candidate_agent_verifier import CandidateAgentVerifier

AGENT_IMAGE = "mlffbench-candidate-claude-code-sandbox:v1"


def run_command_sync(cmd: list[str], timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    """Helper to run a subprocess command synchronously."""
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )


def run_canary_1_image_isolation(evidence: dict[str, Any]) -> None:
    """Canary 1: Real Docker Image inspect, non-root user, and internal probe execution."""
    print("--- [Canary 1/5] Real Container Inspection & Internal Sandbox Probes ---")

    # 1. Inspect local docker image
    inspect_res = run_command_sync(["docker", "image", "inspect", "--format", "{{.Id}}", AGENT_IMAGE])
    if inspect_res.returncode != 0:
        raise RuntimeError(f"Docker image {AGENT_IMAGE} inspect failed: {inspect_res.stderr}")
    actual_image_digest = inspect_res.stdout.strip()
    print(f"  ✓ Docker image inspect: {actual_image_digest}")

    # 2. Run real internal probe in hardened container
    t0 = time.time()
    probe_res = run_command_sync([
        "docker", "run", "--rm",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=256",
        AGENT_IMAGE,
        "/usr/local/bin/qualify_agent.sh",
    ])
    duration = time.time() - t0

    if probe_res.returncode != 0:
        raise RuntimeError(f"Internal probe script failed ({probe_res.returncode}):\n{probe_res.stderr}\n{probe_res.stdout}")

    # Verify stdout contents
    assert "User: agent (UID=1000)" in probe_res.stdout, "Probe did not report non-root uid 1000"
    assert "PASS: Zero scientific computing engines" in probe_res.stdout, "Probe failed scientific boundary"
    assert "PASS: No docker socket or host ssh credentials detected" in probe_res.stdout, "Probe detected exposed secrets"
    print("  ✓ Hardened container qualify_agent.sh probe executed successfully")

    # 3. Check absence of scientific computing binaries
    for sci_bin in ["cp2k.popt", "cp2k.psmp", "dp", "lmp", "mpirun"]:
        chk = run_command_sync(["docker", "run", "--rm", AGENT_IMAGE, "which", sci_bin])
        if chk.returncode == 0:
            raise RuntimeError(f"Scientific binary {sci_bin} found in candidate agent image!")
    print("  ✓ Absolute boundary verified: zero scientific compute engines present")

    evidence["canary_1_image_isolation"] = {
        "status": "PASS",
        "image_name": AGENT_IMAGE,
        "image_digest": actual_image_digest,
        "probe_returncode": probe_res.returncode,
        "probe_stdout": probe_res.stdout,
        "probe_duration_sec": round(duration, 3),
        "user": "agent(uid=1000)",
        "scientific_engines_present": False,
    }


def run_canary_2_adversarial_containment(evidence: dict[str, Any]) -> None:
    """Canary 2: Real adversarial execution of forbidden host commands in container."""
    print("--- [Canary 2/5] Real Adversarial Containment & Command Interception ---")

    intercepted: dict[str, Any] = {}
    forbidden_cmds = ["ssh", "sbatch", "compshare", "docker", "nc", "nmap"]

    for cmd in forbidden_cmds:
        res = run_command_sync([
            "docker", "run", "--rm",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            AGENT_IMAGE,
            cmd, "--help",
        ])
        if res.returncode != 126:
            raise RuntimeError(f"Adversarial attempt for '{cmd}' did not return code 126 (got {res.returncode})")
        if f"Access Denied: command {cmd} is strictly prohibited" not in res.stderr:
            raise RuntimeError(f"Adversarial intercept stderr missing policy rejection for {cmd}: {res.stderr}")
        intercepted[cmd] = {
            "returncode": res.returncode,
            "stderr": res.stderr.strip(),
            "blocked": True,
        }
        print(f"  ✓ Forbidden command '{cmd}' intercepted -> 126 Access Denied")

    # Verify candidate container secret leakage prevention (fail-fast)
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_p = Path(tmpdir)
        try:
            ClaudeCodeAdapter(
                model="claude-3-7-sonnet-20250219",
                threads_root=tmp_p / "threads",
                task_name="probe_sec",
                case_dir=tmp_p / "case",
                container_env={"ANTHROPIC_API_KEY": "sk-ant-test-secret"},
                forbidden_env_names=frozenset({"ANTHROPIC_API_KEY"}),
            )
            raise RuntimeError("Should have raised ValueError on forbidden host secret injection")
        except ValueError as exc:
            assert "trusted API secrets" in str(exc)
            print("  ✓ Host API key injection strictly rejected at adapter boundary")

    # Verify real outbound network isolation using OS-level docker --internal network
    import uuid
    test_net = f"qual-net-{uuid.uuid4().hex[:8]}"
    net_create = run_command_sync(["docker", "network", "create", "--internal", test_net])
    if net_create.returncode != 0:
        raise RuntimeError(f"Failed to create test internal network: {net_create.stderr}")

    raw_socket_blocked = False
    http_curl_blocked = False
    try:
        # 1. Raw socket direct egress test (must raise OSError: Network is unreachable)
        socket_probe = run_command_sync([
            "docker", "run", "--rm",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--network", test_net,
            AGENT_IMAGE,
            "python3", "-c",
            '''
import socket
try:
    s = socket.create_connection(('1.1.1.1', 80), timeout=2)
    print("RAW_SOCKET_CONNECTED")
except OSError as e:
    print("RAW_SOCKET_BLOCKED_OS_ERROR:", type(e).__name__, e)
except Exception as e:
    print("RAW_SOCKET_BLOCKED:", type(e).__name__, e)
''',
        ])
        if "RAW_SOCKET_BLOCKED" in socket_probe.stdout:
            raw_socket_blocked = True
            print(f"  ✓ Raw socket direct egress physically blocked at kernel level: {socket_probe.stdout.strip()}")
        else:
            raise RuntimeError(f"Security Breach: raw socket connected over internal network! {socket_probe.stdout}")

        # 2. curl test to external domain
        net_res = run_command_sync([
            "docker", "run", "--rm",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--network", test_net,
            AGENT_IMAGE,
            "curl", "-s", "--connect-timeout", "2", "http://example.com",
        ])
        if net_res.returncode != 0:
            http_curl_blocked = True
            print("  ✓ External HTTP egress strictly blocked by internal network policy")
        else:
            raise RuntimeError("Security breach: container successfully made external network request over internal network!")
    finally:
        run_command_sync(["docker", "network", "rm", test_net])

    evidence["canary_2_adversarial_containment"] = {
        "status": "PASS" if (raw_socket_blocked and http_curl_blocked) else "FAIL",
        "intercepted_commands": intercepted,
        "secret_isolation_verified": True,
        "network_egress_blocked": http_curl_blocked,
        "raw_socket_blocked": raw_socket_blocked,
    }


def run_canary_3_skills_topology(evidence: dict[str, Any]) -> None:
    """Canary 3: Skills installation topology into workspace /.claude/skills/."""
    print("--- [Canary 3/5] Skills Topology & Container Dynamic Mount ---")
    skills_dir = ROOT / "base-env-build" / "skills"
    hpc_submit = skills_dir / "hpc-submit" / "SKILL.md"
    assert hpc_submit.is_file(), f"Missing hpc-submit skill: {hpc_submit}"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_p = Path(tmpdir)
        threads_root = tmp_p / "threads"
        adapter = ClaudeCodeAdapter(
            model="claude-3-7-sonnet-20250219",
            threads_root=threads_root,
            task_name="probe_skill",
            case_dir=tmp_p / "case",
            skill_roots=(skills_dir / "hpc-submit",),
        )
        ws = Path(adapter.workspace)
        ws.mkdir(parents=True, exist_ok=True)

        asyncio.run(adapter._install_skills_to_claude_dir())

        target_skill = ws / ".claude" / "skills" / "hpc-submit" / "SKILL.md"
        assert target_skill.is_file(), "Skill must be installed under workspace .claude/skills/hpc-submit/"

        # Test reading from inside real container
        mount_arg = f"{ws.resolve()}:/app"
        res = run_command_sync([
            "docker", "run", "--rm",
            "--volume", mount_arg,
            AGENT_IMAGE,
            "cat", "/app/.claude/skills/hpc-submit/SKILL.md",
        ])
        if res.returncode != 0:
            raise RuntimeError(f"Failed to read mounted skill inside container: {res.stderr}")
        assert "bench-hpc" in res.stdout, "Skill content in container mismatch"
        print("  ✓ Skill mounted and verified readable inside physical container at /app/.claude/skills/")

        # Verify read-only immutability of skills directory
        write_res = run_command_sync([
            "docker", "run", "--rm",
            "--volume", f"{ws.resolve() / '.claude' / 'skills'}:/app/.claude/skills:ro",
            AGENT_IMAGE,
            "bash", "-c", "touch /app/.claude/skills/hpc-submit/hack.txt",
        ])
        if write_res.returncode == 0:
            raise RuntimeError("Security breach: skill directory was writable inside container!")
        if "Read-only file system" not in write_res.stderr:
            raise RuntimeError(f"Expected Read-only file system rejection, got: {write_res.stderr}")
        print("  ✓ Skill directory mounted read-only; container write attempts rejected")

    evidence["canary_3_skills_topology"] = {
        "status": "PASS",
        "target_path": "/app/.claude/skills/hpc-submit/SKILL.md",
        "skill_mounted": True,
        "read_only_verified": True,
    }


OVER_LIMIT_ATTACK_STRING = "CANARY_OVER_LIMIT_LEAK_PROBE_UNAUTHORIZED"


class DummyUpstreamHandler(http.server.BaseHTTPRequestHandler):
    req_count = 0

    def do_POST(self):
        DummyUpstreamHandler.req_count += 1
        c = DummyUpstreamHandler.req_count
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if c == 1:
            # First request: within budget (15 + 10 = 25 tokens <= 50)
            chunk = (
                b'event: message_start\r\ndata: {"type": "message_start", "message": {"usage": {"input_tokens": 15}}}\r\n\r\n'
                b'event: message_delta\r\ndata: {"type": "message_delta", "usage": {"output_tokens": 10}}\r\n\r\n'
            )
            self.wfile.write(chunk)
            self.wfile.flush()
        else:
            # Second request:
            # Chunk 1: within budget (10 tokens -> total used 25 + 10 = 35 <= 50)
            chunk1 = (
                b'event: message_start\r\ndata: {"type": "message_start", "message": {"usage": {"input_tokens": 10}}}\r\n\r\n'
            )
            self.wfile.write(chunk1)
            self.wfile.flush()
            time.sleep(0.05)
            # Chunk 2: triggers live budget exceeded (35 + 30 = 65 > 50). Must be intercepted and NOT forwarded!
            chunk2 = (
                f'event: message_delta\r\ndata: {{"type": "message_delta", "usage": {{"output_tokens": 30}}, "delta": {{"text": "{OVER_LIMIT_ATTACK_STRING}"}}}}\r\n\r\n'
            ).encode("utf-8")
            try:
                self.wfile.write(chunk2)
                self.wfile.flush()
            except Exception:
                pass

    def log_message(self, *args):
        pass


async def _run_canary_4_async() -> dict[str, Any]:
    """Canary 4 Async runner for Model Gateway Proxy via production dual-homed sidecar topology."""
    import uuid

    # 0. Reset upstream counter and start live Dummy SSE upstream on host
    DummyUpstreamHandler.req_count = 0
    server = http.server.HTTPServer(("127.0.0.1", 0), DummyUpstreamHandler)
    u_port = server.server_address[1]
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    # 1. Construct production BudgetPolicy & BudgetLedger
    policy_limits = {d: 10_000 for d in BUDGET_DOMAINS}
    policy_limits["tokens"] = 50
    policy_limits["model_turns"] = 10
    budget_policy = BudgetPolicy(policy_limits)
    budget_ledger = BudgetLedger(budget_policy)

    callback_count = 0

    def on_budget_exceeded() -> None:
        nonlocal callback_count
        callback_count += 1

    proxy = ModelGatewayProxy(
        real_api_endpoint=f"http://127.0.0.1:{u_port}",
        real_api_key="sk-ant-canary-dummy-key",
        max_total_tokens=50,  # Strict small budget: 50 tokens
        budget_ledger=budget_ledger,
        on_budget_exceeded=on_budget_exceeded,
    )
    port = await proxy.start()
    token = proxy.ephemeral_token

    # 2. Setup Production Topology: internal network + dual-homed model-gateway sidecar
    topology = SidecarTopologyManager(image=AGENT_IMAGE)
    net_name = await topology.create_network(prefix="qual-net")

    sidecar_gateway_verified = False
    proxy_auth_verified = False
    budget_cutoff_verified = False
    budget_http_code = 0
    over_limit_chunk_forwarded = False
    ledger_before = budget_ledger.used("tokens")
    ledger_after_request_1 = 0
    ledger_after_overflow = 0

    try:
        sidecar_cid = await topology.start_sidecar(host_port=port, prefix="sidecar-qual-")
        await asyncio.sleep(0.5)  # Wait for sidecar TCP forwarder to bind 8080
        sidecar_gateway_verified = True
        print(f"  ✓ Production Dual-Homed Sidecar attached: {topology.sidecar_name} (bridge + {net_name})")

        # Step 1: Verify Candidate internal-only container cannot bypass sidecar
        cmd_bypass = [
            "docker", "run", "--rm",
            "--network", net_name,
            AGENT_IMAGE,
            "python3", "-c",
            '''
import socket
try:
    s = socket.create_connection(('1.1.1.1', 80), timeout=2)
    print("LEAK_EXTERNAL_CONNECTED")
except OSError:
    print("PASS_EGRESS_BLOCKED")
''',
        ]
        proc_bp = await asyncio.create_subprocess_exec(
            *cmd_bypass,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bp, _ = await proc_bp.communicate()
        assert "PASS_EGRESS_BLOCKED" in stdout_bp.decode("utf-8", errors="replace"), "Candidate bypassed internal network!"
        print("  ✓ Candidate container strictly isolated on internal network (direct egress blocked)")

        # Step 2: Real container request through sidecar with invalid token -> 401
        cmd_bad = [
            "docker", "run", "--rm",
            "--network", net_name,
            AGENT_IMAGE,
            "curl", "-s", "-i",
            "-X", "POST",
            "-H", "x-api-key: invalid-token-attack",
            "-H", "content-type: application/json",
            "-d", "{}",
            "http://model-gateway:8080/v1/messages",
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd_bad,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        out_str = stdout.decode("utf-8", errors="replace")
        if proc.returncode == 0 and ("401 Unauthorized" in out_str or "401" in out_str):
            proxy_auth_verified = True
            print("  ✓ Candidate request through production sidecar with invalid token rejected with HTTP 401")
        else:
            print(f"  ✗ Sidecar auth check mismatch: returncode={proc.returncode}, out={out_str!r}")

        # Step 3: Live SSE Request 1 through sidecar within budget (consumes 25 tokens)
        cmd_req1 = [
            "docker", "run", "--rm",
            "--network", net_name,
            AGENT_IMAGE,
            "curl", "-s", "-i",
            "-X", "POST",
            "-H", f"x-api-key: {token}",
            "-H", "content-type: application/json",
            "-d", "{}",
            "http://model-gateway:8080/v1/messages",
        ]
        proc1 = await asyncio.create_subprocess_exec(
            *cmd_req1,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc1.communicate()
        ledger_after_request_1 = budget_ledger.used("tokens")
        assert ledger_after_request_1 == 25, f"Expected ledger_after_request_1=25, got {ledger_after_request_1}"
        assert proxy.tokens_used == 25, f"Expected proxy tokens_used=25, got {proxy.tokens_used}"
        print(f"  ✓ Live SSE request 1 streamed through production sidecar: tokens charged to ledger: {ledger_after_request_1}/50")

        # Step 4: Live SSE Request 2 through sidecar triggering over-limit (25 + 10 + 30 = 65 > 50)
        cmd_req2 = [
            "docker", "run", "--rm",
            "--network", net_name,
            AGENT_IMAGE,
            "curl", "-s", "-i",
            "-X", "POST",
            "-H", f"x-api-key: {token}",
            "-H", "content-type: application/json",
            "-d", "{}",
            "http://model-gateway:8080/v1/messages",
        ]
        proc2 = await asyncio.create_subprocess_exec(
            *cmd_req2,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout2, _ = await proc2.communicate()
        out2_str = stdout2.decode("utf-8", errors="replace")
        if OVER_LIMIT_ATTACK_STRING in out2_str:
            over_limit_chunk_forwarded = True
            print("  ✗ Security Leak: over-limit SSE chunk was leaked through sidecar to candidate container!")
        else:
            print("  ✓ Live SSE request 2 over-limit chunk strictly intercepted by proxy before sidecar stream")

        ledger_after_overflow = budget_ledger.used("tokens")
        assert proxy.budget_exceeded is True, "Proxy should have flagged budget_exceeded upon over-limit SSE chunk"
        assert callback_count > 0, f"on_budget_exceeded callback must be invoked (count={callback_count})"
        assert ledger_after_overflow > ledger_after_request_1, (
            f"Expected overflow ledger accounting > {ledger_after_request_1}, got {ledger_after_overflow}"
        )
        print(f"  ✓ Central BudgetLedger accounting and callback verified (overflow={ledger_after_overflow}, callback_count={callback_count})")

        # Step 5: Subsequent request rejected immediately with HTTP 429 Too Many Requests
        cmd_cutoff = [
            "docker", "run", "--rm",
            "--network", net_name,
            AGENT_IMAGE,
            "curl", "-s", "-i",
            "-X", "POST",
            "-H", f"x-api-key: {token}",
            "-H", "content-type: application/json",
            "-d", "{}",
            "http://model-gateway:8080/v1/messages",
        ]
        proc3 = await asyncio.create_subprocess_exec(
            *cmd_cutoff,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout3, _ = await proc3.communicate()
        out3_str = stdout3.decode("utf-8", errors="replace")
        if "429 Too Many Requests" in out3_str or "Budget Exceeded" in out3_str:
            budget_cutoff_verified = True
            budget_http_code = 429
            print("  ✓ Candidate request through production sidecar immediately rejected (HTTP 429) upon live budget overrun")

    finally:
        # Cleanup sidecar and internal network strictly via topology manager
        await topology.close()
        await proxy.close()
        server.shutdown()

    canary_pass = (
        sidecar_gateway_verified
        and proxy_auth_verified
        and budget_cutoff_verified
        and not over_limit_chunk_forwarded
        and callback_count > 0
        and ledger_after_overflow > ledger_after_request_1
    )

    return {
        "status": "PASS" if canary_pass else "FAIL",
        "sidecar_gateway_verified": sidecar_gateway_verified,
        "proxy_auth_verified": proxy_auth_verified,
        "budget_cutoff_verified": budget_cutoff_verified,
        "budget_cutoff_http_code": budget_http_code,
        "callback_count": callback_count,
        "over_limit_chunk_forwarded": over_limit_chunk_forwarded,
        "ledger_before": ledger_before,
        "ledger_after_request_1": ledger_after_request_1,
        "ledger_after_overflow": ledger_after_overflow,
    }


def run_canary_4_model_gateway_and_budget(evidence: dict[str, Any]) -> None:
    """Canary 4: Live Host Model Gateway Proxy, Ephemeral Token Auth & Budget Cut-off."""
    print("--- [Canary 4/5] Live Model Gateway Proxy & Real-time Budget Enforcement ---")
    res = asyncio.run(_run_canary_4_async())
    if res["status"] != "PASS":
        raise RuntimeError(f"Canary 4 failed: {res}")
    evidence["canary_4_model_gateway_and_budget"] = res


def run_canary_5_run_lock_and_schema(evidence: dict[str, Any]) -> None:
    """Canary 5: Deterministic formal run lock generation and verification."""
    print("--- [Canary 5/5] Deterministic RunLock & Complete Schema Compliance ---")
    registry = ProfileRegistry.load(ROOT / "infra" / "config")
    case = CaseSpec.load(ROOT / "031-matclaw-cips-active-distillation")
    experiment = construct_experiment({"agent": "claude-code-formal", "api": "default"}, registry)

    c1_digest = evidence["canary_1_image_isolation"]["image_digest"]

    lock = resolve_formal(
        experiment,
        case,
        "canary-agent-qual-001",
        replicate=1,
        model="claude-3-7-sonnet-20250219",
        benchmark_commit="bench-qual-commit",
        instruction="run qualification probe",
        engine="claude-code",
        engine_version="0.2.29",
        agent_image_digest=c1_digest,
    )

    agent_info = lock.payload["agent"]
    assert agent_info["engine"] == "claude-code"
    assert agent_info["engine_version"] == "0.2.29"
    assert agent_info["agent_image_digest"] == c1_digest
    assert agent_info["tool_surface_digest"].startswith("sha256:")
    assert lock.verify() is True
    print(f"  ✓ ResolvedRunLock cryptographically verified: {lock.digest}")

    evidence["canary_5_run_lock_compliance"] = {
        "status": "PASS",
        "lock_digest": lock.digest,
        "lock_verified": True,
        "engine": agent_info["engine"],
        "agent_image_digest": c1_digest,
        "tool_surface_digest": agent_info["tool_surface_digest"],
    }


async def _run_real_claude_code_async(api_key: str, api_endpoint: str) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_p = Path(tmpdir)
        threads_root = tmp_p / "threads"
        case_dir = ROOT / "031-matclaw-cips-active-distillation"
        adapter = ClaudeCodeAdapter(
            model="claude-3-7-sonnet-20250219",
            threads_root=threads_root,
            task_name="canary_live_eval",
            case_dir=case_dir,
            api_endpoint=api_endpoint,
            api_key=api_key,
            max_turns=3,
            max_total_tokens=10_000,
            agent_timeout_sec=120.0,
        )
        try:
            await adapter.prepare()
            instruction = (
                "You are participating in an automated qualification probe. "
                "Write the exact string 'canary-ok' into a file named 'canary_verdict.txt' "
                "in the current working directory, and then exit."
            )
            await adapter.start(instruction)
            logs = adapter.collect_logs()
            target_file = Path(adapter.workspace) / "canary_verdict.txt"
            content = target_file.read_text(encoding="utf-8").strip() if target_file.is_file() else ""
            if "canary-ok" not in content:
                raise RuntimeError(f"Canary file verification failed. Content: {content!r}")
            return {
                "executed": True,
                "status": "PASS",
                "tokens_used": logs.get("tokens"),
                "tool_calls": logs.get("tool_calls"),
                "workspace_file_verified": True,
            }
        finally:
            await adapter.close()


def run_real_claude_code_canary(evidence: dict[str, Any]) -> None:
    """Optional End-to-End Live Model Canary: Only runs if host provides valid ANTHROPIC_API_KEY."""
    print("--- [Optional End-to-End] Physical Claude Code Model Invocation ---")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    api_endpoint = os.environ.get("ANTHROPIC_BASE_URL", "").strip() or "https://api.anthropic.com"

    if not api_key or api_key.startswith("dummy") or api_key.startswith("test") or api_key.startswith("sk-ant-test"):
        print("  ⚠ No valid ANTHROPIC_API_KEY detected in host environment.")
        print("  ℹ Skipping live model execution. Qualification receipt will honestly record CONTAINER_PREFLIGHT_PASS.")
        evidence["real_agent_execution"] = {
            "executed": False,
            "status": "NOT_RUN",
            "reason": "No host ANTHROPIC_API_KEY configured; benchmark execution remains BLOCKED.",
        }
        return

    print("  ✓ Host ANTHROPIC_API_KEY detected. Executing physical Claude Code end-to-end Canary...")
    try:
        res = asyncio.run(_run_real_claude_code_async(api_key, api_endpoint))
        evidence["real_agent_execution"] = res
        print(f"  ✓ Live Claude Code execution PASS (tokens={res.get('tokens_used')}, tool_calls={res.get('tool_calls')})")
    except Exception as exc:
        print(f"  ✗ Live Claude Code execution failed: {exc}", file=sys.stderr)
        evidence["real_agent_execution"] = {
            "executed": True,
            "status": "FAIL",
            "error": str(exc),
        }
        raise


def verify_container_cleanup(evidence: dict[str, Any]) -> None:
    """Verify no lingering candidate containers, sidecars, or internal networks remain."""
    print("--- Verifying Zero Orphan Candidate Containers & Topology Resources ---")

    # 1. Candidate containers (running and exited)
    res_cand = run_command_sync([
        "docker", "ps", "-a", "-q",
        "--filter", f"ancestor={AGENT_IMAGE}",
    ])
    if res_cand.returncode != 0:
        raise RuntimeError(
            f"Failed to query candidate containers (exit code {res_cand.returncode}): {res_cand.stderr.strip()}"
        )
    candidate_ids = [cid.strip() for cid in res_cand.stdout.splitlines() if cid.strip()]

    # 2. Sidecar containers (via role label and name prefix)
    res_sidecar_label = run_command_sync([
        "docker", "ps", "-a", "-q",
        "--filter", f"label={SIDECAR_ROLE_LABEL}",
    ])
    if res_sidecar_label.returncode != 0:
        raise RuntimeError(
            f"Failed to query sidecar containers by label (exit code {res_sidecar_label.returncode}): {res_sidecar_label.stderr.strip()}"
        )
    res_sidecar_name = run_command_sync([
        "docker", "ps", "-a", "-q",
        "--filter", "name=sidecar-qual-",
    ])
    if res_sidecar_name.returncode != 0:
        raise RuntimeError(
            f"Failed to query sidecar containers by name (exit code {res_sidecar_name.returncode}): {res_sidecar_name.stderr.strip()}"
        )
    sidecar_ids = sorted(set(
        [cid.strip() for cid in res_sidecar_label.stdout.splitlines() if cid.strip()]
        + [cid.strip() for cid in res_sidecar_name.stdout.splitlines() if cid.strip()]
    ))

    # 3. Internal isolated networks (via role label and name prefix)
    res_net_label = run_command_sync([
        "docker", "network", "ls", "-q",
        "--filter", f"label={NETWORK_ROLE_LABEL}",
    ])
    if res_net_label.returncode != 0:
        raise RuntimeError(
            f"Failed to query internal networks by label (exit code {res_net_label.returncode}): {res_net_label.stderr.strip()}"
        )
    res_net_name = run_command_sync([
        "docker", "network", "ls", "-q",
        "--filter", "name=qual-net-",
    ])
    if res_net_name.returncode != 0:
        raise RuntimeError(
            f"Failed to query internal networks by name (exit code {res_net_name.returncode}): {res_net_name.stderr.strip()}"
        )
    network_ids = sorted(set(
        [nid.strip() for nid in res_net_label.stdout.splitlines() if nid.strip()]
        + [nid.strip() for nid in res_net_name.stdout.splitlines() if nid.strip()]
    ))

    total_containers = len(candidate_ids) + len(sidecar_ids)
    is_clean = len(candidate_ids) == 0 and len(sidecar_ids) == 0 and len(network_ids) == 0

    evidence["container_cleanup"] = {
        "clean": is_clean,
        "candidate_containers_found": len(candidate_ids),
        "sidecar_containers_found": len(sidecar_ids),
        "internal_networks_found": len(network_ids),
        "running_containers_found": total_containers,
        "queries_succeeded": True,
    }

    if not is_clean:
        raise RuntimeError(
            f"Zero-orphan verification failed! Lingering resources detected:\n"
            f"  candidate_containers: {candidate_ids}\n"
            f"  sidecar_containers: {sidecar_ids}\n"
            f"  internal_networks: {network_ids}"
        )
    print("  ✓ Zero orphan agent containers, sidecars, and internal networks verified")


def resolve_maintainer_signing_key(signing_key_file: Path | None = None) -> str:
    """Resolve and securely load the Ed25519 maintainer private key.

    Security requirements:
    1. Private key files must exist and possess strict POSIX permissions (0600 or 0400).
    2. Any group or others read/write permissions cause immediate rejection.
    3. Prefer secure key file over raw environment variable to prevent process / shell history leakage.
    """
    key_path = signing_key_file
    if key_path is None:
        default_path = Path.home() / ".config" / "mlffbench" / "keys" / "candidate-agent-v2.key"
        if default_path.is_file():
            key_path = default_path

    if key_path is not None:
        key_p = Path(key_path).expanduser().resolve()
        if not key_p.is_file():
            raise RuntimeError(f"Maintainer signing key file does not exist: {key_p}")
        # Check permissions on POSIX
        stat_mode = key_p.stat().st_mode
        if stat_mode & 0o077 != 0:
            raise RuntimeError(
                f"Insecure private key file permissions on {key_p} ({oct(stat_mode)}). "
                "Private key must have permissions 0600 (-rw-------) or stricter."
            )
        content = key_p.read_text(encoding="utf-8").strip()
        if not content:
            raise RuntimeError(f"Private key file is empty: {key_p}")
        return content

    # Environment variable ingestion is permanently prohibited for candidate agent signing keys
    raise RuntimeError(
        "Maintainer private key file is required to seal qualification receipt. "
        "Provide --signing-key-file <path> or save key to ~/.config/mlffbench/keys/candidate-agent-v2.key (0600). "
        "Environment variable ingestion is permanently prohibited to prevent credential leakage into process logs."
    )


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="MLFFBench Candidate Agent Live Qualification Battery")
    parser.add_argument(
        "--signing-key-file",
        type=Path,
        default=None,
        help="Path to 0600 Ed25519 private key file (defaults to ~/.config/mlffbench/keys/candidate-agent-v2.key)",
    )
    args = parser.parse_args()

    print("=================================================================")
    print("MLFFBench Candidate Agent Live Qualification Battery: Claude Code")
    print("=================================================================")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"claude-code-{stamp}"
    evidence: dict[str, Any] = {}

    # 0. Enforce clean Git working tree before qualification
    status_proc = run_command_sync(["git", "status", "--porcelain"])
    if status_proc.returncode != 0:
        raise RuntimeError(
            f"Failed to execute git status --porcelain (exit code {status_proc.returncode}): {status_proc.stderr.strip()}"
        )
    dirty_entries = [l.strip() for l in status_proc.stdout.splitlines() if l.strip()]
    if dirty_entries:
        raise RuntimeError(
            f"Git working tree is dirty ({len(dirty_entries)} unstaged/untracked files detected):\n"
            + "\n".join(f"  {line}" for line in dirty_entries[:10])
            + "\nQualification receipt generation requires a completely clean Git working tree."
        )

    try:
        run_canary_1_image_isolation(evidence)
        run_canary_2_adversarial_containment(evidence)
        run_canary_3_skills_topology(evidence)
        run_canary_4_model_gateway_and_budget(evidence)
        run_canary_5_run_lock_and_schema(evidence)
        run_real_claude_code_canary(evidence)
        verify_container_cleanup(evidence)

        print("\n--- Handing over to Independent Verifier ---")
        verifier = CandidateAgentVerifier(workspace_root=ROOT)
        verdict = verifier.verify(evidence)

        signing_key_hex = resolve_maintainer_signing_key(args.signing_key_file)

        evidence_dir = Path.home() / ".config" / "mlffbench" / "evidence" / "gate_agent" / run_id
        receipt_file = verifier.seal_receipt(
            run_id=run_id,
            evidence=evidence,
            evidence_dir=evidence_dir,
            signing_key_hex=signing_key_hex,
            key_id="candidate-agent-v2",
        )

        # Immediate round-trip verification against trusted root
        if not verifier.verify_receipt_file(receipt_file):
            raise RuntimeError(
                f"Integrity check failed: sealed receipt {receipt_file} failed trust store verification!"
            )

        # Update latest receipt pointer under gate_agent/
        latest_pointer = Path.home() / ".config" / "mlffbench" / "evidence" / "gate_agent" / "claude_code_receipt.json"
        shutil.copyfile(receipt_file, latest_pointer)

        print("\n=================================================================")
        print(f"QUALIFICATION VERDICT: {verdict.status}")
        print(f"Agent Execution Status: {'PASS' if verdict.status == 'PROMOTED' else 'NOT_RUN'}")
        print(f"Formal Benchmark Readiness: {'READY' if verdict.status == 'PROMOTED' else 'BLOCKED'}")
        print(f"Independent Verifier Signature Validated (Ed25519)")
        print(f"Immutable Run Receipt: {receipt_file}")
        print(f"Latest Pointer Updated: {latest_pointer}")
        print("=================================================================")

    except Exception as exc:
        print(f"\nQUALIFICATION FAILED: {exc}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
