"""Make repo-root packages importable for plain ``pytest`` runs.

``python -m pytest`` prepends the working directory; the bare ``pytest``
entry point does not. Tests import ``scripts.*`` and ``schemas`` by
repo-relative name, so pin the repo root onto sys.path here.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
