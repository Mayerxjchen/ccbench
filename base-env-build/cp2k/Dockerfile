FROM cp2k/cp2k:latest

RUN apt-get update && \
    apt-get install -y --no-install-recommends python3-pip && \
    pip3 install --break-system-packages pytest pytest-json-ctrf && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
