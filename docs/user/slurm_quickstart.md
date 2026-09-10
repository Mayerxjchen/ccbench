# Slurm and CompShare configuration

An external paper uses the same Candidate/Verifier workflow whether scientific computation runs locally, on IKKEM CPU, on CompShare GPU or on another Slurm cluster.

The host requires Python 3.12+ and the infra installation. Slurm execution requires operator-side SSH access, scheduler commands and a qualified software environment; Apptainer/SIF may supply that environment. GPU runtime, driver and device compatibility must be checked at the target site.

Copy and customize the appropriate template outside the public case bundle:

- [Generic Slurm site](../../examples/hpc/generic-slurm-site-profile.json): login, account, partitions, resource ceilings and runtime policy.
- [IKKEM CPU site](../../examples/hpc/ikkem-cpu-site-profile.json): current CPU operator example.
- [CompShare GPU site](../../examples/hpc/compshare-gpu-site-profile.json): current cloud GPU operator example.
- [Generic compute profile](../../examples/hpc/generic-slurm-compute-profile.json): routes CPU/GPU requests to a site.

Select the backend and operator profile in the Candidate config's `[compute]` table. This selection records the intended route; site credentials stay operator-side. A template is not an already provisioned cluster or a qualification receipt.

Candidate writes a request using `bench-compute-request`. The trusted operator validates and executes it, retrieves outputs and imports them into the run. Formal imports require matching signed receipts. [Run instructions](../mvp-test-infra.md) show import and resume.

When moving GPU work to your own Slurm cluster, change the site/runtime configuration and qualify it. Existing V100 hardware needs software built for that device's compute capability; a successful CompShare GPU probe cannot qualify a different GPU architecture.
