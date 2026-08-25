#!/bin/bash
# Restartable MatClaw Case 032 reference runner.
#
# Runs the hidden paper/smoke protocol inside a PERSISTENT container so a single
# logical run survives many docker exec sessions. The graded workspace (bind
# mounted at /app) and the case solution (bind mounted readonly at /solution)
# live on the host, so killing an exec — by budget timeout, by the caller, or by
# an HPC controller — never loses work: re-invoking with the same --output
# resumes from workspace/checkpoint.json.
#
# Resume semantics (see solution/run_curie.py and solution/curie_utils.py):
#   * checkpoint.json present and identity (profile/seed) matching  -> resume.
#   * no checkpoint.json (or --reset)                                -> fresh start:
#     the workspace is wiped and re-staged from public/ so the run begins from
#     the locked initial state + seed.
#   * a *.partial.traj is never reused: the exact expected-frames gate in
#     load_completed_temperature() rejects a torn/partial trajectory (e.g. a
#     1499-frame pilot vs the 2501-frame contract), so run_curie.py discards it
#     and re-runs that temperature from the locked initial structure + seed.
#
# CLI:
#   run_matclaw_reference_restartable.sh \
#     --case 032 --profile smoke|paper --seed INT --image IMAGE \
#     --output ABS_PATH \
#     [--gpus DEVICE] [--budget SECONDS] [--max-exec N] [--container NAME] [--reset]
#
#   --budget SECONDS  bound each exec session with in-container `timeout`; the
#                     exec is killed but the container and workspace survive.
#   --max-exec N      run at most N exec sessions per invocation (default 1).
#   --reset           force a fresh start (wipes the workspace) even if a
#                     checkpoint exists.
#
# Exit codes:
#   0  run complete (result.json present and checkpoint stage = "result").
#   1  exec / script failure (needs inspection, not cleanly resumable).
#   2  incomplete but resumable (budget/max-exec reached or session busy):
#      re-invoke with the same arguments to continue from the checkpoint.
#   3  usage / workspace-identity error (never resume into a different run).
set -euo pipefail

case_id=""
profile=""
seed=""
image=""
output=""
gpu_device=""
budget=""
max_exec="1"
container_override=""
reset=false

usage() {
  sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --case) case_id="$2"; shift 2 ;;
    --profile) profile="$2"; shift 2 ;;
    --seed) seed="$2"; shift 2 ;;
    --image) image="$2"; shift 2 ;;
    --output) output="$2"; shift 2 ;;
    --gpus) gpu_device="$2"; shift 2 ;;
    --budget) budget="$2"; shift 2 ;;
    --max-exec) max_exec="$2"; shift 2 ;;
    --container) container_override="$2"; shift 2 ;;
    --reset) reset=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 3 ;;
  esac
done

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# This runner is currently wired for Case 032 only (the case with the resumable
# curie paper protocol); other cases would need their own solve/verify contract.
if [[ "$case_id" != "032" ]]; then
  echo "--case must be 032 (restartable runner is wired for Case 032 only)" >&2
  exit 3
fi
case_dir="$repo_root/032-matclaw-cips-curie-temperature"

for required in "$profile" "$seed" "$image" "$output"; do
  test -n "$required" || { echo "--profile/--seed/--image/--output are required" >&2; usage >&2; exit 3; }
done
[[ "$profile" == "smoke" || "$profile" == "paper" ]] || { echo "--profile must be smoke|paper" >&2; exit 3; }
test "${output#/}" != "$output" || { echo "--output must be absolute" >&2; exit 3; }
if [[ -n "$budget" ]]; then
  [[ "$budget" =~ ^[0-9]+$ ]] && [[ "$budget" -gt 0 ]] || { echo "--budget must be a positive integer" >&2; exit 3; }
fi
[[ "$max_exec" =~ ^[0-9]+$ ]] && [[ "$max_exec" -ge 1 ]] || { echo "--max-exec must be an integer >= 1" >&2; exit 3; }

workspace="$output/workspace"

log() { echo "[restartable] $*" >&2; }

# --- Completion: result.json present AND checkpoint stage = result. ----------
# stage="result" is only set by run_curie.py after all trajectories are committed
# and result.json is written, so it is the authoritative completion signal.
is_complete() {
  python3 - "$1" <<'PY'
import json, sys
from pathlib import Path
ws = Path(sys.argv[1])
if not (ws / "result.json").is_file():
    sys.exit(1)
try:
    ckpt = json.loads((ws / "checkpoint.json").read_text())
except Exception:
    sys.exit(1)
sys.exit(0 if ckpt.get("stage") == "result" else 1)
PY
}

# --- Resolve fresh vs resume. -------------------------------------------------
mode=""
if [[ "$reset" == true ]]; then
  mode="fresh"
  log "fresh start requested (--reset)"
elif [[ -f "$workspace/checkpoint.json" ]]; then
  mode="resume"
  # Never resume into a run with different profile/seed: that would silently mix
  # a stale checkpoint's trajectories with a new protocol.
  python3 - "$workspace/checkpoint.json" "$profile" "$seed" <<'PY'
import json, sys
try:
    ckpt = json.load(open(sys.argv[1]))
except Exception as exc:
    print(f"refusing to resume: unreadable checkpoint.json: {exc}", file=sys.stderr)
    sys.exit(3)
for key, want in (("profile", sys.argv[2]), ("seed", sys.argv[3])):
    got = ckpt.get(key)
    if str(got) != str(want):
        print(f"refusing to resume: checkpoint {key}={got!r} != requested {want!r}; "
              f"use a fresh --output or --reset", file=sys.stderr)
        sys.exit(3)
PY
  log "resume: checkpoint.json present; continuing from committed trajectories"
else
  mode="fresh"
fi

if [[ "$mode" == "fresh" ]]; then
  # Clear the workspace CONTENTS in place (never remove the dir itself): an
  # already-running container has /app bind-mounted to this dir, and deleting
  # then recreating the dir can orphan that mount on some Docker backends.
  mkdir -p "$workspace" "$output/artifacts"
  find "$workspace" -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
  cp -a "$case_dir/public/." "$workspace/"
  log "fresh start: wiped workspace and staged public/ from $case_dir/public"
fi

# If the run is already complete there is nothing to do (no docker involvement).
if is_complete "$workspace"; then
  log "run already complete; nothing to do"
  completed=1; sessions=0; last_exit=0
  python3 - "$output" "$case_id" "$profile" "$seed" "$image" "$mode" "$sessions" "$last_exit" "$completed" "${container_override:-none}" <<'PY'
import json, sys
from datetime import datetime, timezone
from pathlib import Path
output, case_id, profile, seed = sys.argv[1:5]
image, mode, sessions = sys.argv[5:8]
last_exit, completed, container = sys.argv[8:11]
status = {"case": case_id, "profile": profile, "seed": int(seed), "image": image,
          "output": output, "mode": mode, "sessions": int(sessions),
          "last_exit": int(last_exit), "completed": bool(int(completed)),
          "result_json": (Path(output) / "workspace" / "result.json").is_file(),
          "checkpoint_stage": "result", "container": container,
          "updated_at": datetime.now(timezone.utc).isoformat()}
Path(output).mkdir(parents=True, exist_ok=True)
(Path(output) / "run_status.json").write_text(json.dumps(status, indent=2) + "\n")
print(f"status: completed={status['completed']} mode={status['mode']} sessions={status['sessions']}")
PY
  exit 0
fi

# --- Persistent container management. -----------------------------------------
container="${container_override:-matclaw-${case_id}-ref-$(printf '%s' "$output" | shasum -a 256 | cut -c1-12)}"

if ! docker inspect "$container" >/dev/null 2>&1; then
  log "creating persistent container $container"
  run_args=(run -d --name "$container")
  run_args+=(--mount "type=bind,src=$workspace,dst=/app")
  run_args+=(--mount "type=bind,src=$case_dir/solution,dst=/solution,readonly")
  if [[ -n "$gpu_device" ]]; then
    run_args+=(--gpus "device=$gpu_device")
  fi
  run_args+=("$image" sleep infinity)
  docker "${run_args[@]}"
elif ! docker inspect -f '{{.State.Running}}' "$container" | grep -q true; then
  log "starting existing container $container"
  docker start "$container"
fi

image_now="$(docker inspect -f '{{.Config.Image}}' "$container")"
if [[ "$image_now" != "$image" ]]; then
  echo "container $container was created with image '$image_now' but --image '$image' was given" >&2
  echo "use a fresh --output, --reset, or remove the container first" >&2
  exit 3
fi

# --- Exec loop: run up to max_exec bounded sessions, resuming each time. ------
sessions=0
last_exit=""
while true; do
  if is_complete "$workspace"; then
    log "run complete"
    last_exit=0
    break
  fi
  if (( sessions >= max_exec )); then
    log "incomplete after $sessions session(s) (max-exec=$max_exec); resumable"
    last_exit=2
    break
  fi
  # Refuse to start a second session while an earlier exec still runs in the
  # container (a host-side kill can leave the in-container timeout process alive).
  if docker exec "$container" sh -c 'pgrep -f "[r]un_curie.py" >/dev/null 2>&1'; then
    log "a run_curie.py session is still active in $container; retry later"
    last_exit=2
    break
  fi

  sessions=$((sessions + 1))
  resume_flag=0
  [[ "$mode" == "resume" ]] && resume_flag=1
  log "=== exec session $sessions (profile=$profile seed=$seed mode=$mode resume=$resume_flag budget=${budget:-unbounded}) ==="
  set +e
  if [[ -n "$budget" ]]; then
    docker exec \
      -e "MATCLAW_PROFILE=$profile" -e "MATCLAW_OUTPUT=/app" \
      -e "MATCLAW_SEED=$seed" -e "MATCLAW_RESUME=$resume_flag" \
      "$container" bash -lc "exec timeout $budget bash /solution/solve.sh"
  else
    docker exec \
      -e "MATCLAW_PROFILE=$profile" -e "MATCLAW_OUTPUT=/app" \
      -e "MATCLAW_SEED=$seed" -e "MATCLAW_RESUME=$resume_flag" \
      "$container" bash /solution/solve.sh
  fi
  status=$?
  set -e
  last_exit=$status
  log "=== session $sessions exited $status ==="
  # A session killed by the budget timeout (124), by the runner's own kill
  # (137), or externally by SIGTERM (143) always leaves the workspace in a
  # resumable state: run_curie.py discards torn partials on the next resume.
  if [[ $status == 124 || $status == 137 || $status == 143 ]]; then
    log "session interrupted ($status); run is resumable (container + workspace intact)"
    break
  fi
  if [[ $status != 0 ]]; then
    log "exec failed with status $status"
    break
  fi
  # status 0 -> solve.sh finished; loop re-checks completion.
done

if is_complete "$workspace"; then
  completed=1
else
  completed=0
fi

python3 - "$output" "$case_id" "$profile" "$seed" "$image" "$mode" "$sessions" "$last_exit" "$completed" "$container" <<'PY'
import json, sys
from datetime import datetime
from pathlib import Path
output, case_id, profile, seed = sys.argv[1:5]
image, mode, sessions = sys.argv[5:8]
last_exit, completed, container = sys.argv[8:11]
ws = Path(output) / "workspace"
stage = None
try:
    stage = json.loads((ws / "checkpoint.json").read_text()).get("stage")
except Exception:
    pass
status = {"case": case_id, "profile": profile, "seed": int(seed), "image": image,
          "output": output, "mode": mode, "sessions": int(sessions),
          "last_exit": int(last_exit), "completed": bool(int(completed)),
          "result_json": (ws / "result.json").is_file(), "checkpoint_stage": stage,
          "container": container,
          "updated_at": datetime.now().astimezone().isoformat()}
Path(output).mkdir(parents=True, exist_ok=True)
(Path(output) / "run_status.json").write_text(json.dumps(status, indent=2) + "\n")
print(f"status: completed={status['completed']} mode={status['mode']} sessions={status['sessions']} "
      f"last_exit={status['last_exit']} checkpoint_stage={status['checkpoint_stage']}")
PY

if [[ "$completed" == 1 ]]; then
  log "clean finish: removing persistent container $container"
  docker rm -f "$container" >/dev/null 2>&1 || true
  exit 0
fi

if [[ "$last_exit" == 124 || "$last_exit" == 137 || "$last_exit" == 143 || "$last_exit" == 2 ]]; then
  exit 2
fi
exit 1
