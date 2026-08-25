#!/usr/bin/env bash
# Install scientific-benchmark-case-builder-portable v2.1.0.
#
# Installs exactly one Skill, build-scientific-benchmark-case, at
# $HOME/.claude/skills/build-scientific-benchmark-case/. The install is staged,
# validated, and atomically swapped: a failed copy or validation leaves any
# previous installation intact.
#
# The legacy literature-to-mlp-spec skill is preserved by default with a
# migration warning. Pass --remove-legacy to delete it explicitly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_VERSION="2.1.0"
SKILL_NAME="build-scientific-benchmark-case"
LEGACY_NAME="literature-to-mlp-spec"
TARGET_BASE="${HOME}/.claude/skills"
TARGET="${TARGET_BASE}/${SKILL_NAME}"
LEGACY_TARGET="${TARGET_BASE}/${LEGACY_NAME}"
SOURCE_SKILL="${SCRIPT_DIR}/skills/${SKILL_NAME}"

host="claude-code"
scope="project"
remove_legacy=0
check=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host="$2"; shift 2 ;;
    --scope) scope="$2"; shift 2 ;;
    --remove-legacy) remove_legacy=1; shift ;;
    --check) check=1; shift ;;
    --help)
      echo "usage: $0 [--host claude-code] [--scope project] [--remove-legacy] [--check]"
      exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

# --check ---------------------------------------------------------------
verify_package() {
  local rc=0
  if [[ ! -f "${SOURCE_SKILL}/SKILL.md" ]]; then
    echo "ERROR: SKILL.md missing at ${SOURCE_SKILL}" >&2; rc=1
  fi
  if ! grep -q "^name: ${SKILL_NAME}$" "${SOURCE_SKILL}/SKILL.md" 2>/dev/null; then
    echo "ERROR: SKILL.md name does not match ${SKILL_NAME}" >&2; rc=1
  fi
  if ! grep -q "\"version\": \"${PACKAGE_VERSION}\"" "${SCRIPT_DIR}/manifest.json" 2>/dev/null; then
    echo "ERROR: manifest.json version is not ${PACKAGE_VERSION}" >&2; rc=1
  fi
  if [[ ! -f "${SCRIPT_DIR}/SHA256SUMS" ]]; then
    echo "ERROR: SHA256SUMS missing" >&2; rc=1
  fi
  while read -r expected file; do
    [[ -z "${expected:-}" || -z "${file:-}" ]] && continue
    if [[ ! -f "${SCRIPT_DIR}/${file}" ]]; then
      echo "ERROR: SHA256SUMS entry missing file ${file}" >&2; rc=1
      continue
    fi
    actual="$(shasum -a 256 "${SCRIPT_DIR}/${file}" | awk '{print $1}')"
    if [[ "${actual}" != "${expected}" ]]; then
      echo "ERROR: hash mismatch ${file}" >&2; rc=1
    fi
  done < "${SCRIPT_DIR}/SHA256SUMS"
  local f
  while IFS= read -r f; do
    if ! grep -qF " ${f}" "${SCRIPT_DIR}/SHA256SUMS"; then
      echo "ERROR: file not listed in SHA256SUMS: ${f}" >&2; rc=1
    fi
  done < <(cd "${SCRIPT_DIR}" && find . -type f ! -name SHA256SUMS ! -path '*/__pycache__/*' ! -name '*.pyc' | sed 's|^\./||' | sort)
  return "${rc}"
}

if [[ "${check}" -eq 1 ]]; then
  if verify_package; then
    echo "OK: package ${PACKAGE_VERSION} contract valid"
    exit 0
  fi
  exit 1
fi

# Install ----------------------------------------------------------------
if [[ ! -d "${SOURCE_SKILL}" ]]; then
  echo "ERROR: source skill missing: ${SOURCE_SKILL}" >&2
  exit 1
fi

if [[ "${remove_legacy}" -eq 1 ]]; then
  if [[ -d "${LEGACY_TARGET}" ]]; then
    rm -rf "${LEGACY_TARGET}"
    echo "removed legacy skill: ${LEGACY_TARGET}"
  fi
elif [[ -d "${LEGACY_TARGET}" ]]; then
  echo "WARNING: legacy skill still installed at ${LEGACY_TARGET}"
  echo "  old command: /${LEGACY_NAME}"
  echo "  new command: /${SKILL_NAME} mode=extract-spec category=mlp"
  echo "  run install.sh --remove-legacy to delete it"
fi

STAGE="${TARGET}.tmp"
rm -rf "${STAGE}"
mkdir -p "$(dirname "${STAGE}")"
# Any failure in cp propagates (set -e) and leaves TARGET untouched.
cp -R "${SOURCE_SKILL}" "${STAGE}"

if ! grep -q "^name: ${SKILL_NAME}$" "${STAGE}/SKILL.md" 2>/dev/null; then
  rm -rf "${STAGE}"
  echo "ERROR: staged skill failed name validation" >&2
  exit 1
fi

if [[ -e "${TARGET}" || -L "${TARGET}" ]]; then
  mv "${TARGET}" "${TARGET}.old"
fi
if mv "${STAGE}" "${TARGET}"; then
  rm -rf "${TARGET}.old"
else
  if [[ -d "${TARGET}.old" ]]; then
    mv "${TARGET}.old" "${TARGET}"
  fi
  echo "ERROR: atomic swap failed; previous installation restored" >&2
  exit 1
fi

echo "installed ${SKILL_NAME} ${PACKAGE_VERSION} to ${TARGET}"
