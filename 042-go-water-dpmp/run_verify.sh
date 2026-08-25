#!/bin/bash
# 042 验证脚本 — 用 ! run_verify.sh 运行
# Runs the FULL 042 test suite + syntax + structure C-C audit.  A failure
# anywhere means the working tree is not ready to commit.
set -e
cd "$(dirname "$0")"

echo "=== 1. Syntax check (all stage scripts + modules) ==="
for f in solution/expert/run.sh solution/expert/01-aimd/run.sh \
         solution/expert/02-train/run.sh solution/expert/03-active-learning/run.sh \
         solution/expert/04-validation/run.sh; do
  bash -n "$f"
done
python3 -m py_compile \
  solution/expert/00-structure-generation/build_all_interfaces.py \
  solution/expert/01-aimd/convert.py \
  solution/expert/02-train/train.py \
  solution/expert/03-active-learning/parse_cp2k_label.py \
  solution/expert/03-active-learning/cp2k_input.py \
  tests/test_structure_origin.py tests/test_outputs.py \
  tests/test_parse_cp2k_label.py tests/test_cp2k_input.py
echo "SYNTAX-OK"
echo ""

echo "=== 2. Production generator: honeycomb C-C audit ==="
python3 -c "
import tempfile, os, sys, numpy as np
sys.path.insert(0, 'solution/expert/00-structure-generation')
import build_all_interfaces as B
with tempfile.TemporaryDirectory() as td:
    os.environ['AI2KIT_042_STRUCTURES_DIR'] = td + '/structures'
    os.environ['AI2KIT_042_SEED'] = '42'
    B.main()
    for iface in ['graphene-water','graphene-O12','graphene-O25','graphene-O50']:
        d = td + '/structures/' + iface
        box = np.loadtxt(d + '/box.raw').reshape(3,3)
        coord = np.loadtxt(d + '/coord.raw').reshape(-1,3)
        typ = np.loadtxt(d + '/type.raw', dtype=int)
        c = coord[typ==2]
        delta = c[:,None,:] - c[None,:,:]
        delta = delta - np.diag(box)*np.round(delta/np.diag(box))
        dist = np.sqrt((delta**2).sum(-1))
        n=len(c); iu=np.triu_indices(n,1)
        print(f'  {iface}: min C-C={dist[iu].min():.4f} A, pairs<1.3A={(dist[iu]<1.3).sum()}')
print('  GEN-OK')
"
echo ""

echo "=== 3. Full pytest suite (all 042 tests) ==="
python3 -m pytest -q tests/
echo ""

echo "=== ALL DONE ==="
