FROM dftworld-base-packmol-src

RUN cd /app && tar xzf packmol-21.2.1.tar.gz && \
    cd packmol-21.2.1 && ./configure && make && \
    cp packmol /usr/local/bin/ && \
    apt-get update && \
    apt-get install -y --no-install-recommends libgfortran5 libgomp1 && \
    apt-get remove -y gfortran gcc make && \
    apt-get autoremove -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /app/packmol-21.2.1 /app/packmol-21.2.1.tar.gz
