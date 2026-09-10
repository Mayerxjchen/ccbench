# Quickstart: Running Bench with Your Slurm Cluster

This guide explains how external users run Bench using their own university or institutional Slurm HPC cluster.

> [!NOTE]
> External users do **not** need CompShare or any cloud GPU account. Bench evaluates agents using standard, provider-neutral protocols (`bench-hpc`).

---

## 1. Prerequisites

On your local machine or submission workstation:
- Python 3.10+
- SSH access to your Slurm cluster login node with key-based authentication (`ssh <user>@<hpc_login>`)

On your remote Slurm cluster:
- Slurm workload manager (`sbatch`, `squeue`, `scancel`)
- Apptainer / Singularity installed and available on compute nodes
- SIF container image(s) for the required benchmark runtimes (e.g. CP2K, DeePMD)

---

## 2. Configuration Workflow

### Step 1: Create a SiteProfile
Copy the template from [`examples/hpc/generic-slurm-site-profile.json`](../../examples/hpc/generic-slurm-site-profile.json) to `config/hpc-site-profile.json`:

```json
{
  "schema_version": 1,
  "site_id": "my-university-hpc",
  "scheduler": "slurm",
  "connection": {
    "credential_profile_id": "my-ssh-key",
    "target_binding": "login.hpc.university.edu:22",
    "remote_user": "my_username",
    "remote_root_policy": "/scratch/users/{remote_user}/bench/{run_id}"
  },
  "account": "my_account_group",
  "queues": {
    "cpu": {
      "partition": "cpu-standard",
      "qos": "normal",
      "max_cpus": 64,
      "max_memory_gb": 256,
      "max_gpus": 0,
      "max_walltime_minutes": 240
    },
    "gpu": {
      "partition": "gpu-a100",
      "qos": "normal",
      "max_cpus": 32,
      "max_memory_gb": 128,
      "max_gpus": 4,
      "max_walltime_minutes": 120
    }
  },
  "runtime_policy": {
    "requires_apptainer": true,
    "apptainer_bin": "apptainer",
    "runtime_store": "/shared/containers/mlff",
    "gres_template": "--gres=gpu:{gpus}"
  }
}
```

### Step 2: Create a ComputeProfile
Copy [`examples/hpc/generic-slurm-compute-profile.json`](../../examples/hpc/generic-slurm-compute-profile.json) to `config/compute-profile.json`:

```json
{
  "schema_version": 1,
  "profile_id": "my-university-hpc",
  "routes": {
    "cpu": { "site_profile": "my-university-hpc" },
    "gpu": { "site_profile": "my-university-hpc" }
  }
}
```

### Step 3: Run Qualification
Before evaluating candidate agents, verify your Slurm site setup:
```bash
# Verify site configuration and cluster connectivity
bench site qualify --profile ~/cluster_profile.toml

# Qualify the compute profile (Layer 2 verification)
bench compute qualify --profile ~/compute_profile.json
```
When qualification passes, a signed qualification receipt is generated.
