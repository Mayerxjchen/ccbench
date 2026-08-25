FROM dftworld-base

RUN apt-get update && \
    apt-get install -y gfortran make gcc && \
    rm -rf /var/lib/apt/lists/*

COPY packmol-21.2.1.tar.gz /app/packmol-21.2.1.tar.gz
