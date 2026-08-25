#!/bin/bash
version=$(python3 --version | awk '{print $2}')
printf '<answer>%s</answer>' "$version" > /app/out.txt
