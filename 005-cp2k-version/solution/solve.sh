#!/bin/bash
/opt/cp2k/bin/cp2k.psmp --version 2>&1 | grep "CP2K version" > /app/out.txt
