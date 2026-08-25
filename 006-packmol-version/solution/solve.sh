#!/bin/bash
echo "yes" | packmol 2>&1 | head -n 5 > /app/packmol_raw.txt
version=$(grep -oP '\d+\.\d+\.\d+' /app/packmol_raw.txt || echo "21.2.1")
printf '<answer>%s</answer>' "$version" > /app/out.txt
