#!/usr/bin/env python3
"""Build and capture CompShare Image A (mlff-matclaw-cips-gpu-v1) under strict Zero-Orphan control."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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

DEFAULT_RECIPE = _ROOT / "base-env-build" / "matclaw-cips-gpu" / "recipe.lock.json"
DEFAULT_RUNTIME_LOCK = _ROOT / "reference" / "runtime" / "matclaw-cips-runtime.lock.json"
BASE_IMAGE_ID = "compshareImage-17bl978pmsju"  # Cuda12.1_Py3.10 (Ubuntu 22.04, CUDA 12.1)
TARGET_IMAGE_NAME = "mlff-deepmd-gpu-v1"
DEEPMD_RUNTIME_LOCK = _ROOT / "reference" / "runtime" / "deepmd-runtime.lock.json"


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
    """Generate the remote installation and verification script to execute on the instance."""
    return """#!/usr/bin/env bash
set -euo pipefail

echo "=== [1/6] System prerequisites ==="
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
    libgomp1 libopenblas0 libgl1 libglib2.0-0 curl rsync ca-certificates

echo "=== [2/6] Install uv via pip or curl ==="
if ! command -v uv &>/dev/null; then
    pip install -i https://pypi.tuna.tsinghua.edu.cn/simple uv || curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="${HOME}/.local/bin:${PATH}"
fi

echo "=== [3/6] Create isolated virtualenv at /opt/matclaw ==="
mkdir -p /opt/matclaw
uv venv --python 3.11 /opt/matclaw

echo "=== [4/6] Install scientific stack ==="
UV_HTTP_TIMEOUT=1800 uv pip install --python /opt/matclaw/bin/python \\
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \\
    "deepmd-kit[lmp]==2.2.11" \\
    "tensorflow[and-cuda]==2.16.2" \\
    "numpy==1.26.4" \\
    "ase==3.26.0" \\
    "scipy==1.17.1" \\
    "matplotlib==3.11.1" \\
    "pymatgen==2026.5.4" \\
    "dpdata==0.2.25" \\
    "pytest==8.3.5"

echo "=== [5/6] Copy assets & verification probes ==="
mkdir -p /opt/matclaw/assets
cp /tmp/matclaw_stage/frozen_model.pb /opt/matclaw/assets/frozen_model.pb
cp /tmp/matclaw_stage/CuInP2S6.cif /opt/matclaw/assets/CuInP2S6.cif
cp /tmp/matclaw_stage/type_map.raw /opt/matclaw/assets/type_map.raw
cp /tmp/matclaw_stage/qualify_gpu.py /opt/matclaw/qualify_gpu.py

# Verify embedded asset integrity on host
printf '%s  %s\\n' \\
    'a3e7cf9c8168c649ee1ba6e39a7212f3f9db29fc0162b093beb908927e956b4d' \\
    '/opt/matclaw/assets/frozen_model.pb' | sha256sum -c -
printf '%s  %s\\n' \\
    'b9e3b0c4470274d5e3e1ce19e7c8834bda323e50483eef9791b1e744bbd629de' \\
    '/opt/matclaw/assets/CuInP2S6.cif' | sha256sum -c -
printf '%s  %s\\n' \\
    'ddd5c423f4f087be6301609fdfd5ac93a2241b3254f7bd732d33457b1f091d8e' \\
    '/opt/matclaw/assets/type_map.raw' | sha256sum -c -

echo "=== [6/6] Run GPU qualification probe ==="
export PATH="/opt/matclaw/bin:${PATH}"
export LAMMPS_PLUGIN_PATH="/opt/matclaw/lib/python3.11/site-packages/deepmd/lib"
/opt/matclaw/bin/python /opt/matclaw/qualify_gpu.py

echo "=== MatClaw GPU runtime build & verification succeeded! ==="
"""


def prepare_staging_dir(temp_dir: Path, recipe_doc: Mapping[str, Any], root: Path) -> Path:
    stage = temp_dir / "matclaw_stage"
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
    region: str = "cn-sh2",
    zone: str = "cn-sh2-02",
    gpu: str = "4090",
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
        # Materialize runtime locks to BUILT_NOT_QUALIFIED
        materialize_runtime_lock(
            recipe_path,
            out_path=runtime_lock_path,
            capability="matclaw-cips",
            image_id=existing_id,
            force=True,
        )
        materialize_runtime_lock(
            recipe_path,
            out_path=DEEPMD_RUNTIME_LOCK,
            capability="deepmd",
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
            print(f"[1/5] Creating temporary builder instance in {zone} ({gpu})...")
            create_args = [
                "instance", "create",
                "--region", region,
                "--zone", zone,
                "--gpu", gpu,
                "--count", "1",
                "--cpu", "16",
                "--memory", "64GiB",
                "--disk", "100GiB",
                "--image", BASE_IMAGE_ID,
                "--image-source", "platform",
                "--name", f"builder-{int(time.time())}",
                "--charge", "Postpay",
                "--wait", "--timeout", "600",
                "--yes",
            ]
            create_res = _run_cli_json(create_args)
            instance_info = create_res.get("data", {}).get("instance", {})
            instance_id = (
                _extract_instance_id(instance_info)
                or _extract_instance_id(create_res.get("data", {}))
            )
            if not instance_id:
                # Fallback query
                time.sleep(3)
                list_res = _run_cli_json(["instance", "list"])
                items = list_res.get("data", {}).get("items", [])
                if items:
                    instance_id = _extract_instance_id(items[0])
            if not instance_id:
                raise ImageBuildError("Instance creation succeeded but could not determine Instance ID")
            print(f"Builder instance created: {instance_id}")

        with tempfile.TemporaryDirectory() as tmp_str:
            tmp_path = Path(tmp_str)
            stage_dir = prepare_staging_dir(tmp_path, recipe_doc, root)

            print("[2/5] Uploading staging assets to builder instance...")
            # Upload stage directory
            _run_cli(["instance", "cp", instance_id, str(stage_dir), ":/tmp/matclaw_stage"])

            print("[3/5] Executing remote build and qualification probe on instance...")
            ret, stdout, stderr = _run_cli([
                "instance", "ssh", instance_id, "--", "bash", "/tmp/matclaw_stage/install.sh"
            ])
            print("Remote output:\n" + stdout)

            # Cleanup staging dir on remote instance
            _run_cli(["instance", "ssh", instance_id, "--", "rm", "-rf", "/tmp/matclaw_stage"])

        print(f"[4/5] Capturing instance {instance_id} as custom image {TARGET_IMAGE_NAME}...")
        img_create_args = [
            "image", "create",
            "--instance", instance_id,
            "--name", TARGET_IMAGE_NAME,
            "--description", "MLFFBench MatClaw CIPS GPU v1 (Cases 031-033)",
            "--wait", "--timeout", "1800",
            "--yes",
        ]
        img_res = _run_cli_json(img_create_args)
        created_image_id = (
            img_res.get("data", {}).get("image", {}).get("CompShareImageId")
            or img_res.get("data", {}).get("CompShareImageId")
        )

        if not created_image_id:
            # Query custom image list
            time.sleep(5)
            custom_imgs = _run_cli_json(["image", "list", "--source", "custom"])
            for itm in custom_imgs.get("data", {}).get("items", []):
                if itm.get("Name") == TARGET_IMAGE_NAME:
                    created_image_id = itm.get("CompShareImageId")
                    break

        if not created_image_id:
            raise ImageBuildError(f"Image creation submitted but failed to obtain CompShareImageId")

        print(f"Custom image created successfully: {created_image_id}")

    finally:
        if instance_id:
            print(f"[5/5] Tearing down builder instance {instance_id} (Zero-Orphan guarantee)...")
            try:
                _run_cli(["instance", "delete", instance_id, "--yes", "--force"], check=False)
                # Wait for deletion
                for _ in range(12):
                    time.sleep(5)
                    chk = _run_cli_json(["instance", "list", "--all"])
                    if not chk.get("data", {}).get("items", []):
                        break
            except Exception as del_exc:
                print(f"[WARNING] Error during instance deletion: {del_exc}", file=sys.stderr)

        # Final verification
        verify_zero_orphan()
        print("Zero-Orphan verification confirmed: 0 active instances.")

    # Materialize runtime locks
    if created_image_id:
        print(f"Materializing runtime locks with image_id={created_image_id}...")
        materialize_runtime_lock(
            recipe_path,
            out_path=runtime_lock_path,
            capability="matclaw-cips",
            image_id=created_image_id,
            force=True,
        )
        materialize_runtime_lock(
            recipe_path,
            out_path=DEEPMD_RUNTIME_LOCK,
            capability="deepmd",
            image_id=created_image_id,
            force=True,
        )
        print(f"[DONE] Gate B complete. Runtime locks updated to BUILT_NOT_QUALIFIED.")

    return {
        "status": "BUILT_SUCCESS",
        "image_id": created_image_id,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and capture CompShare Image A.")
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
