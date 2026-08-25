FROM dftworld-base

WORKDIR /app
RUN uv venv .venv && \
    UV_HTTP_TIMEOUT=1800 uv pip install --python .venv/bin/python deepmd-kit tensorflow-cpu
