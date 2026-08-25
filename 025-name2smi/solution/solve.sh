#!/bin/bash
source /app/.venv/bin/activate
python3 -c "
import json
from rdkit import Chem

d = json.load(open('/app/name_lookup.json'))
smi = d['2-acetoxybenzoic acid']
canon = Chem.MolToSmiles(Chem.MolFromSmiles(smi))
with open('/app/out.txt', 'w') as f:
    f.write(canon)
"
