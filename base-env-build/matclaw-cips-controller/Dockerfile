# Case 031-033 HPC controller base — the local Docker sandbox is ONLY a control
# layer.
#
# Derived from the pinned CPU MatClaw runtime.  The controller no longer ships
# a remote-shell or transfer client: all SSH/scheduler access has moved behind
# the trusted bench-hpc gateway, which runs on the site host with the site
# operator's own credentials.  This image carries only the restricted controller
# surface below /opt/dftworld/controller/: the reused 034 transport, the
# controller CLI, the cluster-profile loader, the fail-closed runtime-lock
# validator, and the fixed Slurm templates for every case (031/032/033).  No
# private key, no SSH config / known-hosts, and no hidden solution/reference/
# tests trees are ever copied in.
#
# Build tag: dftworld-base-matclaw-cips:2.2.11-controller
FROM dftworld-base-matclaw-cips:2.2.11-cpu

# Restricted controller surface: the 034 transport (reused unchanged), the
# controller CLI, the cluster-profile loader, the fail-closed runtime-lock
# validator, and the fixed Slurm templates for every case (031/032/033).  They
# sit side by side so the CLI can import them without the repo package tree.
COPY scripts/ablation/transport/slurm_transport.py /opt/dftworld/controller/slurm_transport.py
COPY scripts/matclaw_hpc_gateway.py /opt/dftworld/controller/matclaw_hpc_gateway.py
COPY scripts/matclaw_hpc_controller.py /opt/dftworld/controller/matclaw_hpc_controller.py
COPY scripts/cluster_profile.py /opt/dftworld/controller/cluster_profile.py
COPY scripts/matclaw_runtime_lock.py /opt/dftworld/controller/matclaw_runtime_lock.py
COPY scripts/hpc/031_matclaw_gpu.slurm /opt/dftworld/controller/hpc/031_matclaw_gpu.slurm
COPY scripts/hpc/032_matclaw_gpu.slurm /opt/dftworld/controller/hpc/032_matclaw_gpu.slurm
COPY scripts/hpc/033_matclaw_gpu.slurm /opt/dftworld/controller/hpc/033_matclaw_gpu.slurm

RUN chmod +x /opt/dftworld/controller/matclaw_hpc_controller.py && \
    ln -s /opt/dftworld/controller/matclaw_hpc_controller.py /usr/local/bin/matclaw-hpc-controller

# bench-hpc CLI (Task 12): the trusted HTTP job client that replaces the
# remote-shell transport.  The whole dftworld_bench package + schemas are
# copied so the controller can later resolve runtime requirements from the
# registry; PYTHONPATH points the interpreter at that surface.
COPY dftworld_bench /opt/dftworld/controller/dftworld_bench
COPY schemas /opt/dftworld/controller/schemas
ENV PYTHONPATH=/opt/dftworld/controller

# bench-hpc needs only pure-python deps (yaml/jsonschema/referencing), so the
# uv-managed venv installs them with no build toolchain.  The sh wrapper keeps
# the CLI callable as `bench-hpc` regardless of PATH.
RUN uv pip install --python /opt/matclaw/bin/python --no-cache-dir \
        pyyaml jsonschema referencing && \
    printf '#!/bin/sh\nexec /opt/matclaw/bin/python -m dftworld_bench.hpc "$@"\n' \
        > /usr/local/bin/bench-hpc && \
    chmod +x /usr/local/bin/bench-hpc

WORKDIR /app
