"""mlffbench thin CLI (P3): five verbs, zero logic — forwarding only.

The CLI must orchestrate existing modules, not reimplement them, and the
private cluster profile must never live inside the repository.
"""

from __future__ import annotations

import json
from pathlib import Path


import dftworld_bench.cli as cli


def test_setup_reports_environment_and_runtime_locks(capsys):
    code = cli.main(["setup", "--quiet"])
    out = capsys.readouterr().out
    assert code == 0
    report = json.loads(out)
    assert report["python_at_least_3_11"] is True
    assert report["template_present"] is True
    assert "cp2k" in report["runtime_locks"]
    assert "ai2kit" in report["runtime_locks"]


def test_site_configure_refuses_profile_inside_repo(tmp_path, capsys):
    code = cli.main(["site", "configure", "--out", str(Path(cli.ROOT) / "cluster_profile.toml")])
    assert code == 2
    assert "OUTSIDE the repository" in capsys.readouterr().err


def test_site_configure_refuses_overwrite(tmp_path):
    out = tmp_path / "cluster_profile.toml"
    out.write_text("existing")
    assert cli.main(["site", "configure", "--out", str(out)]) == 2


def test_site_configure_copies_and_validates_template(tmp_path, capsys):
    out = tmp_path / "nest" / "cluster_profile.toml"
    code = cli.main(["site", "configure", "--out", str(out), "--validate"])
    assert code == 0
    assert out.is_file()
    assert "template copied" in capsys.readouterr().err


def test_site_qualify_forwards_to_qualify_case(monkeypatch):
    from scripts.qualification import qualify_case

    seen: list[list[str]] = []

    def fake_main(argv):
        seen.append(list(argv))
        return 7

    monkeypatch.setattr(qualify_case, "main", fake_main)
    assert cli.main([
        "site", "qualify", "--profile", "/tmp/p.toml", "--case", "034", "--dry-run",
    ]) == 7
    assert seen[0] == ["--profile", "/tmp/p.toml", "--case", "034", "--dry-run"]


def test_report_forwards_to_verify_evidence(monkeypatch):
    from scripts.ablation import verify_evidence

    seen: list[list[str]] = []

    def fake_main(argv):
        seen.append(list(argv))
        return 3

    monkeypatch.setattr(verify_evidence, "main", fake_main)
    assert cli.main(["report", "--jobs-dir", "jobs/x"]) == 3
    assert seen[0] == ["--jobs-dir", "jobs/x"]


def test_run_forwards_argv_to_eval_main(monkeypatch):
    import eval as eval_mod

    seen: list[list[str]] = []

    def fake_main():
        import sys

        seen.append(list(sys.argv))

    monkeypatch.setattr(eval_mod, "main", fake_main)
    assert cli.main(["run", "034-ai2kit-water64-end-to-end-potential", "--skills"]) == 0
    assert seen[0] == [
        "mlffbench run", "034-ai2kit-water64-end-to-end-potential", "--skills",
    ]


def test_cli_is_pure_forwarding_no_new_logic():
    """The CLI module must not reach the gateway/adapter/transport layers."""
    text = Path(cli.__file__).read_text(encoding="utf-8")
    for forbidden in ("Gateway(", "SlurmAdapter(", "subprocess", "sbatch"):
        assert forbidden not in text, forbidden


def test_compute_configure_and_validate(tmp_path, capsys):
    out = tmp_path / "my_compute_profile.json"
    code = cli.main(["compute", "configure", "--out", str(out)])
    assert code == 0
    assert out.is_file()
    assert "template copied" in capsys.readouterr().err

    # Validate
    val_code = cli.main(["compute", "validate", "--profile", str(out)])
    assert val_code == 0
    assert "validates" in capsys.readouterr().err


def test_compute_configure_refuses_inside_repo(capsys):
    inside = Path(cli.ROOT) / "my_compute_profile.json"
    code = cli.main(["compute", "configure", "--out", str(inside)])
    assert code == 2
    assert "OUTSIDE the repository" in capsys.readouterr().err
