#!/usr/bin/env python3
"""Build and capture CompShare Image B (mlff-jax-gpu-v1) under strict Zero-Orphan control."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.infra.audit_compshare_image_recipe import audit_image_recipe
from scripts.infra.materialize_compshare_runtime_lock import materialize_runtime_lock

DEFAULT_RECIPE = _ROOT / "base-env-build" / "jax-gpu" / "recipe.lock.json"
DEFAULT_RUNTIME_LOCK = _ROOT / "reference" / "production-runtime" / "jax-runtime.lock.json"
BASE_IMAGE_ID = "compshareImage-17bl978pmsju"  # Ubuntu-nvidia 22.04
TARGET_IMAGE_NAME = "mlff-jax-gpu-v1"

CANDIDATE_POOLS = [
    {"region": "cn-wlcb", "zone": "cn-wlcb-01", "gpu": "4090", "cpu": "16", "memory": "64GiB"},
    {"region": "cn-bj2", "zone": "cn-bj2-03", "gpu": "4090", "cpu": "14", "memory": "64GiB"},
    {"region": "cn-sh2", "zone": "cn-sh2-02", "gpu": "4090", "cpu": "16", "memory": "64GiB"},
]


class ImageBuildError(RuntimeError):
    """Failure during remote image build or qualification."""


def _resolve_cli_bin() -> str:
    venv_bin = _ROOT / ".venv" / "bin" / "compshare"
    if venv_bin.is_file():
        return str(venv_bin)
    system_bin = shutil.which("compshare")
    if system_bin:
        return system_bin
    raise ImageBuildError("compshare CLI not found in .venv/bin or PATH")


def _run_cli(args: list[str], *, check: bool = True) -> tuple[int, str, str]:
    cli = _resolve_cli_bin()
    cmd = [cli] + args
    res = subprocess.run(cmd, capture_output=True, text=True)
    if check and res.returncode != 0:
        raise ImageBuildError(
            f"Command failed ({res.returncode}): {' '.join(cmd)}\nStdout: {res.stdout}\nStderr: {res.stderr}"
        )
    return res.returncode, res.stdout, res.stderr


def _run_cli_retry(
    args: list[str],
    *,
    max_retries: int = 4,
    delay: int = 5,
    check: bool = True,
) -> tuple[int, str, str]:
    last_ret, last_stdout, last_stderr = 1, "", ""
    for attempt in range(1, max_retries + 1):
        code, stdout, stderr = _run_cli(args, check=False)
        last_ret, last_stdout, last_stderr = code, stdout, stderr
        if code == 0:
            return code, stdout, stderr
        print(f"[RETRY] CLI call failed ({code}) on attempt {attempt}/{max_retries}: {' '.join(args[:4])}...")
        print(f"--- [STDOUT ATTEMPT {attempt}] ---\n{stdout}\n--- [STDERR ATTEMPT {attempt}] ---\n{stderr}")
        time.sleep(delay)
    if check:
        raise ImageBuildError(
            f"CLI call failed after {max_retries} attempts ({last_ret}): {' '.join(args)}\nStdout: {last_stdout}\nStderr: {last_stderr}"
        )
    return last_ret, last_stdout, last_stderr


def _run_cli_json(args: list[str]) -> dict[str, Any]:
    args_with_json = ["--json"] + [a for a in args if a != "--json"]
    _, stdout, stderr = _run_cli(args_with_json, check=True)
    try:
        data = json.loads(stdout)
    except Exception as exc:
        raise ImageBuildError(f"Failed to parse CLI JSON output: {exc}\nRaw: {stdout}") from exc
    if not data.get("ok", False):
        err = data.get("error", {})
        raise ImageBuildError(f"CLI returned error response: {err}")
    return data


def _extract_instance_id(item: Mapping[str, Any]) -> str | None:
    return item.get("UHostId") or item.get("CompShareInstanceId") or item.get("InstanceId")


def verify_zero_orphan(allowed_instance_id: str | None = None) -> None:
    """Ensure no unexpected running or lingering instances exist."""
    data = _run_cli_json(["instance", "list", "--all"])
    items = data.get("data", {}).get("items", [])
    unexpected = [
        _extract_instance_id(item) or "unknown"
        for item in items
        if allowed_instance_id is None or _extract_instance_id(item) != allowed_instance_id
    ]
    if unexpected:
        raise ImageBuildError(
            f"Zero-Orphan check failed: Found unexpected instances on account: {unexpected}"
        )


def build_remote_install_script() -> str:
    """Generate the remote installation and verification script for JAX-GPU."""
    return """#!/usr/bin/env bash
set -euo pipefail

echo "=== [1/7] System prerequisites and Python 3.11 via deadsnakes PPA ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \\
    software-properties-common curl rsync ca-certificates build-essential git libgomp1 libopenblas0

add-apt-repository -y ppa:deadsnakes/ppa
apt-get update -y
apt-get install -y --no-install-recommends \\
    python3.11 python3.11-venv python3.11-dev python3-pip

echo "=== [2/7] Install uv ==="
export PATH="${HOME}/.local/bin:/root/.local/bin:/usr/local/bin:${PATH}"
if ! command -v uv &>/dev/null; then
    python3.11 -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple uv || pip3 install -i https://pypi.tuna.tsinghua.edu.cn/simple uv || curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="${HOME}/.local/bin:/root/.local/bin:/usr/local/bin:${PATH}"
fi

echo "=== [3/7] Create isolated virtualenv at /opt/jax-gpu ==="
rm -rf /opt/jax-gpu
uv venv --clear --python /usr/bin/python3.11 /opt/jax-gpu

echo "=== [4/7] Install locked wheel dependencies ==="
UV_HTTP_TIMEOUT=1800 uv pip install --python /opt/jax-gpu/bin/python \\
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \\
    -r /tmp/jax_stage/requirements.lock

echo "=== [5/7] Install JAX-MD and DeePMD-JAX from frozen source tarballs ==="
tar -xzf /tmp/jax_stage/jax_md-0.2.29.tar.gz -C /tmp/
tar -xzf /tmp/jax_stage/deepmd_jax-0.2.tar.gz -C /tmp/
UV_HTTP_TIMEOUT=1800 uv pip install --python /opt/jax-gpu/bin/python --no-deps /tmp/jax-md
UV_HTTP_TIMEOUT=1800 uv pip install --python /opt/jax-gpu/bin/python --no-deps /tmp/deepmd-jax
rm -rf /tmp/jax-md /tmp/deepmd-jax

echo "=== [6/7] Persist assets and probes into /opt/jax-gpu ==="
mkdir -p /opt/jax-gpu/assets /opt/jax-gpu/probes
cp /tmp/jax_stage/deepmd_jax-0.2.tar.gz /opt/jax-gpu/assets/
cp /tmp/jax_stage/jax_md-0.2.29.tar.gz /opt/jax-gpu/assets/
cp /tmp/jax_stage/qualify_jax.py /opt/jax-gpu/probes/qualify_jax.py

# Verify embedded asset integrity
printf '%s  %s\\n' \\
    'f6a4de451d24ef1d540b6935b5ad56e65af860401a1e03247426a02d60cdba15' \\
    '/opt/jax-gpu/assets/deepmd_jax-0.2.tar.gz' | sha256sum -c -
printf '%s  %s\\n' \\
    'b50c7318305db3deab3f033190210239dd275d73b0c0f06e02cd8a51463dd638' \\
    '/opt/jax-gpu/assets/jax_md-0.2.29.tar.gz' | sha256sum -c -
printf '%s  %s\\n' \\
    '52f789a162b27a8c8e0bc5b9ba6b7cdbd3bededb407c41ff4b38c90de87c7f2a' \\
    '/opt/jax-gpu/probes/qualify_jax.py' | sha256sum -c -

# Grant full read and execution permissions across system
chmod -R a+rX /opt/jax-gpu
find /opt/jax-gpu/bin -type f -exec chmod a+rx {} +

echo "=== [7/7] Execute smoke verification probe ==="
export PATH="/opt/jax-gpu/bin:${PATH}"
export JAX_ENABLE_X64=1
export CUDA_VISIBLE_DEVICES=0

/opt/jax-gpu/bin/python /opt/jax-gpu/probes/qualify_jax.py --json-out /tmp/smoke_report.json

cat /tmp/smoke_report.json

# Cleanup temporary files to minimize image footprint
rm -rf /root/.cache /tmp/jax_stage
apt-get clean
rm -rf /var/lib/apt/lists/*

echo "=== JAX GPU runtime build & verification succeeded! ==="
"""


def prepare_staging_dir(temp_dir: Path, recipe_doc: Mapping[str, Any], root: Path) -> Path:
    stage = temp_dir / "jax_stage"
    stage.mkdir(parents=True, exist_ok=True)

    # Copy requirements.lock
    req_rel = recipe_doc.get("requirements_lock", {}).get("path")
    shutil.copy2(root / req_rel, stage / "requirements.lock")

    # Copy assets
    for asset in recipe_doc.get("assets", []):
        shutil.copy2(root / asset["path"], stage / asset["name"])

    # Copy probes
    for probe in recipe_doc.get("probes", []):
        shutil.copy2(root / probe["path"], stage / probe["name"])

    # Write installer script
    script_path = stage / "install.sh"
    script_path.write_text(build_remote_install_script(), encoding="utf-8")
    script_path.chmod(0o755)

    return stage


def execute_build(
    *,
    recipe_path: Path = DEFAULT_RECIPE,
    runtime_lock_path: Path = DEFAULT_RUNTIME_LOCK,
    dry_run: bool = False,
    reuse_instance: str | None = None,
) -> dict[str, Any]:
    """Execute complete build workflow with strict fail-closed and Zero-Orphan guarantees."""
    root = _ROOT

    # 1. Audit recipe
    audit_res = audit_image_recipe(recipe_path, repo_root=root)
    recipe_doc = json.loads(recipe_path.read_text(encoding="utf-8"))

    # 2. Check Zero-Orphan (allowing reuse_instance if explicitly passed)
    verify_zero_orphan(allowed_instance_id=reuse_instance)

    # 3. Check if target image already exists in custom images
    img_list = _run_cli_json(["image", "list", "--source", "custom"])
    existing = [
        item for item in img_list.get("data", {}).get("items", [])
        if item.get("Name") == TARGET_IMAGE_NAME
    ]
    if existing:
        existing_id = existing[0].get("CompShareImageId")
        print(f"Target custom image already exists: {TARGET_IMAGE_NAME} -> {existing_id}")
        materialize_runtime_lock(
            recipe_path,
            out_path=runtime_lock_path,
            capability="jax",
            image_id=existing_id,
            force=True,
        )
        return {"status": "ALREADY_EXISTS", "image_id": existing_id}

    if dry_run:
        print("[DRY-RUN] Preflight checks passed. Zero-Orphan verified. Assets validated.")
        return {"status": "DRY_RUN_PASSED"}

    instance_id: str | None = reuse_instance
    created_image_id: str | None = None

    try:
        if instance_id:
            print(f"[1/5] Reusing existing builder instance: {instance_id}")
        else:
            # Try candidate resource pools
            last_err = None
            for pool in CANDIDATE_POOLS:
                region, zone, gpu = pool["region"], pool["zone"], pool["gpu"]
                cpu, memory = pool["cpu"], pool["memory"]
                print(f"[1/5] Attempting to create temporary builder in {region}/{zone} ({gpu}, {cpu}C/{memory})...")
                create_args = [
                    "instance", "create",
                    "--region", region,
                    "--zone", zone,
                    "--gpu", gpu,
                    "--count", "1",
                    "--cpu", cpu,
                    "--memory", memory,
                    "--disk", "100GiB",
                    "--image", BASE_IMAGE_ID,
                    "--image-source", "platform",
                    "--name", f"builder-jax-{int(time.time())}",
                    "--charge", "Postpay",
                    "--wait", "--timeout", "600",
                    "--yes",
                ]
                try:
                    create_res = _run_cli_json(create_args)
                    instance_info = create_res.get("data", {}).get("instance", {})
                    instance_id = (
                        _extract_instance_id(instance_info)
                        or _extract_instance_id(create_res.get("data", {}))
                    )
                    if not instance_id:
                        time.sleep(3)
                        list_res = _run_cli_json(["instance", "list"])
                        items = list_res.get("data", {}).get("items", [])
                        if items:
                            instance_id = _extract_instance_id(items[0])
                    if instance_id:
                        print(f"Builder instance created successfully in {zone}: {instance_id}")
                        break
                except Exception as exc:
                    print(f"Pool {zone}/{gpu} not available: {exc}. Trying next pool...")
                    last_err = exc

            if not instance_id:
                raise ImageBuildError(f"Failed to create builder instance across all candidate pools: {last_err}")

            # Wait for SSH to stabilize
            print(f"Waiting for SSH daemon to stabilize on {instance_id}...")
            ssh_ready = False
            consecutive = 0
            for attempt in range(1, 25):
                ret, out, err = _run_cli(
                    ["instance", "ssh", instance_id, "--", "echo", "SSH_READY"],
                    check=False,
                )
                if ret == 0 and "SSH_READY" in out:
                    consecutive += 1
                    if consecutive >= 2:
                        print(f"SSH stabilized on attempt {attempt}.")
                        ssh_ready = True
                        break
                else:
                    consecutive = 0
                time.sleep(5)
            if not ssh_ready:
                raise ImageBuildError(f"SSH failed to stabilize on {instance_id}")

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp_path = Path(tmp_str)
            stage_dir = prepare_staging_dir(tmp_path, recipe_doc, root)

            print("[2/5] Uploading staging assets to builder instance...")
            _run_cli_retry(["instance", "cp", instance_id, str(stage_dir), ":/tmp/jax_stage"])

            print("[3/5] Executing remote build and qualification probe on instance with sudo...")
            ret, stdout, stderr = _run_cli_retry([
                "instance", "ssh", instance_id, "--", "bash", "/tmp/jax_stage/install.sh"
            ])
            print("Remote output:\n" + stdout)

        print(f"[4/5] Capturing instance {instance_id} as custom image {TARGET_IMAGE_NAME}...")
        img_create_args = [
            "image", "create",
            "--instance", instance_id,
            "--name", TARGET_IMAGE_NAME,
            "--description", "MLFFBench JAX GPU v1 (Case 042: GO-water DPMP)",
            "--wait", "--timeout", "1800",
            "--yes",
        ]
        img_res = _run_cli_json(img_create_args)
        created_image_id = (
            img_res.get("data", {}).get("image", {}).get("CompShareImageId")
            or img_res.get("data", {}).get("CompShareImageId")
        )

        if not created_image_id:
            time.sleep(5)
            custom_imgs = _run_cli_json(["image", "list", "--source", "custom"])
            for itm in custom_imgs.get("data", {}).get("items", []):
                if itm.get("Name") == TARGET_IMAGE_NAME:
                    created_image_id = itm.get("CompShareImageId")
                    break

        if not created_image_id:
            raise ImageBuildError("Image creation submitted but failed to obtain CompShareImageId")

        print(f"Custom image created successfully: {created_image_id}")

    finally:
        if instance_id:
            print(f"[5/5] Tearing down builder instance {instance_id} (Zero-Orphan guarantee)...")
            try:
                _run_cli(["instance", "delete", instance_id, "--yes", "--release-disk", "--wait"], check=False)
                for _ in range(15):
                    time.sleep(5)
                    chk = _run_cli_json(["instance", "list", "--all"])
                    if not chk.get("data", {}).get("items", []):
                        break
            except Exception as del_exc:
                print(f"[WARNING] Error during instance deletion: {del_exc}", file=sys.stderr)

        # Final verification
        verify_zero_orphan()
        print("Zero-Orphan verification confirmed: 0 active instances.")

    # Materialize runtime lock to BUILT_NOT_QUALIFIED
    if created_image_id:
        print(f"Materializing production runtime lock with image_id={created_image_id}...")
        materialize_runtime_lock(
            recipe_path,
            out_path=runtime_lock_path,
            capability="jax",
            image_id=created_image_id,
            force=True,
        )
        print(f"[DONE] Gate B complete. Production runtime lock updated to BUILT_NOT_QUALIFIED.")

    return {
        "status": "BUILT_SUCCESS",
        "image_id": created_image_id,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and capture CompShare Image B (JAX).")
    parser.add_argument("--dry-run", action="store_true", help="Run local preflight checks only")
    parser.add_argument("--execute", action="store_true", help="Execute real cloud instance build")
    parser.add_argument("--reuse-instance", default=None, help="Reuse an already-running builder instance ID")
    args = parser.parse_args()

    if not args.execute and not args.dry_run:
        print("Must specify either --dry-run or --execute", file=sys.stderr)
        return 1

    try:
        execute_build(dry_run=args.dry_run, reuse_instance=args.reuse_instance)
        return 0
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
