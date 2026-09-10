# syntax=docker/dockerfile:1
# Shared CPU runtime for DeepMD-JAX/DPMP cases and verifier workers.
ARG BASE_IMAGE=python:3.11.15-slim-bookworm@sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    JAX_ENABLE_X64=1 \
    JAX_PLATFORMS=cpu \
    MPLBACKEND=Agg

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libgomp1 libopenblas0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/bench/deepmd-jax
COPY requirements.lock ./requirements.lock
COPY assets/deepmd_jax-0.2.tar.gz assets/jax_md-0.2.29.tar.gz ./assets/

# Registry wheels are hash locked. The two reviewed source archives are
# installed without dependency resolution and checked before Docker is invoked.
RUN python -m pip install --require-hashes -r requirements.lock

# The reviewed DeepMD-JAX archive has a historical ``deepmd-jax/`` root whose
# name differs from the archive filename, so install it from an explicit,
# deterministic extraction path instead of relying on pip's sdist heuristic.
RUN mkdir -p ./src \
    && tar -xzf ./assets/deepmd_jax-0.2.tar.gz -C ./src \
    && tar -xzf ./assets/jax_md-0.2.29.tar.gz -C ./src \
    && python -m pip install --no-build-isolation --no-deps ./src/jax-md \
    && python -m pip install --no-build-isolation --no-deps ./src/deepmd-jax \
    && python -m pip check \
    && rm -rf ./src

COPY smoke_test.py ./smoke_test.py
WORKDIR /workspace
CMD ["python", "/opt/bench/deepmd-jax/smoke_test.py"]
