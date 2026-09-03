#!/usr/bin/env python3
"""Cheap local diagnostics for the draft's public contract and fixture intent."""
import json
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from verify_submission import check  # noqa: E402

class ContractTests(unittest.TestCase):
  def test_public_system_identity(self):
    data = json.loads((ROOT / "public/system.json").read_text())
    target = data["interfaces"]["cips_bulk_3x3x1"]
    self.assertEqual(target["natoms"], 90)
    self.assertEqual(target["composition"], {"Cu": 9, "In": 9, "P": 18, "S": 54})
    self.assertEqual(target["elements"], ["Cu", "In", "P", "S"])

  def test_draft_has_fail_closed_release_state(self):
    self.assertFalse(json.loads((ROOT / "benchmark_valid.json").read_text())["benchmark_valid"])
    self.assertEqual(json.loads((ROOT / "reference/thresholds.json").read_text())["status"], "draft")
    self.assertEqual(json.loads((ROOT / "reference/reference.json").read_text())["state"], "planned")

  def test_empty_submission_rejected(self):
    result = check(ROOT / "tests/fixtures/negative")
    self.assertFalse(result["valid"])

  def test_fixture_closure_files_exist(self):
    for rel in ("positive/README.md", "alternative-valid/README.md", "negative/forged_or_duplicate_labels.yaml", "negative/missing_loop_or_model.yaml"):
        self.assertTrue((ROOT / "tests/fixtures" / rel).is_file())
