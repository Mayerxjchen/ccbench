# CompShare Maintainer GPU Image Specification (offline scope freeze)

This document specifies the offline scope for the one-time GPU images planned
for the maintainer evaluation infrastructure. It does not assert that a cloud
image has been built or qualified.

Per architecture rules:
- These images are **reusable, immutable assets** maintained by the benchmark team.
- Individual benchmark runs **only reuse** these images by `image_id`; dynamic runtime builds during evaluation runs are strictly forbidden.
- AI2Kit controller SIF is no longer constructed (AI2Kit workflows are driven by provider-neutral bench-hpc scripts).

---

## 1. Image A: MatClaw/DeepMD GPU image (`runtime.matclaw-gpu`)

- **Image ID**: not assigned (Gate B has not run)
- **Lock File**: [`reference/runtime/deepmd-runtime.lock.json`](file:///Users/xjchen/bench/mlffbench/reference/runtime/deepmd-runtime.lock.json)
- **Base Image**: `compshare/pytorch:2.1.2-cuda12.1-cudnn8-devel-ubuntu22.04`
- **CUDA Version**: 12.1
- **Driver Compatibility**: >= 525.60.13
- **Primary Capability**: `runtime.matclaw-gpu`
- **Qualification scope**: cases **031, 032, and 033 only**
- **Explicit exclusions**: case 034 and case 042

### Installed Packages & Versions
| Software | Version | Purpose |
|---|---|---|
| Python | 3.10.12 | Base runtime |
| DeepMD-kit | 2.2.11 | DeePMD potential training and inference |
| TensorFlow | 2.16.2 | DeePMD backend |
| LAMMPS | 2023.8 (stable) | MD engine with DeePMD plugin |
| PyTorch | 2.1.2 | Deep learning utilities |
| NumPy | 1.26.4 | Numerical computations |

### Smoke Test Verification Command
```bash
python -c "import deepmd; print('DeepMD version:', deepmd.__version__); import tensorflow as tf; print('GPUs available:', len(tf.config.list_physical_devices('GPU')))"
```

---

## 2. Deferred image: JAX / DP-MP (`runtime.jax`)

- **Image ID**: not assigned (deferred until a separate qualification)
- **Lock File**: [`reference/runtime/jax-runtime.lock.json`](file:///Users/xjchen/bench/mlffbench/reference/runtime/jax-runtime.lock.json)
- **Base Image**: `compshare/cuda:12.2-devel-ubuntu22.04`
- **CUDA Version**: 12.2
- **Driver Compatibility**: >= 525.60.13
- **Primary Capability**: `runtime.jax`
- **Qualification scope**: case 042 only, in a later phase
- **Status**: explicitly out of the Image A scope

### Installed Packages & Versions
| Software | Version | Purpose |
|---|---|---|
| Python | 3.11.8 | Base runtime |
| JAX | 0.5.3 (cuda12) | High-performance autodiff / accelerator framework |
| JAX-MD | 0.2.8 | Differentiable molecular dynamics |
| Flax | 0.10.6 | Neural network models |
| Optax | 0.2.4 | Gradient optimization |
| DPMP | 0.2.0 | Deep potential message-passing model |

### Default Environment Variables
- `JAX_ENABLE_X64`: `"1"` (strict requirement for Case 042 double-precision simulations)
- `CUDA_VISIBLE_DEVICES`: `"0"`

### Smoke Test Verification Command
```bash
python -c "import jax; print('JAX version:', jax.__version__, 'Devices:', jax.devices())"
```

---

## 3. Gate B/C workflow (when separately authorized)

To build and register a new version:
1. Start an interactive GPU instance from the base image only after the
   offline gates and an explicit operator authorization:
   ```bash
   compshare instance create --name image-builder --image compshare/cuda:12.2-devel-ubuntu22.04 --gpu rtx4090 --auto-shutdown 60
   ```
2. Install required packages in the instance.
3. Run smoke verification scripts.
4. Save the instance disk as a new custom image:
   ```bash
   compshare image create --instance <instance_id> --name mlff-jax-gpu-v1 --description "MLFFBench JAX GPU v1"
   ```
5. Retrieve the assigned `image_id` and update the construction evidence and
   lock only through the reviewed maintainer workflow. A provider image ID is
   not a qualification receipt.
6. Terminate the builder instance:
   ```bash
   compshare instance delete <instance_id> --force
   ```

---

## 4. Failover and RunLock policy

The standby region/zone is not an execution fallback. An operator must settle
the old run, choose an already-qualified standby profile, and start a new run
with a new RunLock. The active RunLock never changes image, region, zone, GPU
type, or site profile while a run is in progress. Automatic drift or
cross-region retry is prohibited.

Case 034 remains excluded from Image A until its operation-level GPU route is
implemented and separately qualified. Case 042 remains on the deferred JAX
track.

## 5. Maintainer Credential & Profile Configuration

CompShare CLI configuration is stored on the trusted host at `~/.config/compshare/config.json` (not in `~/.compshare/`).
The official CLI configuration entry point is `compshare config`:

```bash
compshare config --name default
```
Run interactively to enter `PublicKey`, `PrivateKey`, and endpoint without exposing sensitive credentials in shell history or process tables.

Alternatively, configure the maintainer environment using environment variables:
```bash
export COMPSHARE_PUBLIC_KEY="<YOUR_PUBLIC_KEY>"
export COMPSHARE_PRIVATE_KEY="<YOUR_PRIVATE_KEY>"
```

Verification:
```bash
compshare --json doctor
```

Key security principles:
- Official configuration is persistent in `~/.config/compshare/config.json`.
- CompShare uses `PublicKey` and `PrivateKey` authentication (not a single `--api-key`). Never pass private keys via CLI flags (`--api-key` / `--private-key`) to prevent leakage into shell history and `/proc`.
- Candidates and container sandboxes **never** receive access to `~/.config/compshare/config.json`, the `compshare` CLI, or any cloud keys.
