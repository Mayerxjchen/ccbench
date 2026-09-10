#!/usr/bin/env sh
set -eu
test "$(id -u)" = "10002"
python3 -c 'import asyncio; print("sidecar-python-ok")'
