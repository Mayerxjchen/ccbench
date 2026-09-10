"""Private verifier for the neutral example; emits the Bench result contract."""
import json
import os
import sys
from pathlib import Path

submission = Path(sys.argv[1] if len(sys.argv) > 1 else "/submission")
output = Path(os.environ.get("BENCH_VERIFIER_OUTPUT_DIR", "/logs/verifier"))
output.mkdir(parents=True, exist_ok=True)
try:
    answer = json.loads((submission / "answer.json").read_text())
    passed = (isinstance(answer, dict) and set(answer) == {"sum"}
              and type(answer["sum"]) is int and answer["sum"] == 5)
except (OSError, ValueError):
    passed = False

result = {
    "run_id": os.environ.get("BENCH_RUN_ID", "example"),
    "result_class": "VALID_RESULT",
    "failure_code": "PASS" if passed else "SCIENTIFIC_FAIL",
    "reason": "correct sum" if passed else "missing or incorrect integer sum",
    "retryable": False,
    "is_counted_scientifically": True,
}
(output / "result.json").write_text(json.dumps(result) + "\n")
(output / "reward.txt").write_text("1\n" if passed else "0\n")
sys.exit(0 if passed else 1)
