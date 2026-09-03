# CompShare Maintainer GPU Image Specification (P4)

This document specifies the frozen, one-time GPU images prepared for the maintainer hybrid evaluation infrastructure (`maintainer-hybrid-v1`).

Per architecture rules:
- These images are **reusable, immutable assets** maintained by the benchmark team.
- Individual benchmark runs **only reuse** these images by `image_id`; dynamic runtime builds during evaluation runs are strictly forbidden.
- AI2Kit controller SIF is no longer constructed (AI2Kit workflows are driven by provider-neutral bench-hpc scripts).

---

## 1. DeepMD GPU Image (`mlff-deepmd-gpu-v1`)

- **Image ID**: `img-deepmd-gpu-v1`
- **Lock File**: [`reference/runtime/compshare-deepmd-gpu.lock.json`](file:///Users/xjchen/bench/mlffbench/reference/runtime/compshare-deepmd-gpu.lock.json)
- **Base Image**: `compshare/pytorch:2.1.2-cuda12.1-cudnn8-devel-ubuntu22.04`
- **CUDA Version**: 12.1
- **Driver Compatibility**: >= 525.60.13
- **Primary Capabilities**: `deepmd`, `lammps`

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

## 2. JAX / DP-MP GPU Image (`mlff-jax-gpu-v1`)

- **Image ID**: `img-jax-gpu-v1`
- **Lock File**: [`reference/runtime/compshare-jax-gpu.lock.json`](file:///Users/xjchen/bench/mlffbench/reference/runtime/compshare-jax-gpu.lock.json)
- **Base Image**: `compshare/cuda:12.2-devel-ubuntu22.04`
- **CUDA Version**: 12.2
- **Driver Compatibility**: >= 525.60.13
- **Primary Capabilities**: `jax`, `deepmd-jax`

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

## 3. Maintainer CLI Preparation Workflow

To build and register a new version:
1. Start an interactive GPU instance from the base image:
   ```bash
   compshare instance create --name image-builder --image compshare/cuda:12.2-devel-ubuntu22.04 --gpu rtx4090 --auto-shutdown 60
   ```
2. Install required packages in the instance.
3. Run smoke verification scripts.
4. Save the instance disk as a new custom image:
   ```bash
   compshare image create --instance <instance_id> --name mlff-jax-gpu-v1 --description "MLFFBench JAX GPU v1"
   ```
5. Retrieve the assigned `image_id` and update the lock file in `reference/runtime/`.
6. Terminate the builder instance:
   ```bash
   compshare instance delete <instance_id> --force
   ```

