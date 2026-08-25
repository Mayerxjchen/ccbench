#!/bin/bash
cd /app
tar xzf packmol-21.2.1.tar.gz
cd packmol-21.2.1
./configure
make
cp packmol /usr/local/bin/packmol
cd /app
packmol < /dev/null 2>&1 | head -n 5 > /app/packmol_version.txt
