# syntax=docker/dockerfile:1
# MLFFBench Candidate Agent Sandbox Image: Claude Code
# Pinned Immutable Base: node:20-bookworm-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0
FROM node:20-bookworm-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0

LABEL org.opencontainers.image.title="mlffbench-agent-claude-code" \
      org.opencontainers.image.version="v1" \
      org.opencontainers.image.description="Hardened Candidate Agent Sandbox for Claude Code in MLFFBench"

ENV DEBIAN_FRONTEND=noninteractive \
    NODE_ENV=production \
    HOME=/home/agent

# Install essential agent system tools (strictly no CP2K/DeepMD/MatClaw/JAX)
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    git \
    jq \
    python3 \
    python3-minimal \
    procps \
    && rm -rf /var/lib/apt/lists/*

# P0 Defense: Install security wrappers for forbidden commands
RUN for cmd in ssh sbatch compshare docker nc nmap; do \
      printf '#!/bin/sh\necho "Access Denied: command %s is strictly prohibited by MLFFBench security policy." >&2\nexit 126\n' "$cmd" > "/usr/local/bin/$cmd" \
      && chmod 755 "/usr/local/bin/$cmd"; \
    done

# Install pinned Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code@0.2.29 \
    && npm cache clean --force

# Copy qualification probe into image
COPY probes/qualify_agent.sh /usr/local/bin/qualify_agent.sh
RUN chmod 755 /usr/local/bin/qualify_agent.sh

# Recreate non-root agent user with uid 1000 / gid 1000 (replacing default node user)
RUN userdel -r node 2>/dev/null || true \
    && groupadd -g 1000 agent \
    && useradd -u 1000 -g 1000 -m -s /bin/bash agent \
    && mkdir -p /app /home/agent/.claude \
    && chown -R agent:agent /app /home/agent

USER agent
WORKDIR /app

CMD ["/bin/bash"]
