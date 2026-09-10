# The Candidate control image is intentionally scientific-runtime agnostic.
# It is shared by all cases; CP2K/LAMMPS/DeepMD/JAX/MPI/CUDA/Slurm/CompShare,
# SSH and Docker CLI belong to operator/verifier runtimes and must not enter
# this image.  The build wrapper requires immutable base digests and exact
# dependency versions.
ARG BASE_IMAGE=node:22-bookworm-slim
FROM ${BASE_IMAGE}

ARG CLAUDE_CODE_VERSION
ARG PYTHON_DEB_VERSION=3.11.2-6+deb12u8
ARG BASH_VERSION=5.2.15-2+b13
ARG COREUTILS_VERSION=9.1-1
ARG FINDUTILS_VERSION=4.9.0-4
ARG GREP_VERSION=3.8-5
ARG SED_VERSION=4.9-1+deb12u1
ARG GAWK_VERSION=1:5.2.1-2
ARG JQ_VERSION=1.6-2.1+deb12u2
ARG RIPGREP_VERSION=13.0.0-4+b2
ARG FILE_VERSION=1:5.44-3
ARG CA_CERTIFICATES_VERSION=20250419~deb12u1
ARG PACKMOL_VERSION=1:20.14.0-1

COPY requirements.lock /tmp/bench-candidate-requirements.lock
RUN test -n "${CLAUDE_CODE_VERSION}" \
 && apt-get update \
 && apt-get install --no-install-recommends -y \
      "bash=${BASH_VERSION}" "coreutils=${COREUTILS_VERSION}" \
      "findutils=${FINDUTILS_VERSION}" "grep=${GREP_VERSION}" \
      "sed=${SED_VERSION}" "gawk=${GAWK_VERSION}" "jq=${JQ_VERSION}" \
      "ripgrep=${RIPGREP_VERSION}" "file=${FILE_VERSION}" \
      "ca-certificates=${CA_CERTIFICATES_VERSION}" \
      "python3.11=${PYTHON_DEB_VERSION}" "python3.11-venv=${PYTHON_DEB_VERSION}" \
      "packmol=${PACKMOL_VERSION}" \
 && python3.11 -m venv --copies /opt/bench-python \
 && /opt/bench-python/bin/pip install --no-cache-dir --require-hashes --no-deps \
      -r /tmp/bench-candidate-requirements.lock \
 && rm -rf /var/lib/apt/lists/* /root/.cache /tmp/bench-candidate-requirements.lock \
 && groupadd --gid 10001 agent \
 && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin agent \
 && mkdir -p /opt/claude-code /workspace \
 && chown -R 10001:10001 /opt/claude-code /opt/bench-python /workspace

WORKDIR /opt/claude-code
RUN npm install --global --prefix /opt/claude-code \
      "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && chown -R 10001:10001 /opt/claude-code

ENV PATH="/opt/claude-code/bin:/opt/claude-code/node_modules/.bin:/opt/bench-python/bin:${PATH}" \
    BENCH_PYTHON=/opt/bench-python/bin/python \
    MPLCONFIGDIR=/tmp/matplotlib \
    DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
    ENABLE_CLAUDEAI_MCP_SERVERS=false \
    CLAUDE_CODE_DISABLE_ARTIFACT=1
WORKDIR /workspace
USER 10001:10001
CMD ["sleep", "3600"]
