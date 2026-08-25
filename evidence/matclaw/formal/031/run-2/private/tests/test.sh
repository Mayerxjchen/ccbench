#!/bin/bash
set -e
/opt/matclaw/bin/python -m pytest -q /tests/test_outputs.py /tests/test_active_contract.py -rA \
  && echo 1 > /logs/verifier/reward.txt \
  || echo 0 > /logs/verifier/reward.txt
