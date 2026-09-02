"""Canonical release identity hashing and rebuild from the frozen on-disk state.

Every digest in the release manifest must recompute from disk so the frozen
anchors pin real bytes.  Two serializations are canonical:

- file: ``sha256(content)``
- directory tree: ``sha256(rel_path + '\\0' + file_sha256 + '\\0' ...)`` over the
  sorted relative paths — the same serialization ``skills_sha.hash_tree`` uses —
  with ``__pycache__`` and dotfiles excluded.  Interpreter cache bytes are not
  benchmark identity; a digest must not depend on whether a test was run
  locally.

``release_mismatches`` reports what the committed release gets wrong.
``regenerate_release`` rewrites every locally verifiable digest from ``root``
and recomputes ``release_digest``.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import dftworld_bench.experiments.ablation as ablation

# Release-manifest field that holds the digest for each component family.
_FIELD = {
    "schemas": "sha256",
    "site_adapter": "sha256",
    "skills": "sha256",
    "resource_profiles": "sha256",
    "platform_profiles": "sha256",
    "compute_runtimes": "sha256",
    "verifiers": "digest",
    "cases": "task_toml_sha256",  # plus instruction_sha256, handled specially
    "candidate_images": "dockerfile_sha256",
}


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_digest(root: Path) -> str:
    """Deterministic content digest over a directory tree (see module doc)."""
    entries = sorted(
        (p.relative_to(root).as_posix(), file_sha256(p))
        for p in root.rglob("*")
        if p.is_file()
        and not p.name.startswith(".")
        and "__pycache__" not in p.parts
    )
    h = hashlib.sha256()
    for rel, digest in entries:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(digest.encode("ascii"))
        h.update(b"\0")
    return h.hexdigest()


def disk_digests(component: str, entry: dict, root: Path) -> list[tuple[str, str, str]]:
    """(field_name, identity_label, disk_digest) for every locally verifiable
    digest a release entry must pin.  Remote-only entries (e.g. a SIF on the
    cluster) yield nothing — they cannot be checked from the host."""
    if component == "cases":
        case_dir = root / entry["case_id"]
        return [
            ("task_toml_sha256", "task.toml", file_sha256(case_dir / "task.toml")),
            ("instruction_sha256", "instruction.md", file_sha256(case_dir / "instruction.md")),
        ]
    if component == "candidate_images":
        if "dockerfile" not in entry:
            return []
        return [
            ("dockerfile_sha256", entry["dockerfile"], file_sha256(root / entry["dockerfile"]))
        ]
    path = entry.get("path")
    if not path:
        return []
    p = root / path
    want = tree_digest(p) if p.is_dir() else file_sha256(p)
    return [(_FIELD[component], path, want)]


def release_mismatches(release: dict, root: Path) -> list[tuple[str, str, str, str]]:
    """``(component, identity_label, disk_digest, release_digest)`` for every
    digest the committed release gets wrong.  Empty list means every locally
    verifiable digest recomputes from disk."""
    out: list[tuple[str, str, str, str]] = []
    for component, entries in release["components"].items():
        for entry in entries:
            for field, label, want in disk_digests(component, entry, root):
                have = entry.get(field)
                if have != want:
                    out.append((component, label, want, have))
    return out


def regenerate_components(release: dict, root: Path) -> dict:
    """Deep copy of ``components`` with every locally verifiable digest
    recomputed from ``root``."""
    components = copy.deepcopy(release["components"])
    for component, entries in components.items():
        for entry in entries:
            for field, _label, want in disk_digests(component, entry, root):
                entry[field] = want
    return components


def regenerate_release(release: dict, root: Path) -> dict:
    """Return a release with all locally verifiable digests recomputed from
    disk and ``release_digest`` re-derived (mutable fields left as-is)."""
    out = dict(release)
    out["components"] = regenerate_components(release, root)
    out["release_digest"] = ablation.release_digest(out)
    return out


# -- D11 qualification receipt gate -------------------------------------------

_RECEIPT_PATH = (
    "evidence/hpc-dispatcher/qualification/site-v1/receipt.json"
)


def check_qualification_receipt(
    root: Path, *, case_dir: Path | None = None
) -> dict:
    """Check for a valid D11 qualification receipt (fail-closed).

    Returns a status dict::

        {"status": "PASS", "receipt_path": ..., "digest": ...,
         "qual_requires": [...]}
        {"status": "BLOCKED_QUALIFICATION", "detail": ...}

    The verdicts are never read from the receipt — it carries none.  Every
    status is *derived* by
    :func:`dftworld_bench.experiments.qualification_receipt.verify_receipt`
    from the bound evidence; any broken anchor, missing receipt, or
    capability-matrix gap yields ``BLOCKED_QUALIFICATION``.

    ``case_dir`` optionally names the case being released: its
    ``[hpc].qual_requires`` names must all derive PASS on the receipt's
    capability matrix (via
    :func:`dftworld_bench.experiments.qualification_receipt.case_requirements_satisfied`)
    or the release is blocked — a case that needs CP2K is not released off an
    aggregate PASS while ``runtime.cp2k`` is NOT_RUN.  A missing or unknown
    name in ``qual_requires`` blocks too, naming the unmet capability.  The
    legacy site-ACL status is gone: the collector refuses cpu-workloads-on-the
    -gpu-queue profiles up front, and the provenance gate re-checks the ACL
    fact against the rebuilt SiteProfile, so a contradictory site change fails
    closed here as BLOCKED_QUALIFICATION.
    """
    import json

    from dftworld_bench.experiments.qualification_receipt import (
        case_requirements_satisfied,
        verify_receipt,
    )

    receipt_path = root / _RECEIPT_PATH
    if not receipt_path.is_file():
        return {
            "status": "BLOCKED_QUALIFICATION",
            "detail": f"D11 receipt not found: {receipt_path}",
        }
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "status": "BLOCKED_QUALIFICATION",
            "detail": f"D11 receipt unreadable: {exc}",
        }
    result = verify_receipt(
        receipt, root=root, receipt_dir=receipt_path.parent
    )
    common = {
        "receipt_path": str(receipt_path),
        "digest": receipt.get("digest"),
    }
    if result["problems"]:
        return {
            "status": "BLOCKED_QUALIFICATION",
            "detail": "D11 receipt failed derivation: "
            + "; ".join(result["problems"][:5]),
            **common,
        }
    derived = result["derived"]
    if derived["qualification_status"] != "PASS":
        return {
            "status": "BLOCKED_QUALIFICATION",
            "detail": (
                "D11 receipt derives "
                f"{derived['qualification_status']} "
                f"(gates={derived['gates']})"
            ),
            **common,
        }
    try:
        case_requires = _case_qual_requires(case_dir)
    except ValueError as exc:
        return {
            "status": "BLOCKED_QUALIFICATION",
            "detail": str(exc),
            **common,
        }
    if case_requires and not case_requirements_satisfied(derived, case_requires):
        capabilities = derived.get("capabilities") or {}
        unmet = [
            name for name in case_requires
            if capabilities.get(name) != "PASS"
        ]
        return {
            "status": "BLOCKED_QUALIFICATION",
            "detail": (
                f"case {Path(case_dir).name} qual_requires not satisfied: "
                f"unmet={unmet} capabilities={capabilities}"
            ),
            "qual_requires": list(case_requires),
            **common,
        }
    return {
        "status": "PASS",
        "qual_requires": list(case_requires),
        **common,
    }


def _case_qual_requires(case_dir: Path | None) -> tuple[str, ...]:
    """Resolve ``[hpc].qual_requires`` from the case manifest (fail-closed).

    Returns () when no case is named.  A broken or conflicting manifest raises
    ValueError so the caller can block rather than release.
    """
    if case_dir is None:
        return ()
    from dftworld_bench.contracts.case import CaseSpec

    try:
        spec = CaseSpec.load(case_dir)
    except Exception as exc:  # noqa: BLE001 — a broken manifest must block
        raise ValueError(
            f"case {Path(case_dir).name} qual_requires unresolvable: {exc}"
        ) from exc
    return tuple(spec.qual_requires)
