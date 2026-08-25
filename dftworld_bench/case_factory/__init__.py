"""Case Factory: repository-owned rendering of Builder case-designs into
dftworld-executable Draft Cases.

Target Adapter is dftworld infrastructure, not a Skill.  The portable Builder
may invoke this CLI with ``--target dftworld`` but never duplicates adapter
logic.  See docs/case-factory/CASE-FACTORY.md.
"""

from dftworld_bench.case_factory.target import (
    GeneratedFile,
    TargetAdapter,
    TargetVerdict,
)
from dftworld_bench.case_factory.state import FactoryGates, read_factory_state

__all__ = [
    "GeneratedFile",
    "TargetAdapter",
    "TargetVerdict",
    "FactoryGates",
    "read_factory_state",
]
