# VASP on GPUs (OpenACC builds)

> Load this when: running VASP on GPU nodes — choosing between CPU and GPU builds, writing the launch script, setting ranks/threads per GPU, or adapting INCAR parallelization for OpenACC VASP. For generic Slurm GPU discovery and request syntax, also load the `hpc-submit` skill's `references/running.md`.

## Build choice is a method decision

Do not submit a CPU-only VASP MPI build to a GPU partition. For GPU nodes, use the site's OpenACC/GPU VASP module and launcher (record them in the private cluster guide, under its VASP/GPU section). If strict method reproduction requires a specific CPU VASP version that is unavailable as a GPU build, treat the CPU-vs-GPU choice as a method/runtime decision and ask before changing versions.

## Binary choice

- `vasp_std` for general k-meshes.
- `vasp_gam` for true Gamma-only jobs (molecules in boxes, large supercells with `1 1 1` Gamma KPOINTS) — often faster.
- `vasp_ncl` for SOC/noncollinear, if the GPU build provides it.

## Launch shape — one MPI rank per GPU

Scheduler headers, GPU binding, and modules are the gateway's job: submit a
digest-pinned job descriptor through the `hpc-submit` skill's bench-hpc
lifecycle with `gpus` set in the typed resources. Your command is pure argv —
the launch line below is what goes into `command`, sized by the resource class
you requested:

```bash
export OMP_NUM_THREADS=<cores-per-gpu>   # match your request's cpu ceiling
export OMP_PLACES=cores
export OMP_PROC_BIND=close
export OMP_STACKSIZE=512m
export OMP_WAIT_POLICY=PASSIVE

# Balance ranks across CPU sockets. This assumes 2 sockets per node
# (1 GPU -> 1/socket, 4 -> 2/socket, 8 -> 4/socket).
RPS=$(( (NGPU + 1) / 2 ))
BIN=vasp_std                             # vasp_gam for Gamma-only; vasp_ncl for SOC if supported

mpirun -np "$NGPU" --map-by ppr:${RPS}:socket:PE=${OMP_NUM_THREADS} --bind-to core \
  "$BIN"
```

Keep `NGPU` equal to the `gpus` count in your descriptor. If a run is rejected at
the gateway, read the error; do not try to work around resource ceilings.

## Practical rules

- Keep MPI ranks equal to GPU count. Do not run tens of CPU-style MPI ranks on a GPU VASP job.
- Do not carry CPU INCAR parallel settings over. `NCORE`, `KPAR`, and `NPAR` choices
  that helped CPU jobs can hurt or fail GPU jobs. Local default is to omit `NPAR` and
  `NCORE` for GPU/OpenACC inputs unless the GPU module documentation explicitly
  recommends them; then benchmark.
- NEB on GPUs works best with one image per GPU; keep total GPUs/ranks divisible by `IMAGES`.
- Check early stdout/stderr for CUDA/OpenACC/MPI binding errors before letting a long job run.
- Record GPU partition, GPU count/type, module, binary, and the exact launch command with each run.
