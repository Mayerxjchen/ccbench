#!/usr/bin/env bash
set -euo pipefail

base_digest=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-image-digest) base_digest="${2:-}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ "$base_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || {
  echo "--base-image-digest must be sha256:<64 lowercase hex>" >&2; exit 2;
}
docker build \
  --build-arg "BASE_IMAGE=python:3.12-alpine@${base_digest}" \
  -t "bench-gateway-anthropic:1" \
  "$(dirname "$0")"
