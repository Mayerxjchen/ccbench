"""Root directory allowlist and anti-proliferation gate (R3-20).

Enforces clean root workspace hygiene:
- Prohibits legacy sprawl directories (base-env-build, reference, infra, portable case builder).
- Prevents root-level clutter (ad-hoc slurm scripts, txt outputs, debug probes).
- Validates that only officially approved directories and files exist at repository root.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FORBIDDEN_ROOT_NAMES = {
    "base-env-build",
    "reference",
    "infra",
    "scientific-benchmark-case-builder-portable",
    "qualify_gpu.py",
    "test_job.slurm",
    "unique_src.txt",
    "walkthrough.md",
    "implementation_plan.md",
}

ALLOWED_ROOT_DIRS = {
    ".git",
    ".github",
    ".pytest_cache",
    ".venv",
    ".vscode",
    ".test-gateway-workspace",
    "__pycache__",
    "benchmark",
    "cases",
    "ccbench",
    "ccbench.egg-info",
    "ccbench",
    "docs",
    "evidence",
    "examples",
    "experiments",
    "maintainer",
    "releases",
    "runs",
    "runtimes",
    "schemas",
    "scripts",
    "src",
    "tests",
}

ALLOWED_ROOT_FILES = {
    ".DS_Store",
    ".dockerignore",
    ".env",
    ".env.example",
    ".gitattributes",
    ".gitignore",
    ".python-version",
    "conftest.py",
    "eval.py",
    "portfolio-report.json",
    "pyproject.toml",
    "README.md",
    "skills_sha.py",
    "summarize.py",
    "uv.lock",
}


def test_no_forbidden_legacy_artifacts_at_root():
    """Ensure strictly none of the retired/superseded artifacts exist at repository root."""
    present_forbidden = [name for name in FORBIDDEN_ROOT_NAMES if (ROOT / name).exists()]
    assert not present_forbidden, (
        f"Forbidden legacy artifacts detected at root: {present_forbidden}. "
        "These have been retired or migrated to runtimes/, maintainer/, or tests/."
    )


def test_root_entries_adhere_strictly_to_allowlist():
    """All top-level entries in the repository must be explicitly declared in the allowlist."""
    actual_entries = {p.name: p for p in ROOT.iterdir()}

    unexpected_dirs = []
    unexpected_files = []

    for name, p in actual_entries.items():
        if name == ".git":
            # In git worktrees, .git is a file referencing the main worktree; in standard clones it's a directory.
            continue
        if p.is_dir():
            if name not in ALLOWED_ROOT_DIRS:
                unexpected_dirs.append(name)
        elif p.is_file() or p.is_symlink():
            if name not in ALLOWED_ROOT_FILES:
                unexpected_files.append(name)

    error_messages = []
    if unexpected_dirs:
        error_messages.append(f"Unexpected directories found at repo root: {sorted(unexpected_dirs)}")
    if unexpected_files:
        error_messages.append(f"Unexpected files found at repo root: {sorted(unexpected_files)}")

    assert not error_messages, "; ".join(error_messages)
