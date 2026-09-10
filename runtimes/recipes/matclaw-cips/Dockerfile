# BASE_IMAGE lets a cross-arch build pin an amd64 base (dftworld-base:amd64)
# without touching the default arm64 tag used for local container runs.
ARG BASE_IMAGE=ubuntu:24.04
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.8.14
ARG PYTHON_IMAGE=python:3.11.15-slim-bookworm
FROM ${UV_IMAGE} AS uv-tools
FROM ${PYTHON_IMAGE} AS python-runtime

FROM ${BASE_IMAGE} AS matclaw-runtime

# uv is copied from an explicit, version-pinned upstream image.  Formal
# builds should pass UV_IMAGE with its verified @sha256 digest; keeping the
# source in the recipe makes the build reproducible instead of relying on a
# host-installed binary (or on a stale image history layer).
COPY --from=uv-tools /uv /uvx /usr/local/bin/
# The Ubuntu runtime does not ship Python.  Copy the official, version-pinned
# interpreter into /usr/local so UID 65532 never follows a /root-owned uv
# symlink.  Its site-packages remain isolated inside /opt/matclaw below.
COPY --from=python-runtime /usr/local /usr/local

ENV PATH="/opt/matclaw/bin:${PATH}" \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    LAMMPS_PLUGIN_PATH="/opt/matclaw/lib/python3.11/site-packages/deepmd/lib" \
    TF_CPP_MIN_LOG_LEVEL=2 \
    OMP_NUM_THREADS=1 \
    TF_INTRA_OP_PARALLELISM_THREADS=1 \
    TF_INTER_OP_PARALLELISM_THREADS=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 libopenblas0 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/uv-python && chmod 755 /opt/uv-python && \
    uv venv --python /usr/local/bin/python3.11 /opt/matclaw && \
    UV_HTTP_TIMEOUT=1800 uv pip install --python /opt/matclaw/bin/python \
        "deepmd-kit[lmp]==2.2.11" \
        "tensorflow==2.16.2" \
        "numpy==1.26.4" \
        "ase==3.26.0" \
        "scipy==1.17.1" \
        "matplotlib==3.11.1" \
        "pymatgen==2026.5.4" \
        "dpdata==0.2.25"

RUN uv pip install --python /opt/matclaw/bin/python "pytest==8.3.5"

RUN mkdir -p /opt/matclaw/assets
COPY benchmark/sources/matclaw/common/teacher-model/frozen_model.pb /opt/matclaw/assets/frozen_model.pb
COPY benchmark/sources/matclaw/common/teacher-model/type_map.raw /opt/matclaw/assets/type_map.raw
COPY benchmark/sources/matclaw/common/CuInP2S6.cif /opt/matclaw/assets/CuInP2S6.cif
COPY runtimes/recipes/matclaw-cips/smoke_test.py /opt/matclaw/smoke_test.py

RUN printf '%s  %s\n' \
      'a3e7cf9c8168c649ee1ba6e39a7212f3f9db29fc0162b093beb908927e956b4d' \
      '/opt/matclaw/assets/frozen_model.pb' | sha256sum -c - && \
    printf '%s  %s\n' \
      'b9e3b0c4470274d5e3e1ce19e7c8834bda323e50483eef9791b1e744bbd629de' \
      '/opt/matclaw/assets/CuInP2S6.cif' | sha256sum -c -

FROM matclaw-runtime AS matclaw-verified
RUN /opt/matclaw/bin/python /opt/matclaw/smoke_test.py

WORKDIR /app
