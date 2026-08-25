# ChemGraph 025–030 Benchmark Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the six public instructions, oracle artifacts, evaluator checks, network isolation, and validation metadata so cases 025–030 are fair and reproducible.

**Architecture:** A root contract test statically enforces the shared benchmark rules. Each standalone hidden evaluator retains its independent scientific recomputation but uses canonical method identity and ASE-compatible atom-wise force norms. Docker oracle and forged-method runs provide behavioral evidence, followed by one authorized pagent smoke run for the revised 029 instruction.

**Tech Stack:** Python 3.12, pytest, ASE 3.25.0, MACE-MP-0, TBLite 0.4.0, Bash, TOML, Docker, pagent.

## Global Constraints

- Preserve all numerical reference values, image digests, public molecular inputs, and source provenance.
- Require delivered geometries to satisfy `max_i ||F_i|| < 0.01 eV/Å` under the specified calculator.
- Treat BFGS with `fmax=0.01` and 1000 steps as a reference workflow, not a mandatory optimizer identity.
- Require canonical methods `mace_mp / medium-mpa-0` and `GFN2-xTB` after case/whitespace normalization.
- Preserve the 029 `spin=0` fidelity lock for every species, including O2.
- Use `/app/.venv/bin/python` in agent-facing runtime instructions.
- Emit four-column standard XYZ artifacts from every oracle.
- Set `allow_internet = false` for all six cases.
- Do not update validation claims until the corresponding fresh command has completed successfully.

---

### Task 1: Add Shared Contract Regression Tests

**Files:**
- Create: `tests/test_chemgraph_case_contracts.py`
- Test: `tests/test_chemgraph_case_contracts.py`

**Interfaces:**
- Consumes: the six case directories and their text/JSON/TOML files.
- Produces: pytest checks that make every known contract mismatch reproducible on the host.

- [ ] **Step 1: Write the failing contract test**

Create `tests/test_chemgraph_case_contracts.py` with these constants and checks:

```python
import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = (
    "025-name2opt-so2",
    "026-name2vib-water",
    "027-name2gibbs-co2",
    "028-name2file-so2",
    "029-react2enthalpy-methane",
    "030-react2gibbs-ammonia",
)
MACE_CASES = CASES[:2] + (CASES[3],)
XTB_CASES = (CASES[2],) + CASES[4:]
FORCE_CONTRACT = "max_i ||F_i|| < 0.01 eV/Å"


def text(case, relative):
    return (ROOT / case / relative).read_text()


def test_public_instructions_expose_the_force_and_runtime_contracts():
    for case in CASES:
        instruction = text(case, "instruction.md")
        assert FORCE_CONTRACT in instruction, case
        assert "/app/.venv/bin/python" in instruction, case
        assert "source /app/.venv/bin/activate" not in instruction, case


def test_cases_are_offline():
    for case in CASES:
        task = tomllib.loads(text(case, "task.toml"))
        assert task["environment"]["allow_internet"] is False, case


def test_evaluators_require_canonical_method_identity():
    for case in CASES:
        evaluator = text(case, "tests/test_outputs.py")
        assert "CANONICAL_METHOD" in evaluator, case
        assert "normalize_method" in evaluator, case
        assert "not-mace" not in evaluator
        assert "not-gfn2" not in evaluator


def test_mace_force_checks_use_atom_wise_norms():
    for case in MACE_CASES:
        evaluator = text(case, "tests/test_outputs.py")
        assert "np.linalg.norm(forces, axis=1).max()" in evaluator, case
        assert "np.abs(forces).max()" not in evaluator, case


def test_xtb_oracles_emit_standard_xyz():
    for case in XTB_CASES:
        solution = text(case, "solution/solve.sh")
        assert 'write(out_xyz, atoms, format="xyz")' in solution, case


def test_029_locks_spin_but_not_optimizer_identity():
    instruction = text(CASES[4], "instruction.md")
    assert "spin = 0" in instruction
    assert "BFGS is the pinned reference workflow, not a required optimizer" in instruction
    assert "use ASE `BFGS`" not in instruction


def test_smoke_metadata_agrees_per_case():
    for case in CASES:
        validation = json.loads(text(case, "VALIDATION.json"))
        summary = json.loads(text(case, "benchmark_valid.json"))
        assert validation["agent_smoke_tested"] == summary["agent_smoke_tested"], case
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run --extra dev pytest tests/test_chemgraph_case_contracts.py -q
```

Expected: failures for force wording, runtime activation, internet access, canonical method identity, MACE force norm, xTB XYZ output, and 029 smoke disagreement.

- [ ] **Step 3: Commit the failing regression test**

```bash
git add tests/test_chemgraph_case_contracts.py
git commit -m "test: capture ChemGraph benchmark contract gaps"
```

---

### Task 2: Align Public Instructions and Network Isolation

**Files:**
- Modify: `025-name2opt-so2/instruction.md`
- Modify: `026-name2vib-water/instruction.md`
- Modify: `027-name2gibbs-co2/instruction.md`
- Modify: `028-name2file-so2/instruction.md`
- Modify: `029-react2enthalpy-methane/instruction.md`
- Modify: `030-react2gibbs-ammonia/instruction.md`
- Modify: each case's `task.toml`
- Test: `tests/test_chemgraph_case_contracts.py`

**Interfaces:**
- Consumes: `FORCE_CONTRACT` and runtime/network expectations from Task 1.
- Produces: public prompts whose mandatory conditions exactly match evaluator-observable conditions.

- [ ] **Step 1: Replace ambiguous convergence prose**

Add this mandatory sentence to every instruction:

```markdown
The delivered optimized geometry must satisfy **`max_i ||F_i|| < 0.01 eV/Å`**
under the specified calculator. ASE BFGS with `fmax=0.01` and at most 1000 steps
is the pinned reference workflow, but any optimizer that meets the final force
criterion is acceptable.
```

For reaction cases, use “every delivered optimized geometry.” Remove wording that
makes BFGS or 1000 steps mandatory. In 029 retain the existing `spin = 0` section and
include this exact sentence:

```markdown
BFGS is the pinned reference workflow, not a required optimizer.
```

- [ ] **Step 2: Make runtime instructions POSIX-safe**

In all six instructions replace activation guidance with:

```markdown
Use the bundled interpreter directly at `/app/.venv/bin/python`.
```

- [ ] **Step 3: Disable network access**

In every `task.toml`, set:

```toml
allow_internet = false
```

- [ ] **Step 4: Run the focused contract tests**

```bash
uv run --extra dev pytest tests/test_chemgraph_case_contracts.py::test_public_instructions_expose_the_force_and_runtime_contracts tests/test_chemgraph_case_contracts.py::test_cases_are_offline tests/test_chemgraph_case_contracts.py::test_029_locks_spin_but_not_optimizer_identity -q
```

Expected: these three tests pass; evaluator/XYZ/smoke tests remain red.

- [ ] **Step 5: Commit prompt and isolation fixes**

```bash
git add \
  {025-name2opt-so2,026-name2vib-water,027-name2gibbs-co2,028-name2file-so2,029-react2enthalpy-methane,030-react2gibbs-ammonia}/instruction.md \
  {025-name2opt-so2,026-name2vib-water,027-name2gibbs-co2,028-name2file-so2,029-react2enthalpy-methane,030-react2gibbs-ammonia}/task.toml
git commit -m "fix: align ChemGraph prompts with evaluation contract"
```

---

### Task 3: Harden Method Identity and Force Semantics

**Files:**
- Modify: each case's `tests/test_outputs.py`
- Test: `tests/test_chemgraph_case_contracts.py`
- Test: each case's `tests/test_outputs.py`

**Interfaces:**
- Consumes: canonical method strings documented by the public instructions.
- Produces: `CANONICAL_METHOD: str` and `normalize_method(value) -> str` in every standalone evaluator.

- [ ] **Step 1: Add exact normalized method checks**

In MACE evaluators add:

```python
CANONICAL_METHOD = "mace_mp / medium-mpa-0"


def normalize_method(value):
    return " ".join(str(value).strip().lower().split())
```

In xTB evaluators use:

```python
CANONICAL_METHOD = "GFN2-xTB"
```

Replace substring assertions with:

```python
assert normalize_method(data["method"]) == normalize_method(CANONICAL_METHOD), (
    f"wrong method (L3): {data['method']}"
)
```

- [ ] **Step 2: Match ASE force semantics in MACE evaluators**

In 025, 026, and 028 compute:

```python
forces = np.asarray(atoms.get_forces())
max_force = float(np.linalg.norm(forces, axis=1).max())
```

Use the corresponding variable name in cached results and assertion messages.

- [ ] **Step 3: Run focused host contract tests**

```bash
uv run --extra dev pytest tests/test_chemgraph_case_contracts.py::test_evaluators_require_canonical_method_identity tests/test_chemgraph_case_contracts.py::test_mace_force_checks_use_atom_wise_norms -q
```

Expected: both tests pass.

- [ ] **Step 4: Commit evaluator hardening**

```bash
git add \
  {025-name2opt-so2,026-name2vib-water,027-name2gibbs-co2,028-name2file-so2,029-react2enthalpy-methane,030-react2gibbs-ammonia}/tests/test_outputs.py
git commit -m "fix: enforce canonical methods and ASE force norms"
```

---

### Task 4: Emit Standard XYZ from xTB Oracles

**Files:**
- Modify: `027-name2gibbs-co2/solution/solve.sh`
- Modify: `029-react2enthalpy-methane/solution/solve.sh`
- Modify: `030-react2gibbs-ammonia/solution/solve.sh`
- Test: `tests/test_chemgraph_case_contracts.py`

**Interfaces:**
- Consumes: ASE `write(filename, atoms, format="xyz")`.
- Produces: strict XYZ files with atom records containing symbol and three coordinates.

- [ ] **Step 1: Make each writer explicit**

Replace:

```python
write(out_xyz, atoms)
```

with:

```python
write(out_xyz, atoms, format="xyz")
```

- [ ] **Step 2: Run the XYZ contract test**

```bash
uv run --extra dev pytest tests/test_chemgraph_case_contracts.py::test_xtb_oracles_emit_standard_xyz -q
```

Expected: pass.

- [ ] **Step 3: Commit standard XYZ output**

```bash
git add 027-name2gibbs-co2/solution/solve.sh 029-react2enthalpy-methane/solution/solve.sh 030-react2gibbs-ammonia/solution/solve.sh
git commit -m "fix: emit standard XYZ from xTB oracles"
```

---

### Task 5: Reconcile Pre-Smoke Metadata and Run Host Tests

**Files:**
- Modify: `029-react2enthalpy-methane/VALIDATION.json`
- Modify: `029-react2enthalpy-methane/benchmark_valid.json`
- Test: `tests/test_chemgraph_case_contracts.py`
- Test: `tests/test_workspace_isolation.py`

**Interfaces:**
- Consumes: current evidence that the revised 029 prompt has not yet received a successful agent smoke run.
- Produces: both metadata files temporarily reporting `agent_smoke_tested: false` until Task 7 succeeds.

- [ ] **Step 1: Set both 029 smoke fields to false**

Use the same value in both JSON files:

```json
"agent_smoke_tested": false
```

Keep historical failed-run diagnosis under `fidelity_exception`; do not claim a new pass.

- [ ] **Step 2: Run all host tests**

```bash
uv run --extra dev pytest tests -q
```

Expected: all isolation and contract tests pass.

- [ ] **Step 3: Validate syntax and structured files**

```bash
PYTHONPYCACHEPREFIX=/private/tmp/dftworld-hardening-pycache python3 -m py_compile \
  {025-name2opt-so2,026-name2vib-water,027-name2gibbs-co2,028-name2file-so2,029-react2enthalpy-methane,030-react2gibbs-ammonia}/{reference/generate_reference.py,tests/test_outputs.py}
bash -n {025-name2opt-so2,026-name2vib-water,027-name2gibbs-co2,028-name2file-so2,029-react2enthalpy-methane,030-react2gibbs-ammonia}/{solution/solve.sh,tests/test.sh}
```

Expected: exit code 0 with no output.

- [ ] **Step 4: Commit consistent pre-smoke metadata**

```bash
git add 029-react2enthalpy-methane/VALIDATION.json 029-react2enthalpy-methane/benchmark_valid.json
git commit -m "docs: reconcile 029 pre-smoke validation state"
```

---

### Task 6: Run Six Docker Oracles and Forged-Method Fixtures

**Files:**
- Verify: all six case directories
- No persistent file changes until observed results are recorded in Task 7.

**Interfaces:**
- Consumes: pinned base images, each case Dockerfile, solution, and hidden evaluator.
- Produces: six oracle pass counts and two representative forged-method failures.

- [ ] **Step 1: Verify pinned base image digests**

```bash
docker image inspect dftworld-base-mace dftworld-base-xtb --format '{{.RepoTags}} {{.Id}}'
```

Expected digests:

```text
sha256:c54103db3c6bf58a2427833361910a34715f643fa3f7aa28a081aa5672170c77
sha256:4e1b395ae86f20738d78ccc446146b43dd89d4e0771091998b07859f0a86eb1d
```

- [ ] **Step 2: Build six temporary audit images**

Build tags `dft-audit-025` through `dft-audit-030` from their case directories.
Expected: six successful image IDs.

- [ ] **Step 3: Run every clean oracle and evaluator**

For each case, mount only its `solution/` and `tests/` into the audit container, run
`solution/solve.sh`, then `pytest -q /tests/test_outputs.py`.

Expected pass counts:

```text
025: 9
026: 10
027: 11
028: 10
029: 13
030: 13
```

- [ ] **Step 4: Verify standard XYZ output behaviorally**

For 027, 029, and 030, inspect every delivered XYZ and assert each atom line has exactly
four whitespace-separated fields. Expected: all files satisfy the strict format.

- [ ] **Step 5: Run forged method negative fixtures**

After a clean oracle result, mutate only the method field:

```python
d["method"] = "not-mace / not-medium"
```

for 025 and:

```python
d["method"] = "not-gfn2 / not-xtb"
```

for 027. Run the full evaluator. Expected: both pytest runs fail specifically at L3.

- [ ] **Step 6: Remove temporary audit images**

```bash
docker image rm dft-audit-025 dft-audit-026 dft-audit-027 dft-audit-028 dft-audit-029 dft-audit-030
```

Expected: all six temporary tags are removed; pinned base images remain.

---

### Task 7: Run Authorized 029 Agent Smoke and Record Evidence

**Files:**
- Modify: `029-react2enthalpy-methane/VALIDATION.json`
- Modify: `029-react2enthalpy-methane/benchmark_valid.json`
- Test: `tests/test_chemgraph_case_contracts.py`

**Interfaces:**
- Consumes: revised instruction and the authorized pagent model call.
- Produces: one new job directory and metadata that reports success only when reward is 1.

- [ ] **Step 1: Run the 029 smoke test**

```bash
uv run python eval.py 029-react2enthalpy-methane -v
```

Expected: job completes with `reward: 1.0`, `ok: true`, and no evaluator error.

- [ ] **Step 2: Inspect the job evidence**

Read the new `metainfo.json`, `summary.json`, and message/tool trajectory. Confirm the
agent used `spin=0`, delivered force-converged geometries, and performed genuine TBLite
thermochemistry rather than writing frozen values.

- [ ] **Step 3: Update validation metadata from observed evidence**

Only after reward 1, set both files to:

```json
"agent_smoke_tested": true
```

Add the exact retained job path, model, reward, oracle pass counts, forged-method failure
counts, and current date to `VALIDATION.json.last_regression` and the concise
`benchmark_valid.json.revalidation_note`. If the smoke fails, leave both fields false and
record the actual failure instead of claiming completion.

- [ ] **Step 4: Run metadata and host regression tests**

```bash
uv run --extra dev pytest tests -q
```

Expected: all tests pass, including smoke metadata agreement.

- [ ] **Step 5: Commit verified validation evidence**

```bash
git add 029-react2enthalpy-methane/VALIDATION.json 029-react2enthalpy-methane/benchmark_valid.json
git commit -m "test: record verified 029 agent smoke"
```

---

### Task 8: Final Verification and Handoff

**Files:**
- Verify: all modified files and retained job evidence.

**Interfaces:**
- Consumes: every implementation and verification result from Tasks 1–7.
- Produces: an evidence-backed final status with no unsupported `benchmark_valid` claim.

- [ ] **Step 1: Run final host tests**

```bash
uv run --extra dev pytest tests -q
```

Expected: zero failures.

- [ ] **Step 2: Run structural validation**

Run Python syntax checks, Bash syntax checks, JSON parsing for all `VALIDATION.json`,
`benchmark_valid.json`, and reference JSON files, plus `git diff --check`.
Expected: exit code 0 and no whitespace errors.

- [ ] **Step 3: Review scoped diff and repository status**

```bash
git status --short
git diff --stat HEAD~5..HEAD
```

Confirm no unrelated user files were modified and no temporary audit images or generated
workspace artifacts remain.

- [ ] **Step 4: Report evidence**

Report host test count, six oracle counts, forged method fixture failures, 029 job path and
reward, metadata consistency, and any remaining limitation. Do not state completion if any
required check did not execute or failed.
