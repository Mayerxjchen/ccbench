#!/usr/bin/env python3
"""Pre-gate manual mirror of check_discovery_runnable.check_mount_smoke.

Local unit validation of the case verifier entry (authorized: the Skill's own
scripts + local validation). Does NOT touch the case tree beyond temp dirs.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CASE = Path("/Users/xjchen/bench/mlffbench/experiments/case-builder-luna-v2/generated/902-mvp-cips-curie-temperature")
TESTS = CASE / "tests"
PY_BIN_DIR = str(Path(sys.executable).parent)


def run(tmp: Path, submission, name, strip=()):
    mount = tmp / f"mount-{name}" / "tests"
    shutil.copytree(TESTS, mount)
    for rel in strip:
        v = mount / rel
        if v.is_file():
            v.unlink()
    result_dir = tmp / f"logs-{name}" / "verifier"
    env = dict(os.environ)
    env["PATH"] = f"{PY_BIN_DIR}:/usr/bin:/bin:/usr/local/bin"
    if submission is not None:
        env["SUBMISSION_ROOT"] = str(submission)
    else:
        env.pop("SUBMISSION_ROOT", None)
    env["RESULT_DIR"] = str(result_dir)
    env["BENCH_RUN_ID"] = f"mvp-{name}"
    subprocess.run(["bash", str(mount / "test.sh")], text=True, capture_output=True, env=env, cwd=tmp)
    rp = result_dir / "result.json"
    return json.loads(rp.read_text()) if rp.is_file() else {"MISSING": True}


def staged(tmp, name):
    dest = tmp / f"submission-{name}"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(TESTS / "fixtures" / name, dest)
    return dest


ok = True
with tempfile.TemporaryDirectory(prefix="pre-smoke-") as td:
    tmp = Path(td)
    empty = tmp / "submission-empty"
    empty.mkdir()
    checks = [
        ("empty", run(tmp, empty, "empty"), "AGENT_FAILURE", "NO_SUBMISSION"),
        ("forged", run(tmp, staged(tmp, "negative/forged-manifest"), "forged"), "AGENT_FAILURE", "SCIENTIFIC_FAIL"),
        ("missing-model", run(tmp, staged(tmp, "negative/missing-model"), "missing-model"), "AGENT_FAILURE", "SCIENTIFIC_FAIL"),
        ("broken-lineage", run(tmp, staged(tmp, "negative/broken-lineage"), "broken-lineage"), "AGENT_FAILURE", "SCIENTIFIC_FAIL"),
        ("structural", run(tmp, staged(tmp, "positive/structural-minimal"), "structural"), "VALID_RESULT", "PASS"),
        ("alt-valid", run(tmp, staged(tmp, "alternative-valid/alt-hybrid-analysis"), "alt-valid"), "VALID_RESULT", "PASS"),
        ("broken-entry", run(tmp, empty, "broken-entry", strip=("verifier.py",)), "INFRA_INVALID", "HARNESS_FAILURE"),
    ]
    for name, res, cls, code in checks:
        got_cls, got_code = res.get("result_class"), res.get("failure_code")
        reason = str(res.get("reason", ""))
        good = got_cls == cls and got_code == code
        if good and cls == "AGENT_FAILURE" and code == "SCIENTIFIC_FAIL":
            good = "V" in reason
        if good and cls == "AGENT_FAILURE" and code == "NO_SUBMISSION":
            good = "empty" in reason and "submission" in reason
        if good and cls == "VALID_RESULT":
            good = "deferred" in reason
        print(f"{'OK ' if good else 'BAD'} {name}: {got_cls}/{got_code} reason[:110]={reason[:110]!r}")
        ok = ok and good
print("PRE-SMOKE:", "ALL PASS" if ok else "FAILURES PRESENT")
sys.exit(0 if ok else 1)
