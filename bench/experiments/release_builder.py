"""Canonical release identity hashing and rebuild from the frozen Git tree at source_commit.

Every digest in the release manifest must recompute from the frozen Git tree at
source_commit so the frozen anchors pin real immutable bytes.  Two serializations
are canonical:

- file: ``sha256(content)``
- directory tree: ``sha256(rel_path + '\\0' + file_sha256 + '\\0' ...)`` over the
  sorted relative paths — the same serialization ``skills_sha.hash_tree`` uses —
  with ``__pycache__`` and dotfiles excluded.  Interpreter cache bytes are not
  benchmark identity; a digest must not depend on whether a test was run
  locally.

``release_mismatches`` reports what the committed release gets wrong.
``regenerate_release`` rewrites every locally verifiable digest from the target
commit's Git tree and recomputes ``release_digest``.
"""

from __future__ import annotations

import copy
import hashlib
import re
import subprocess
from pathlib import Path

import bench.experiments.ablation as ablation

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


def validate_source_commit(commit: str, root: Path) -> None:
    """Enforce strict commit pinning: must be a full 40-character hex commit SHA
    that exists in the git repository. Rejects mutable refs (main, HEAD, tags)
    and short SHAs.
    """
    if commit == "DISK":
        return
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise ValueError(
            f"Invalid source_commit '{commit}': must be a full 40-character hex SHA. "
            f"Mutable refs (main, HEAD, tags) and abbreviated SHAs are strictly rejected."
        )
    proc = subprocess.run(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
        cwd=root,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise ValueError(
            f"Commit '{commit}' does not exist as a commit in repository {root}."
        )


def git_file_sha256(commit: str, rel_path: str, root: Path) -> str:
    """Read file content at ``commit:rel_path`` from git and return its sha256."""
    proc = subprocess.run(
        ["git", "show", f"{commit}:{rel_path}"],
        cwd=root,
        capture_output=True,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise FileNotFoundError(f"Cannot read {commit}:{rel_path}: {err}")
    return hashlib.sha256(proc.stdout).hexdigest()


def git_tree_digest(commit: str, rel_path: str, root: Path) -> str:
    """Deterministic content digest over a directory tree at ``commit:rel_path`` from git.

    Matches tree_digest: sorted relative posix paths and file_sha256 joined with
    NUL bytes, excluding dotfiles and __pycache__.
    """
    proc = subprocess.run(
        ["git", "ls-tree", "-r", "--full-name", commit, rel_path],
        cwd=root,
        capture_output=True,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace").strip()
        raise FileNotFoundError(f"Cannot list {commit}:{rel_path}: {err}")

    entries: list[tuple[str, str]] = []
    for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
        if not line:
            continue
        meta, full_path = line.split("\t", 1)
        parts = meta.split()
        if len(parts) < 3:
            continue
        blob_sha = parts[2]
        p = Path(full_path)
        if p.name.startswith(".") or "__pycache__" in p.parts:
            continue
        rel = p.relative_to(rel_path).as_posix()
        cat_proc = subprocess.run(
            ["git", "cat-file", "-p", blob_sha],
            cwd=root,
            capture_output=True,
        )
        if cat_proc.returncode != 0:
            err = cat_proc.stderr.decode("utf-8", errors="replace").strip()
            raise FileNotFoundError(f"Cannot cat blob {blob_sha} for {full_path}: {err}")
        digest = hashlib.sha256(cat_proc.stdout).hexdigest()
        entries.append((rel, digest))

    entries.sort()
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
    return entry_digests(component, entry, root, commit="DISK")


def entry_digests(
    component: str, entry: dict, root: Path, *, commit: str | None = None
) -> list[tuple[str, str, str]]:
    """(field_name, identity_label, digest) computed either from a Git commit
    tree (when ``commit`` is given and != 'DISK') or from the host disk.
    """
    use_git = commit is not None and commit != "DISK"
    if use_git:
        validate_source_commit(commit, root)

    if component == "cases":
        case_id = entry["case_id"]
        if use_git:
            toml_sha = git_file_sha256(commit, f"{case_id}/task.toml", root)
            inst_sha = git_file_sha256(commit, f"{case_id}/instruction.md", root)
        else:
            case_dir = root / case_id
            toml_sha = file_sha256(case_dir / "task.toml")
            inst_sha = file_sha256(case_dir / "instruction.md")
        return [
            ("task_toml_sha256", "task.toml", toml_sha),
            ("instruction_sha256", "instruction.md", inst_sha),
        ]

    if component == "candidate_images":
        if "dockerfile" not in entry:
            return []
        dockerfile = entry["dockerfile"]
        if use_git:
            df_sha = git_file_sha256(commit, dockerfile, root)
        else:
            df_sha = file_sha256(root / dockerfile)
        return [("dockerfile_sha256", dockerfile, df_sha)]

    path = entry.get("path")
    if not path:
        return []

    p = root / path
    if use_git:
        # Determine if path is a directory in git or a file
        chk = subprocess.run(
            ["git", "cat-file", "-t", f"{commit}:{path}"],
            cwd=root,
            capture_output=True,
        )
        obj_type = chk.stdout.decode().strip()
        if obj_type == "tree":
            want = git_tree_digest(commit, path, root)
        elif obj_type == "blob":
            want = git_file_sha256(commit, path, root)
        else:
            raise FileNotFoundError(f"Object {commit}:{path} not found in git (type={obj_type})")
    else:
        want = tree_digest(p) if p.is_dir() else file_sha256(p)

    return [(_FIELD[component], path, want)]


def release_mismatches(
    release: dict, root: Path, *, commit: str | None = None
) -> list[tuple[str, str, str, str]]:
    """``(component, identity_label, target_digest, release_digest)`` for every
    digest the release manifest gets wrong compared to the target tree.

    Defaults to checking against ``release['source_commit']`` in Git.
    Pass ``commit='DISK'`` to inspect against the uncommitted host disk.
    """
    target_commit = commit if commit is not None else release.get("source_commit")
    if target_commit is None:
        target_commit = "DISK"
    if target_commit != "DISK":
        validate_source_commit(target_commit, root)

    out: list[tuple[str, str, str, str]] = []
    for component, entries in release["components"].items():
        for entry in entries:
            for field, label, want in entry_digests(
                component, entry, root, commit=target_commit
            ):
                have = entry.get(field)
                if have != want:
                    out.append((component, label, want, have))
    return out


def regenerate_components(
    release: dict, root: Path, *, commit: str | None = None
) -> dict:
    """Deep copy of ``components`` with every locally verifiable digest
    recomputed from ``commit`` (defaults to release source_commit)."""
    target_commit = commit if commit is not None else release.get("source_commit")
    if target_commit is None:
        target_commit = "DISK"
    if target_commit != "DISK":
        validate_source_commit(target_commit, root)

    components = copy.deepcopy(release["components"])
    for component, entries in components.items():
        for entry in entries:
            for field, _label, want in entry_digests(
                component, entry, root, commit=target_commit
            ):
                entry[field] = want
    return components


def regenerate_release(
    release: dict, root: Path, *, commit: str | None = None
) -> dict:
    """Return a release with all locally verifiable digests recomputed from
    the target commit (or disk) and ``release_digest`` re-derived."""
    target_commit = commit if commit is not None else release.get("source_commit")
    if target_commit is None:
        target_commit = "DISK"
    if target_commit != "DISK":
        validate_source_commit(target_commit, root)

    out = dict(release)
    if target_commit != "DISK":
        out["source_commit"] = target_commit
    out["components"] = regenerate_components(release, root, commit=target_commit)
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
         "qualification_requires": [...]}
        {"status": "BLOCKED_QUALIFICATION", "detail": ...}

    The verdicts are never read from the receipt — it carries none.  Every
    status is *derived* by
    :func:`bench.experiments.qualification_receipt.verify_receipt`
    from the bound evidence; a missing receipt or any broken anchor yields
    ``BLOCKED_QUALIFICATION``.

    ``case_dir`` optionally names the case being released (spec §5b): the
    release gates on the case's OWN effective ``[hpc.qualification]`` requires
    (declared ∪ registry auto-derivation) via
    :func:`bench.experiments.qualification_receipt.case_requirements_satisfied`
    — never on the site-wide aggregate.  A MatClaw case whose
    ``dispatcher.gpu`` / ``runtime.matclaw-gpu`` both derive PASS is released
    while the aggregate is PARTIAL (cp2k/ai2kit canaries NOT_RUN); a
    034-style case naming ``runtime.cp2k`` / ``runtime.ai2kit`` stays blocked
    until those canaries ran.  Structural breakage (schema, code identity,
    overlay) fails EVERY capability, so a case-gated release blocks on it too
    without a separate problems gate.  Without ``case_dir`` the call keeps the
    legacy operator semantics: the site-wide aggregate must be full PASS —
    PARTIAL (a runtime canary NOT_RUN) is the honest report and is NOT a
    release.
    """
    import json

    from bench.experiments.qualification_receipt import (
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
    derived = result["derived"]
    common = {
        "receipt_path": str(receipt_path),
        "digest": receipt.get("digest"),
    }
    if case_dir is None:
        # Legacy site-qualification path: full PASS only.  Problems always
        # surface as a FAIL capability here, so the status check subsumes
        # them; the detail keeps the aggregate readable for operators.
        if result["problems"] or derived["qualification_status"] != "PASS":
            return {
                "status": "BLOCKED_QUALIFICATION",
                "detail": (
                    "D11 receipt derives "
                    f"{derived['qualification_status']} "
                    f"(gates={derived['gates']})"
                ),
                **common,
            }
        return {
            "status": "PASS",
            "qualification_requires": [],
            **common,
        }
    try:
        case_requires = _case_qualification_requires(case_dir)
    except ValueError as exc:
        return {
            "status": "BLOCKED_QUALIFICATION",
            "detail": str(exc),
            **common,
        }
    if not case_requirements_satisfied(derived, case_requires):
        capabilities = derived.get("capabilities") or {}
        unmet = [
            name for name in case_requires
            if capabilities.get(name) != "PASS"
        ]
        return {
            "status": "BLOCKED_QUALIFICATION",
            "detail": (
                f"case {Path(case_dir).name} qualification requires not "
                f"satisfied: unmet={unmet} capabilities={capabilities}"
            ),
            "qualification_requires": list(case_requires),
            **common,
        }
    return {
        "status": "PASS",
        "qualification_requires": list(case_requires),
        **common,
    }


def _case_qualification_requires(case_dir: Path | None) -> tuple[str, ...]:
    """Resolve the case's EFFECTIVE ``[hpc.qualification]`` requires.

    Returns () when no case is named.  A broken or conflicting manifest raises
    ValueError so the caller can block rather than release.
    """
    if case_dir is None:
        return ()
    from bench.contracts.case import CaseSpec

    try:
        spec = CaseSpec.load(case_dir)
    except Exception as exc:  # noqa: BLE001 — a broken manifest must block
        raise ValueError(
            f"case {Path(case_dir).name} qualification requires "
            f"unresolvable: {exc}"
        ) from exc
    return tuple(spec.effective_qualification_requires)
