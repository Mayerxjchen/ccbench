# deepmd-jax runtime family (DPMP/JAX workflow capability) — CPU variant.
#
# Shared runtime for GO-water DPMP (Case 005) and any future deepmd-jax MLP case.
# NOT a per-case image: build once, reuse.
#
#   deepmd-jax  = Deep Potential / Deep Potential Message-Passing (DP-MP)
#                 potential, implemented in JAX (repo SparkyTruck/deepmd-jax).
#   jax-md      = MD framework (deepmd-jax's MD engine is JAX-native; jax-md is
#                 a declared dependency).
#   flax/optax  = deepmd-jax's NN/optimizer stack.
#
# Pin: DEEPMD_JAX_COMMIT=48a981a (2025-08-19) — the last commit before the paper
# dataset was posted (figshare v2 2025-10-29) and the last revision whose train()
# still exposes `tensor_2nd` (removed at 2e4390e on 2026-03-07, hardcoded to
# use_2nd=True after). The paper's train.py passes tensor_2nd=True, so this pin
# matches the published configuration byte-for-byte.
FROM dftworld-base-ai2kit:0.1.0-cpu

ARG DEEPMD_JAX_COMMIT=48a981a

# Install into the ai2kit venv (/opt/ai2kit, python 3.11.15) so the agent's
# single python sees cp2k + ai2-kit + deepmd-jax together. The venv has no pip
# (ensurepip first); ase/scipy/numpy are already present.
#
# Version lock (2026-08-20, verified by 4-layer smoke): deepmd-jax@48a981a
# calls jax.sharding.PositionalSharding, removed in jax>=0.6.0, so the JAX
# stack is pinned to jax 0.5.3 (flax/optax resolved under that constraint).
# jax-md pinned to a41c7d19 (v0.2.29 HEAD, no haiku dependency) for
# reproducibility.
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    /opt/ai2kit/bin/python -m ensurepip && \
    /opt/ai2kit/bin/python -m pip install --no-cache-dir \
        "jax[cpu]==0.5.3" "flax==0.10.6" "optax==0.2.8" \
        "jax-md @ git+https://github.com/google/jax-md.git@a41c7d19" && \
    git clone https://github.com/SparkyTruck/deepmd-jax.git /opt/deepmd-jax && \
    cd /opt/deepmd-jax && \
    git checkout ${DEEPMD_JAX_COMMIT} && \
    /opt/ai2kit/bin/python -m pip install -e . --no-deps --no-cache-dir

ENV JAX_ENABLE_X64=1
WORKDIR /app
