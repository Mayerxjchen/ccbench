import hashlib
import json
from pathlib import Path

case = Path("/Users/xjchen/bench/mlffbench/experiments/case-builder-luna-v2/generated/902-mvp-cips-curie-temperature")

def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

# 1) fill PENDING in input-manifest
im_path = case / "public" / "input-manifest.json"
im = json.loads(im_path.read_text())
for e in im["files"]:
    p = case / e["case_source"]
    if e.get("sha256") == "PENDING" or e.get("size_bytes", -1) < 0:
        e["sha256"] = sha(p)
        e["size_bytes"] = p.stat().st_size
im_path.write_text(json.dumps(im, indent=2, sort_keys=True) + "\n", encoding="utf-8")

# 2) reference/source.lock.json
public_files = {}
for p in sorted((case / "public").iterdir()):
    if p.is_file() and p.name != ".gitkeep":
        public_files["public/" + p.name] = {"sha256": sha(p), "size_bytes": p.stat().st_size}
lock = {
    "schema_version": 1,
    "public_input": {"files": public_files},
    "hidden_validation": {"files": {
        "tests/hidden/teacher-digest.json": {"sha256": sha(case / "tests" / "hidden" / "teacher-digest.json")},
        "public/teacher_model.pb": {"sha256": sha(case / "public" / "teacher_model.pb")},
    }},
    "note": "public inputs + sealed model-authenticity data; hidden reference sets are planned (upstream trajectories missing) and will be appended under G-track gates",
}
(case / "reference" / "source.lock.json").write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print("public files:", sorted(public_files))
print("lock written")
