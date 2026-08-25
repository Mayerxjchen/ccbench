# MatClaw 031–033 GPU execution contract

The formal runs for cases 031–033 execute on a **paper-profile GPU image**
(`dftworld-base-matclaw-cips:2.2.11-gpu`) with an **explicit** GPU allocation.
"GPU image" is not enough — the runtime identity, the device, and a
CPU/GPU-parity gate are all part of the evidence.

## 1. The two images and their identity

| tag | content | gate |
|-----|---------|------|
| `dftworld-base-matclaw-cips:2.2.11-cpu` | deepmd 2.2.11 + CPU tensorflow 2.16.2 + LAMMPS | build-time `smoke_test.py` |
| `dftworld-base-matclaw-cips:2.2.11-gpu` | same venv, `tensorflow[and-cuda]==2.16.2` + nvidia pip CUDA/cuDNN, plus `qualify_gpu.py` | build-time smoke (device-independent) + runtime parity probe |

The GPU image is built only by `scripts/run_gpu_paper.sh --build` (or
`docker build -f base-env-build/matclaw-cips-gpu/Dockerfile .`). The CPU tag is
**never re-tagged by the GPU path**; `run_gpu_paper.sh` contains no `docker tag`,
no `PINNED_TAG`, no `--force-retag`, and no `-t ...:cpu`.

Formal execution resolves the image by immutable digest:

```bash
gpu_digest="$(docker image inspect --format '{{index .RepoDigests 0}}' \
  dftworld-base-matclaw-cips:2.2.11-gpu)"
```

The runner fails unless the digest contains `@sha256:`.

## 2. CPU/GPU parity qualification

`/opt/matclaw/qualify_gpu.py` evaluates the locked structure (`CuInP2S6.cif`)
and teacher model (`frozen_model.pb`) once with CUDA disabled and once with
CUDA visible (in separate child processes, because TensorFlow reads
`CUDA_VISIBLE_DEVICES` at CUDA initialization), then runs a short MD. It emits
a JSON report and exits nonzero unless:

- a physical GPU is visible to TensorFlow and actually used by the GPU pass
  (a host without a GPU **fails closed**, never silently runs on CPU);
- `energy_abs_diff_eV < 1e-6` and `max_force_component_abs_diff_eV_A < 1e-6`;
- every MD energy is finite and the trajectory has exactly `steps + 1` frames.

Report keys: `gpu_visible`, `gpu_name`, `energy_abs_diff_eV`,
`max_force_component_abs_diff_eV_A`, `md_steps`, `md_finite`, `frames_ok`,
`elapsed_s`, `seconds_per_step`, `versions` (python / tensorflow / deepmd /
ase), and both `passes` records.

## 3. Orchestration and timeout policy

`scripts/run_gpu_paper.sh`:

1. builds `:2.2.11-gpu` only when `--build` is passed;
2. resolves the `@sha256:` digest (fails if absent);
3. runs `qualify_gpu.py` inside the container on `device=$gpu_device` and
   captures the JSON report to `qualify_gpu.json`;
4. persists a timeout estimate `gpu_timeout_estimate.json` with
   `timeout_seconds = ceil(1.5 × measured_seconds)` from the probe;
5. calls `scripts/run_matclaw_reference.sh` with the digest and `--gpus 0`.

The reference runner now honors `--gpus` for **any** evidence class when a
device is named, so a diagnostic GPU run is never silently demoted to CPU.

## 4. Resource metadata

Each case's `task.toml` declares `[environment] gpus = 1`, and both
`[verifier.env]` / `[solution.env]` pin `MATCLAW_PROFILE = "paper"`. The
`[verifier] timeout_sec` / `[agent] timeout_sec` values are floors derived from
measured stage durations (at least 1.5× the committed measured values); after
each formal-host qualification probe, the measured seconds and the chosen
ceil(1.5×) value are recorded in `gpu_timeout_estimate.json` and mirrored here,
**not** in an unverified estimate.

| case | measured stage (s) | agent timeout_sec | verifier timeout_sec | probe evidence |
|------|--------------------|-------------------|----------------------|----------------|
| 031 | (probe on formal host) | 86400 | 7200 | `gpu_timeout_estimate.json` |
| 032 | (probe on formal host) | 86400 | 7200 | `gpu_timeout_estimate.json` |
| 033 | (probe on formal host) | 86400 | 7200 | `gpu_timeout_estimate.json` |

`build_timeout_sec` stays ≥ 1800.

## 5. Formal gate

A formal run additionally requires a clean worktree, `profile=paper`, the
`@sha256:` image, an empty output workspace, and `--gpus DEVICE`
(`run_matclaw_reference.sh --evidence-class formal`). Any failed parity probe
blocks the formal passes (Tasks 11–12). On hosts without an NVIDIA driver /
Docker GPU runtime, `docker run --gpus` fails and the qualification probe fails
closed — that is the intended behavior, not a fallback to CPU.
