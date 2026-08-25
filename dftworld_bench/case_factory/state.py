"""Factory state: orthogonal derived gates distinct from ``case_status`` and
from the case-internal G0..G12 release gates.

Rendering may set only ``design_valid`` and ``target_adapter_valid``.
``runtime_contract_valid`` / ``verifier_command_valid`` are contract-level gates
set by the smoke checker: the generated Draft's runtime contract (task.toml,
Dockerfile, harness lifecycle) holds and the verifier command carries the
isolation contract.  ``runtime_valid`` / ``candidate_smoke_valid`` require real
container-isolated execution (Candidate sandbox + Verifier container) and stay
false in every current runner.  ``discovery_complete`` / ``diagnosis_complete``
belong to the later Discovery/Diagnosis plan and are always false in P0.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

LOCK_RELPATH = Path("source/dftworld-target.lock.json")


@dataclass(frozen=True)
class FactoryGates:
    """Derived factory gates recorded inside the target lock."""

    design_valid: bool = False
    target_adapter_valid: bool = False
    # Contract gates: provable without container exec (subprocess Candidate,
    # audit-asserted verifier argv).  Set by the smoke checker on PASS.
    runtime_contract_valid: bool = False
    verifier_command_valid: bool = False
    # Full runtime gates: require real container Candidate sandbox + real
    # Verifier container.  No current runner may set these true.
    runtime_valid: bool = False
    candidate_smoke_valid: bool = False
    discovery_complete: bool = False
    diagnosis_complete: bool = False

    def with_updates(self, **kw: bool) -> "FactoryGates":
        """Return a new gates object with the given fields replaced."""
        return FactoryGates(**{**asdict(self), **kw})

    def as_dict(self) -> dict:
        return asdict(self)


def read_factory_state(case_dir: Path) -> FactoryGates:
    """Read factory gates from the target lock; missing lock -> all false."""
    import json

    lock = Path(case_dir) / LOCK_RELPATH
    if not lock.is_file():
        return FactoryGates()
    try:
        payload = json.loads(lock.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return FactoryGates()
    gates = payload.get("factory_gates") or {}
    return FactoryGates(**{k: bool(gates.get(k, False)) for k in asdict(FactoryGates())})


def write_factory_state(case_dir: Path, gates: FactoryGates) -> None:
    """Record factory gates written by an independent check.

    Only the ``factory_gates`` field of the target lock is replaced; the lock's
    generated-file digests, design hash and execution identity are preserved
    byte-for-byte.  Never called by rendering — runtime/smoke gates are set by
    their independent checks (Tasks 8/9) only.
    """
    import json

    lock = Path(case_dir) / LOCK_RELPATH
    payload = json.loads(lock.read_text(encoding="utf-8"))
    payload["factory_gates"] = gates.as_dict()
    lock.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
