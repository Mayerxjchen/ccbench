# MatClaw CIPS GPU runtime — built from the CPU-verified image.
#
# The CPU image (dftworld-base-matclaw-cips:2.2.11-cpu) already installs the
# pinned deepmd 2.2.11 + CPU tensorflow + LAMMPS stack and runs the build-time
# scientific smoke gate. This stage swaps tensorflow for the CUDA-enabled wheel
# (`tensorflow[and-cuda]`, which installs the nvidia pip CUDA/cuDNN libraries)
# and adds the CPU/GPU parity qualification probe. The runtime still requires an
# NVIDIA driver on the host; `docker run --gpus device=0` is mandatory.
#
# Build tag: dftworld-base-matclaw-cips:2.2.11-gpu
#
# BASE_IMAGE lets a cross-arch build pin the amd64 CPU image
# (dftworld-base-matclaw-cips:2.2.11-cpu-amd64) without touching the default
# arm64 tags used for local container runs.
ARG BASE_IMAGE=dftworld-base-matclaw-cips:2.2.11-cpu
FROM ${BASE_IMAGE} AS matclaw-gpu-runtime

ENV PATH="/opt/matclaw/bin:${PATH}" \
    LAMMPS_PLUGIN_PATH="/opt/matclaw/lib/python3.11/site-packages/deepmd/lib" \
    TF_CPP_MIN_LOG_LEVEL=2 \
    OMP_NUM_THREADS=1 \
    TF_INTRA_OP_PARALLELISM_THREADS=1 \
    TF_INTER_OP_PARALLELISM_THREADS=1

# Replace the CPU tensorflow wheel in the existing venv with the CUDA-enabled
# one. --reinstall forces the wheel swap (same version, different extras); the
# nvidia-* pip packages ship the CUDA 12 runtime + cuDNN that TF 2.16.2 needs.
RUN uv pip install --python /opt/matclaw/bin/python --reinstall \
        "tensorflow[and-cuda]==2.16.2"

# The parity qualification probe ships next to the smoke gate.
COPY runtimes/recipes/matclaw-cips-gpu/qualify_gpu.py /opt/matclaw/qualify_gpu.py

FROM matclaw-gpu-runtime AS matclaw-gpu-verified
# Build-time gate stays device-independent: docker build has no GPU, so pin the
# scientific smoke gate to CPU. Real GPU behavior is qualified at runtime by
# /opt/matclaw/qualify_gpu.py (which fails closed when no GPU is visible).
RUN CUDA_VISIBLE_DEVICES="" /opt/matclaw/bin/python /opt/matclaw/smoke_test.py

WORKDIR /app
