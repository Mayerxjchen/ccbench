"""Recover immutable MatClaw paper and CIPS repository sources."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path


PUBLIC_COMMIT = "cebcf2be839af87663c0e5b64efaf1afe99f2e39"
RELEASE_COMMIT = "52557c077f5e3be8444a3f03ea10a647fbd442ca"
REPOSITORY_URL = "https://github.com/cz2014/MatClaw.git"
PAPER_URL = "https://arxiv.org/pdf/2604.02688v3"
PAPER_SHA256 = "28349bc2cf74a93d12a505c99864d1b4bdb2754f19dbee7848fe00288df33ce6"

SELECTED_REPOSITORY_PATHS = (
    ".ref/CuInP2S6.cif",
    ".ref/cips_monolayer.cif",
    ".ref/cips_monolayer.data",
    ".ref/Ecurve_1d.in",
    ".ref/cips_flip.png",
    "remote_jobs/_efield_calculator.py",
    "workspace_demo1a_distill",
    "workspace_demo1b_distill_pdf",
    "workspace_demo2a_curie_no_convergence",
    "workspace_demo2b_curie_with_convergence",
    "workspace_demo3_search",
)

SOURCE_SPEC = {
    "project": "MatClaw",
    "paper": {
        "title": (
            "MatClaw: An Autonomous Code-First LLM Agent for End-to-End "
            "Materials Exploration"
        ),
        "arxiv": "2604.02688v3",
        "url": PAPER_URL,
        "sha256": PAPER_SHA256,
    },
    "repository": {
        "url": REPOSITORY_URL,
        "public_branch": "public",
        "public_commit": PUBLIC_COMMIT,
        "release_branch": "release",
        "release_commit": RELEASE_COMMIT,
        "selected_paths": list(SELECTED_REPOSITORY_PATHS),
    },
    "system": "CuInP2S6",
}


def recover_repository(source_repo: Path, destination: Path) -> list[Path]:
    """Copy only the selected CIPS sources from a pinned repository checkout."""
    if destination.exists() and any(destination.iterdir()):
        raise FileExistsError(
            f"refusing to overlay nonempty recovery destination: {destination}"
        )
    copied: list[Path] = []
    for relative in SELECTED_REPOSITORY_PATHS:
        source = source_repo / relative
        if not source.exists():
            raise FileNotFoundError(f"selected upstream source is absent: {relative}")
        target = destination / relative
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
            copied.extend(path for path in target.rglob("*") if path.is_file())
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(target)
    return sorted(copied)


def _run(command: list[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip()


def _clone_release(repo_url: str, destination: Path) -> Path:
    clone = destination / "MatClaw"
    _run(
        [
            "git",
            "clone",
            "--filter=blob:none",
            "--no-checkout",
            repo_url,
            str(clone),
        ]
    )
    _run(["git", "checkout", "--detach", RELEASE_COMMIT], cwd=clone)
    actual = _run(["git", "rev-parse", "HEAD"], cwd=clone)
    if actual != RELEASE_COMMIT:
        raise RuntimeError(f"release commit mismatch: {actual}")
    return clone


def _download(url: str, destination: Path, expected_sha256: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": "dftworld-source-recovery"})
    with urllib.request.urlopen(request, timeout=120) as response:
        partial.write_bytes(response.read())
    payload = partial.read_bytes()
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256:
        partial.unlink()
        raise RuntimeError(
            f"download SHA-256 mismatch for {url}: {actual_sha256}"
        )
    if not payload.startswith(b"%PDF-") or b"%%EOF" not in payload[-4096:]:
        partial.unlink()
        raise RuntimeError(f"download is not a structurally recognizable PDF: {url}")
    partial.replace(destination)


def copy_runtime_locks(output: Path) -> list[Path]:
    """Copy the checked validation environment locks into a fresh package."""
    runtime_source_root = Path(__file__).resolve().parent
    copied = []
    for filename in (
        "teacher-runtime.lock.json",
        "teacher-runtime-requirements.txt",
    ):
        target = output / filename
        shutil.copyfile(runtime_source_root / filename, target)
        copied.append(target)
    return copied


def recover_sources(
    output: Path,
    *,
    repo_url: str = REPOSITORY_URL,
    source_repo: Path | None = None,
) -> list[Path]:
    """Recover the selected release snapshot, paper, and immutable source lock."""
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(
            f"refusing to recover into nonempty package destination: {output}"
        )
    output.mkdir(parents=True, exist_ok=True)
    if source_repo is None:
        with tempfile.TemporaryDirectory(prefix="matclaw-recovery-") as temp:
            clone = _clone_release(repo_url, Path(temp))
            copied = recover_repository(clone, output / "repository" / "release")
    else:
        actual = _run(["git", "rev-parse", "HEAD"], cwd=source_repo)
        if actual != RELEASE_COMMIT:
            raise RuntimeError(
                f"source repository must be at {RELEASE_COMMIT}, found {actual}"
            )
        copied = recover_repository(source_repo, output / "repository" / "release")

    paper = output / "paper" / "matclaw-2604.02688v3.pdf"
    _download(PAPER_URL, paper, PAPER_SHA256)
    copied.append(paper)

    source_lock = output / "source.lock.json"
    source_lock.write_text(
        json.dumps(SOURCE_SPEC, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    copied.append(source_lock)
    copied.extend(copy_runtime_locks(output))
    return sorted(copied)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-url", default=REPOSITORY_URL)
    parser.add_argument("--source-repo", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    recovered = recover_sources(
        args.output,
        repo_url=args.repo_url,
        source_repo=args.source_repo,
    )
    print(f"public_commit={PUBLIC_COMMIT}")
    print(f"release_commit={RELEASE_COMMIT}")
    print(f"recovered_files={len(recovered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
