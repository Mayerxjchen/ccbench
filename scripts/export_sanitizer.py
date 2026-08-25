#!/usr/bin/env python3
"""One-shot export sanitizer: copy the tracked tree to a clean export dir.

Replaces site identifiers (cluster host alias, internal IP, username, home
paths, node names) with neutral placeholders.  Functional code paths derive
these values from the cluster profile at runtime, so the sanitized export is
still runnable once the operator fills in their own profile.

Rules (applied to every exported file's text):
- ``xjchen``                     -> ``<site-user>``
- ``10.26.14.64``                -> ``<site-host-ip>``
- ``ikkemhpc`` / ``ikkem``       -> ``<site-alias>`` (case variants kept)
- ``/public/home/xjchen/...``    -> ``/public/home/<site-user>/...``
- ``mu012``, ``gpu001..gpu006``  -> node placeholders
- ``pytest-of-xjchen``           -> covered by the xjchen rule

The public GitHub username (Mayerxjchen) is intentionally preserved.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1]
DST = SRC.parent / "dftworld2-export"

# Files that must never be exported verbatim: private site config gets a
# template instead.
TEMPLATE_FILES = {
    "scripts/hpc/cluster_profile.toml": """\
# cluster_profile.toml - site-specific HPC configuration template.
#
# Copy this file OUTSIDE the repository (it is operator-private), fill in
# every value for YOUR cluster, and pass its path via --profile.  All keys
# are REQUIRED; a missing or wrong-typed key fails fast.
# The SIF path and SHA live in the runtime lock (F3), not here.

[ssh]
host = "<site-alias>"          # ssh-config alias of your login node
user = ""                      # empty = take it from ~/.ssh/config
port = 22

[ssh.options]
# Per-invocation -o options (host ssh config is mounted read-only).

[paths]
remote_root = "/public/home/<site-user>/dftworld2-runs/matclaw-031"
apptainer = "/path/to/apptainer"

[slurm]
partition = "gpu"              # gpu-class queue name at your site
cpu_partition = "cpu"          # native cpu queue (empty = ACL-era routing)
gres = "gpu:1"
cpus_per_task = "8"
nodes = "1"

[runtime]
expected_node_arch = "x86_64"
""",
}


def git_ls_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=SRC, capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


RULES = [
    # Order matters: longest/most specific first.
    (re.compile(r"/public/home/xjchen"), "/public/home/<site-user>"),
    (re.compile(r"\bxjchen@10\.26\.14\.64(:22)?\b"), "<site-user>@<site-host-ip>"),
    (re.compile(r"\b10\.26\.14\.64\b"), "<site-host-ip>"),
    (re.compile(r"\bikkemhpc\b"), "<site-alias>"),
    (re.compile(r"\bikkem-v1\b"), "site-v1"),
    (re.compile(r"test_ikkemhpc_"), "test_site_"),
    (re.compile(r"_ssh_real_ikkem"), "_ssh_real_site"),
    (re.compile(r"test_real_ikkem_"), "test_real_site_"),
    (re.compile(r"\bikkem\b", re.I), "<site-alias>"),
    (re.compile(r"\bai4ecaig\b"), "acct-alpha"),
    (re.compile(r"\bai4ecall\b"), "acct-all"),
    (re.compile(r"\bai4ecccg\b"), "acct-gamma"),
    (re.compile(r"\bdpikkem\b"), "dp-site"),
    (re.compile(r"\bai4ec/"), "benchmark/"),
    (re.compile(r"\bai4eccsc\b"), "acct-blocked"),
    (re.compile(r"\bai4ec\b"), "acct-delta"),
    (re.compile(r"嘉庚智算"), "the HPC site"),
    (re.compile(r"嘉庚"), "the site"),
    (re.compile(r"\bmu012\b"), "<site-login-node>"),
    (re.compile(r"\bgpu00([1-6])\b"), r"<site-node-gpu\1>"),
    (re.compile(r"\bxjchen\b"), "<site-user>"),
]

SKIP_SUFFIXES = {".png", ".jpg", ".pdf", ".zip", ".gz", ".sif", ".model", ".pt",
                 ".pb", ".whl", ".zst", ".tar", ".docx"}
SKIP_DIRS = {"__pycache__", ".git", "node_modules", "dftworld2-export",
             "active-work"}


SKIP_FILES = {"scripts/export_sanitizer.py"}  # carries the rules themselves


def main() -> int:
    if DST.exists():
        print(f"refusing to overwrite existing export dir: {DST}")
        return 1
    DST.mkdir(parents=True)

    files = git_ls_files()
    changed = untouched = templated = binary_skipped = 0
    for rel in files:
        parts = Path(rel).parts
        if any(d in SKIP_DIRS for d in parts):
            continue
        src_path = SRC / rel
        dst_path = DST / rel
        if not src_path.is_file():
            continue
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        if rel in SKIP_FILES:
            shutil.copy2(src_path, dst_path)
            continue
        if rel in TEMPLATE_FILES:
            dst_path.write_text(TEMPLATE_FILES[rel], encoding="utf-8")
            templated += 1
            continue
        if src_path.suffix.lower() in SKIP_SUFFIXES:
            shutil.copy2(src_path, dst_path)
            binary_skipped += 1
            continue
        try:
            text = src_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            shutil.copy2(src_path, dst_path)
            binary_skipped += 1
            continue
        new = text
        for pattern, repl in RULES:
            new = pattern.sub(repl, new)
        dst_path.write_text(new, encoding="utf-8")
        if new != text:
            changed += 1
        else:
            untouched += 1

    print(f"files total={len(files)} changed={changed} unchanged={untouched} "
          f"templated={templated} binary-copied={binary_skipped}")

    # Residual scan over the whole export.
    residual = []
    pat = re.compile(r"xjchen|10\.26\.14\.64|ikkem|mu012|gpu00[1-6]")
    for p in DST.rglob("*"):
        if not p.is_file() or p.suffix.lower() in SKIP_SUFFIXES:
            continue
        if Path(p.relative_to(DST)).as_posix() in SKIP_FILES:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            m = pat.search(line)
            if m and "Mayerxjchen" not in line and "mayerxjchen" not in line.lower():
                residual.append(f"{p.relative_to(DST)}:{i}: ...{line.strip()[:90]}")
    if residual:
        print(f"RESIDUAL SITE IDENTIFIERS ({len(residual)}):")
        for line in residual[:30]:
            print(" ", line)
        return 1
    print("residual scan: CLEAN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
