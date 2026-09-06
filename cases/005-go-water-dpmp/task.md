# Develop a machine-learning potential for graphene-oxide/water interfaces

Construct five graphene-oxide/water interface systems and develop a Deep
Potential Message Passing (DPMP) interatomic potential using deepmd-jax.
Generate every atomic coordinate yourself and retain reproducible
structure-generation provenance.

A remote HPC capability exists in the sandbox. Discover and use the available
infrastructure yourself. No initial structures or training data are supplied.

The target systems are exactly the five described in `system.json` (no other
systems will be scored). Each interface is a WATER SLAB sandwiched between TWO
parallel basal planes (vacuum at both z edges); air–water is the same slab
with no planes:

| system | C total | H₂O | hydroxyl | epoxide | total atoms | cell (Å, x×y×z) |
|---|---|---|---|---|---|---|
| air–water | 0 | 752 | 0 | 0 | 2256 | 29.520 × 25.566 × 70.0 |
| graphene–water | 576 | 752 | 0 | 0 | 2832 | 29.520 × 25.566 × 70.0 |
| graphene–O12 | 576 | 752 | 32 | 40 | 2936 | 29.712 × 25.756 × 70.0 |
| graphene–O25 | 576 | 752 | 72 | 72 | 3048 | 29.794 × 26.014 × 70.0 |
| graphene–O50 | 576 | 752 | 144 | 144 | 3264 | 29.998 × 25.850 × 70.0 |

These composition counts and orthorhombic cells are binding. Construction
facts:

- Each basal plane is pristine-graphene honeycomb (a=2.46 Å, C–C 1.42 Å)
  carrying exactly 288 C atoms; the two planes per interface are identical
  and parallel. This is NOT a stacked graphite multilayer.
- Oxidation is basal-plane functionalization only: hydroxyl (-OH) and
  epoxide (bridging O) groups at exactly the counts listed per oxidation
  level; WHERE they sit on the plane is your construction choice.
- Water (~1.0 g/cm³) occupies the region between the two planes; vacuum
  separates periodic images along z.
- Relationship to the labeling unit: SI §1.1 constructs each GO/water cell at
  C144 (single-sided water film); the C576 sandwich above is that unit
  assembled four-fold into the symmetric dual-plane MLMD cell. You may build
  and label at either scale, provided every target system is covered and the
  assembled interfaces match the table.
- **Labeling coverage**: your AIMD reference data must cover EVERY target
  system — a model trained on fewer than all five chemical environments
  cannot pass hidden validation.

Element totals follow automatically from the table (e.g. graphene–O12:
C=576, O = 752+32+40 = 824, H = 2×752+32 = 1536).

Write each interface as DeepMD raw format (box.raw, coord.raw, type.raw) using
the element type map in `system.json`.

Independently design, implement, and execute the full development cycle:

1. **Structure generation** — build all five interface supercells from the
   composition/cell specs; generate a physically reasonable initial configuration
   for each (relax, equilibrate);
2. **Reference labeling** — generate first-principles (CP2K, revPBE-D3) AIMD
   trajectories and labeled energies/forces for every target system; all five
   systems must contribute configurations to the final reference corpus;
3. **Initial training** — train a DPMP potential (deepmd-jax) using the recipe
   in `dpmp-config/train.py`, adapted to your generated data;
4. **Iterative improvement** — actively learn: explore new configurations with
   your current potential, screen them, label selected configurations with CP2K,
   and retrain. Repeat until convergence;
5. **Validation** — assess energy/force accuracy on held-out data and physical
   behavior under short MD (structures must remain physical at 300 K; reproduce
   a physically sensible interfacial water density profile for each interface).

The scientific stages above are fixed, but all methodological and infrastructure
decisions are yours. Do not merely prepare inputs: execute the workflow and
obtain a trained potential. Long-running scientific work must use the remote
scheduler; the sandbox is the control layer.

At completion, retain under `/app`:

- the complete reproducible workflow and structure-generation record;
- the first-principles data generated during the project;
- provenance for every training datum and remote job;
- the final trained potential model(s);
- evidence supporting accuracy and physical reliability;
- a concise scientific report describing methodology, validation, limitations,
  and conclusions.
