# HPC operator contract

Candidate uses the supplied `bench-compute-request` skill to write a structured compute request. It has no SSH key, SSH agent socket, raw scheduler credentials or cloud-management token. The trusted operator chooses Local, Slurm or CompShare execution and owns job submission and cleanup.

```python
job_states = {"QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "TIMEOUT", "LOST"}
```

Requests declare CPU/GPU class, runtime capability, argv, inputs, outputs and resources. Schema checks and path confinement run before operator execution. Resources and walltime are bounded by the selected site profile. Candidate cannot provide arbitrary host paths or scheduler directives.

The Candidate phase ends with `COMPUTE_REQUIRED`. The operator executes the request, retrieves outputs, and imports them into the existing run. Formal import requires the matching signed operator receipt and bound artifacts. Resume checks the saved request, public bundle, outputs, configuration and cumulative budget before continuing the original session.

Remote waiting is excluded from Candidate phase walltime. Scheduler walltime and cloud billing are separate limits. A scheduler COMPLETED state or a version probe is not proof of scientific correctness.

The `bench.hpc` drivers and their job contracts are operator implementation details. They do not add a direct scheduler surface to the request-only Candidate.
