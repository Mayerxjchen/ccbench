#!/bin/bash
set -euo pipefail

echo "=== MLFFBench Candidate Agent Sandbox Probe: Claude Code ==="

# 1. Verify user identity
CURRENT_UID=$(id -u)
CURRENT_USER=$(id -un)
echo "User: ${CURRENT_USER} (UID=${CURRENT_UID})"
if [ "${CURRENT_UID}" -eq 0 ]; then
  echo "FAIL: Agent running as root. Non-root user (uid 1000) required." >&2
  exit 1
fi

# 2. Verify /app write permissions
echo "Checking /app write permissions..."
test_file="/app/.probe_write_test_$$"
touch "${test_file}"
echo "probe_ok" > "${test_file}"
rm -f "${test_file}"
echo "PASS: /app is writable by agent user."

# 3. Verify Claude Code CLI availability
echo "Checking Claude Code CLI..."
if ! command -v claude >/dev/null 2>&1; then
  echo "FAIL: claude binary not found in PATH." >&2
  exit 1
fi
echo "PASS: claude CLI present ($(claude --version 2>/dev/null || echo 'claude-code-installed'))."

# 4. Strict Boundary Check: Science compute binaries MUST NOT exist
echo "Verifying absence of scientific computing engines..."
forbidden_binaries=("cp2k.popt" "cp2k.psmp" "dp" "mpirun" "lmp")
for bin in "${forbidden_binaries[@]}"; do
  if command -v "${bin}" >/dev/null 2>&1; then
    echo "FAIL: Forbidden scientific binary found in Agent image: ${bin}" >&2
    exit 1
  fi
done
echo "PASS: Zero scientific computing engines in candidate agent image."

# 5. Boundary Check: Docker socket and host secrets MUST NOT exist
echo "Verifying absence of Docker socket and host secret mounts..."
if [ -e "/var/run/docker.sock" ]; then
  echo "FAIL: Docker socket exposed inside candidate container!" >&2
  exit 1
fi
if [ -d "/home/agent/.ssh" ] && [ -n "$(ls -A /home/agent/.ssh 2>/dev/null)" ]; then
  echo "FAIL: Non-empty .ssh directory found in candidate container!" >&2
  exit 1
fi
echo "PASS: No docker socket or host ssh credentials detected."

echo "=== ALL AGENT SANDBOX PROBES PASSED ==="
exit 0
