"""A copied paper template must run through the real public contract."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from bench.mvp import export_case, freeze_submission, resolve_verifier_bundle
from bench.suite import validate_suite

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('answer,code', [({'sum': 5}, 'PASS'),
                                      ({'sum': 6}, 'SCIENTIFIC_FAIL'),
                                      ({'sum': True}, 'SCIENTIFIC_FAIL')])
def test_copied_template_exports_only_public_input_and_verifies(tmp_path, answer, code):
    paper = tmp_path / 'paper'
    shutil.copytree(ROOT / 'examples/case-template', paper)
    assert validate_suite(paper)['validation'] == 'PASS'
    case = paper / 'cases/001-case-name'
    workspace = tmp_path / 'run/workspace'
    export_case(case, workspace)
    assert json.loads((workspace / 'numbers.json').read_text()) == [2, 3]
    assert not (workspace / 'verifier').exists()
    (workspace / 'final/answer.json').write_text(json.dumps(answer))
    sealed = tmp_path / 'sealed'
    freeze_submission(workspace, sealed)
    bundle = resolve_verifier_bundle(case, tmp_path / 'run', strict_external=True)
    logs = tmp_path / 'logs'
    proc = subprocess.run(['bash', str(bundle / 'test.sh'), str(sealed)],
                          env={**os.environ, 'BENCH_VERIFIER_OUTPUT_DIR': str(logs)},
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == (0 if code == 'PASS' else 1), proc.stderr
    result = json.loads((logs / 'result.json').read_text())
    assert result['result_class'] == 'VALID_RESULT'
    assert result['failure_code'] == code
