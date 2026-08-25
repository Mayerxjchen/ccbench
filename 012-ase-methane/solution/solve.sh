#!/bin/bash
source /app/.venv/bin/activate
python3 -c "
from ase.build import molecule
mol = molecule('CH4')
mol.write('/app/CH4.xyz')
"
