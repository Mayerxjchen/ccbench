# Develop a reliable machine-learning potential for liquid water

Construct a periodic cubic liquid-water system containing 64 H2O molecules in a
12.4 Å box. Generate every atomic coordinate yourself and retain reproducible
structure-generation provenance. Develop and execute an end-to-end DeePMD
potential workflow using CP2K labels and material ai2-kit participation.

A remote HPC capability exists in the sandbox. Discover and use the available
infrastructure yourself. No initial structure or cluster recipe is supplied.

Independently design, implement, and execute the full development cycle:

- prepare the generated structure so it is physically suitable for
  first-principles molecular dynamics;
- generate an ab initio molecular-dynamics reference trajectory with CP2K before
  training;
- train a DeePMD potential;
- iteratively improve the model by generating and labeling additional
  configurations selected using the current potential;
- assess energy/force accuracy and physical behavior under simulation.

The scientific stages above are fixed, but all methodological and infrastructure
decisions are yours. Do not merely prepare inputs: execute the workflow and obtain
a trained potential. Long-running scientific work must use the remote scheduler;
the sandbox is the control layer.

At completion, retain under `/app`:

- the complete reproducible workflow and structure-generation record;
- the first-principles data generated during the project;
- provenance for every training datum and remote job;
- the final trained potential model(s);
- evidence supporting accuracy and physical reliability;
- a concise scientific report describing methodology, validation, limitations,
  and conclusions.
