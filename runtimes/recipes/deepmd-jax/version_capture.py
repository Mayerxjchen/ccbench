#!/usr/bin/env python3
"""Capture the frozen software identity of dftworld-base-deepmd-jax.

Run inside the image:
    /opt/ai2kit/bin/python version_capture.py
Emits JSON to stdout. This is the G1 evidence that fills
cases/005-go-water-dpmp-potential/reference/compute-runtime.lock.json — no TBD may be left fabricated.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()

def version(mod, attr=None):
    try:
        m = __import__(mod)
        if attr:
            v = getattr(m, attr)
            return str(v) if not hasattr(v, "version") else str(v.version)
        from importlib.metadata import version as mv
        return mv(mod)
    except Exception:
        return "unknown"

info = {
    "image_tag": "dftworld-base-deepmd-jax",
    "python": sys.version.split()[0],
    "jax": version("jax", "__version__"),
    "jaxlib": version("jaxlib", "__version__"),
    "flax": version("flax", "__version__"),
    "optax": version("optax", "__version__"),
    "jax_md": version("jax-md"),
    "deepmd_jax": version("deepmd_jax"),
    "ai2_kit": version("ai2-kit"),
    "cp2k": sh("/opt/cp2k/bin/cp2k --version 2>/dev/null | grep -oE 'version [0-9.]+' | head -1"),
    "deepmd_jax_git": sh("cd /opt/deepmd-jax && git rev-parse HEAD 2>/dev/null"),
    "jax_md_git": sh("cd /opt/deepmd-jax && git ls-remote https://github.com/google/jax-md.git HEAD 2>/dev/null | cut -c1-12"),
    "env": {
        "JAX_ENABLE_X64": os.environ.get("JAX_ENABLE_X64"),
        "CP2K_DATA_DIR": os.environ.get("CP2K_DATA_DIR"),
    },
}
print(json.dumps(info, indent=2))
