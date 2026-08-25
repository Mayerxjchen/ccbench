FROM dftworld-base

WORKDIR /app
# 源论文环境版本：ASE 3.25.0、TBLite 0.4.0、RDKit 2024.3.5。
# pymatgen 用于对称数（get_symmetry_number），是 source implementation ase_core 的依赖。
# tblite 0.4.0 在 PyPI 无 linux/arm64 wheel（只有 x86_64 + macOS arm64），
# 须从 sdist 编译：需要 gfortran（Fortran 核心）+ cmake + pkg-config。
RUN apt-get update && apt-get install -y --no-install-recommends \
        gfortran cmake pkg-config git \
        libopenblas-dev liblapack-dev libblas-dev \
    && rm -rf /var/lib/apt/lists/*

RUN uv venv .venv && \
    UV_HTTP_TIMEOUT=1800 uv pip install --python .venv/bin/python \
        "ase==3.25.0" \
        "rdkit==2024.3.5" \
        "tblite==0.4.0" \
        pymatgen
