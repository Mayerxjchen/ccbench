#!/usr/bin/env bash
set -euo pipefail

# Archive previous ai2-kit run artifacts and prepare a clean rerun workspace.
#
# This script is intentionally conservative:
#   - It moves run artifacts into runs/<timestamp>-rerun-cleanup/.
#   - It does not delete files directly.
#   - It keeps source/config/input data by default.
#   - It requires --dry-run or --yes.
#
# Typical use:
#   tools/clean_rerun.sh --dry-run
#   tools/clean_rerun.sh --yes
#   tools/clean_rerun.sh --yes --archive-name 2026-06-15-before-new-aimd

usage() {
  cat <<'USAGE'
Usage:
  tools/clean_rerun.sh --dry-run
  tools/clean_rerun.sh --yes [--archive-name NAME]

Purpose:
  Archive previous run artifacts and prepare a clean workspace for rerunning
  setup.sh / run.sh / validation scripts.

Keeps by default:
  config/
  data/
  workflow/
  tools/
  test scripts
  README.md, checklist.md, CLAUDE.md, run.sh

Archives if present:
  workdir/iter-*
  workdir/dp-init-data
  workdir/lammps-data
  workdir/*.done
  test/dp-test/output
  test/dp-test/test-data
  test/model-devi/output
  test/nvt/output
  test/rdf/output
  root slurm outputs

Note:
  config/aimd.xyz is preserved. If the next run should use new AIMD data,
  regenerate config/aimd.xyz before running workflow/setup.sh or run.sh.
USAGE
}

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN=0
CONFIRM=0
ARCHIVE_NAME="$(date +%Y-%m-%d-%H%M%S)-rerun-cleanup"
ARCHIVED_COUNT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --yes)
      CONFIRM=1
      shift
      ;;
    --archive-name)
      ARCHIVE_NAME="${2:?missing archive name}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$DRY_RUN" -eq 0 && "$CONFIRM" -eq 0 ]]; then
  echo "Refusing to modify files without --yes. Use --dry-run to preview." >&2
  exit 2
fi

if [[ ! -d "$PROJECT_DIR/config" || ! -d "$PROJECT_DIR/workflow" || ! -d "$PROJECT_DIR/tools" ]]; then
  echo "Refusing to run: $PROJECT_DIR does not look like the ai2-kit project root." >&2
  exit 1
fi

ARCHIVE_DIR="$PROJECT_DIR/runs/$ARCHIVE_NAME"

if [[ "$DRY_RUN" -eq 0 && -e "$ARCHIVE_DIR" ]]; then
  echo "Archive already exists: $ARCHIVE_DIR" >&2
  echo "Choose another name with --archive-name." >&2
  exit 1
fi

has_meaningful_content() {
  local path="$1"

  if [[ -f "$path" ]]; then
    return 0
  fi

  if [[ -d "$path" ]]; then
    find "$path" -mindepth 1 ! -name '.gitkeep' -print -quit | grep -q .
    return $?
  fi

  return 1
}

archive_path() {
  local rel="$1"
  local src="$PROJECT_DIR/$rel"
  local dst="$ARCHIVE_DIR/$rel"

  [[ -e "$src" ]] || return 0

  if ! has_meaningful_content "$src"; then
    echo "skip empty: $rel"
    return 0
  fi

  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "archive: $rel -> runs/$ARCHIVE_NAME/$rel"
  else
    mkdir -p "$(dirname "$dst")"
    mv "$src" "$dst"
    echo "archived: $rel -> runs/$ARCHIVE_NAME/$rel"
  fi

  ARCHIVED_COUNT=$((ARCHIVED_COUNT + 1))
}

archive_glob() {
  local path
  for path in "$@"; do
    [[ -e "$path" ]] || continue
    archive_path "${path#$PROJECT_DIR/}"
  done
}

recreate_dirs() {
  local dirs=(
    workdir
    test/dp-test/output
    test/model-devi/output
    test/nvt/output
    test/rdf/output
  )

  local rel
  for rel in "${dirs[@]}"; do
    mkdir -p "$PROJECT_DIR/$rel"
    touch "$PROJECT_DIR/$rel/.gitkeep"
  done
}

echo "Project: $PROJECT_DIR"
echo "Archive: runs/$ARCHIVE_NAME"

shopt -s nullglob

# Active-learning workdir.
archive_glob "$PROJECT_DIR"/workdir/iter-*

for rel in \
  workdir/dp-init-data \
  workdir/lammps-data \
  workdir/setup.done \
  workdir/iter.done \
  workdir/train.done \
  workdir/lammps.done; do
  archive_path "$rel"
done

archive_glob "$PROJECT_DIR"/workdir/*.done

# Validation outputs. Keep test scripts, archive generated data/results only.
for rel in \
  test/dp-test/output \
  test/dp-test/test-data \
  test/model-devi/output \
  test/nvt/output \
  test/rdf/output; do
  archive_path "$rel"
done

# Root-level scheduler leftovers.
archive_path slurm.out
archive_glob "$PROJECT_DIR"/slurm-*.out "$PROJECT_DIR"/*.slurm.exitcode

if [[ "$DRY_RUN" -eq 0 ]]; then
  recreate_dirs
fi

if [[ "$ARCHIVED_COUNT" -eq 0 ]]; then
  echo "No meaningful old rerun artifacts found."
else
  echo "Archived $ARCHIVED_COUNT path(s)."
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "Dry run only; no files were moved."
else
  echo "Clean rerun workspace is ready."
  echo "Archived results are in: $ARCHIVE_DIR"
  echo "Reminder: config/aimd.xyz was preserved. Regenerate it before rerun if using new AIMD data."
fi
