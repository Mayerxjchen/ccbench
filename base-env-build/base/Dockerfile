ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

RUN apt-get update && \
    apt-get install -y ca-certificates curl python3 python3-pip python3-venv && \
    rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    mv /root/.local/bin/uv /usr/local/bin/uv

RUN pip3 install --break-system-packages pytest pytest-json-ctrf

WORKDIR /app
