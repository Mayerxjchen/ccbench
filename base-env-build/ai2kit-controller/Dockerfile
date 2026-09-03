# Case 034 HPC controller base — built in-repo replacement for the former
# external ``dftworld-base-ai2kit:0.1.0-cpu-controller`` overlay.
#
# Like every controller image, this is ONLY a control layer.  Scientific
# software is inherited byte-for-byte from the frozen ai2kit CPU runtime; the
# controller no longer ships a remote-shell or transfer client because all
# SSH/scheduler access has moved behind the trusted bench-hpc gateway
# (site-host credentials, never mounted into the agent).  Added: the restricted
# controller surface under /opt/dftworld/controller/.
#
# Build tag: dftworld-base-ai2kit:0.1.0-cpu-controller
FROM dftworld-base-ai2kit:0.1.0-cpu

# Restricted controller surface: transport, controller CLI, cluster-profile
# loader, fail-closed runtime-lock validator.  No private key, no SSH config /
# known-hosts, no hidden solution/reference/tests trees.
COPY scripts/ablation/transport/slurm_transport.py /opt/dftworld/controller/slurm_transport.py
COPY scripts/cluster_profile.py /opt/dftworld/controller/cluster_profile.py
COPY scripts/matclaw_runtime_lock.py /opt/dftworld/controller/matclaw_runtime_lock.py

# bench-hpc CLI (Task 12): the trusted HTTP job client.  The whole
# dftworld_bench package + schemas are copied so the controller can later
# resolve runtime requirements from the registry; PYTHONPATH points the
# interpreter at that surface, and /opt/ai2kit/bin is first on PATH so a bare
# `python` inside the image is the venv interpreter.
COPY dftworld_bench /opt/dftworld/controller/dftworld_bench
COPY schemas /opt/dftworld/controller/schemas
ENV PYTHONPATH=/opt/dftworld/controller \
    PATH="/opt/ai2kit/bin:${PATH}"

# The ai2kit venv ships without pip; seed it with ensurepip, then install the
# pure-python CLI deps and the `bench-hpc` wrapper.
RUN /opt/ai2kit/bin/python -m ensurepip && \
    /opt/ai2kit/bin/python -m pip install --no-cache-dir \
        pyyaml jsonschema referencing && \
    printf '#!/bin/sh\nexec /opt/ai2kit/bin/python -m dftworld_bench.hpc "$@"\n' \
        > /usr/local/bin/bench-hpc && \
    chmod +x /usr/local/bin/bench-hpc

WORKDIR /app
