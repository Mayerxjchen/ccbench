FROM dftworld-base

WORKDIR /app
# 论文环境版本（环境文件 environment.yml）：mace-torch 0.3.13、ASE 3.25.0、
# RDKit 2025.3.3、pymatgen 2025.3.10。torch 走 CPU（linux/arm64 wheel 由 PyPI 提供）。
RUN uv venv .venv && \
    UV_HTTP_TIMEOUT=1800 uv pip install --python .venv/bin/python \
        "ase==3.25.0" \
        "rdkit==2025.3.3" \
        "pymatgen==2025.3.10" \
        "mace-torch==0.3.13"

# Bake MACE-MP-0 medium 权重到 mace 包目录，使 mace_mp(model="medium-mpa-0")
# 完全离线加载（foundations_models.py 对 model=None / "medium-mpa-0" 优先检查
# <mace>/calculators/foundations_models/mace-mpa-0-medium.model）。
RUN PYTHON=/app/.venv/bin/python && \
    MACE_DIR=$($PYTHON -c "import mace, os; print(os.path.dirname(mace.__file__))") && \
    mkdir -p "$MACE_DIR/calculators/foundations_models" && \
    curl -LsSf -o "$MACE_DIR/calculators/foundations_models/mace-mpa-0-medium.model" \
        "https://github.com/ACEsuit/mace-mp/releases/download/mace_mpa_0/mace-mpa-0-medium.model" && \
    ls -lh "$MACE_DIR/calculators/foundations_models/"

# 构建期验证：权重离线可加载
RUN /app/.venv/bin/python -c \
    "from mace.calculators import mace_mp; c = mace_mp(model='medium-mpa-0'); print('MACE-MP-0 medium loaded OK, device=', c.device)"
