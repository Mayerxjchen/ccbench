# Develop a reliable machine-learning potential for liquid water

Construct a periodic cubic liquid-water system containing 64 H2O molecules in a
12.4 Å box. Generate every atomic coordinate yourself and retain reproducible
structure-generation provenance. Develop and execute an end-to-end DeePMD
potential workflow using CP2K labels and material ai2-kit participation.

External CPU/GPU compute may be required. No cluster recipe or provider
details are supplied to the Candidate; write only an abstract request draft
under `compute-requests/` when local preflight is insufficient.

Independently design, implement, and execute the full development cycle:

- prepare the generated structure so it is physically suitable for
  first-principles molecular dynamics;
- generate an ab initio molecular-dynamics reference trajectory with CP2K before
  training;
- train a DeePMD potential;
- iteratively improve the model by generating and labeling additional
  configurations selected using the current potential;
- assess energy/force accuracy and physical behavior under simulation.

The scientific stages above are fixed, but all methodological and execution
decisions are yours. Do not merely prepare inputs: execute the workflow and obtain
a trained potential. Long-running scientific work may use an external Operator;
the sandbox is the control layer.

At completion, retain under `final/`:

- the complete reproducible workflow and structure-generation record;
- the first-principles data generated during the project;
- provenance for every training datum and external compute request;
- the final trained potential model(s);
- evidence supporting accuracy and physical reliability;
- a concise scientific report describing methodology, validation, limitations,
  and conclusions.
