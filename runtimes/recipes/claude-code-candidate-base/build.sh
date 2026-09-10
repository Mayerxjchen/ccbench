#!/usr/bin/env bash
set -euo pipefail

base_digest=""
claude_version=""
python_deb_version="3.11.2-6+deb12u8"
packmol_version="1:20.14.0-1"
tag="bench-agent-claude-code:2.1.266"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-image-digest) base_digest="${2:-}"; shift 2 ;;
    --claude-code-version) claude_version="${2:-}"; shift 2 ;;
    --python-deb-version) python_deb_version="${2:-}"; shift 2 ;;
    --packmol-version) packmol_version="${2:-}"; shift 2 ;;
    --tag) tag="${2:-}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ "$base_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  echo "--base-image-digest must be sha256:<64 lowercase hex>" >&2; exit 2;
}
[[ "$claude_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z.-]+)?$ ]] || {
  echo "--claude-code-version must be an exact npm version" >&2; exit 2;
}
[[ "$python_deb_version" =~ ^3\.11\.[0-9]+-[0-9A-Za-z.+~]+$ ]] || {
  echo "--python-deb-version must be an exact Python 3.11 Debian version" >&2; exit 2
}
[[ "$packmol_version" =~ ^[0-9]+:[0-9]+\.[0-9]+\.[0-9]+-[0-9A-Za-z.+~]+$ ]] || {
  echo "--packmol-version must be an exact Debian version" >&2; exit 2
}
[[ "$tag" =~ ^[A-Za-z0-9._/-]+:[A-Za-z0-9._-]+$ ]] || {
  echo "--tag must be an explicit image:tag" >&2; exit 2
}

docker build \
  --build-arg "BASE_IMAGE=node:22-bookworm-slim@${base_digest}" \
  --build-arg "CLAUDE_CODE_VERSION=${claude_version}" \
  --build-arg "PYTHON_DEB_VERSION=${python_deb_version}" \
  --build-arg "PACKMOL_VERSION=${packmol_version}" \
  -t "$tag" \
  "$(dirname "$0")"
