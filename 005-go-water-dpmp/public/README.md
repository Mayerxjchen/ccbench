# Case 042 — GO–water DPMP (public inputs)

Agent-visible inputs for developing a DPMP interatomic potential for
graphene-oxide/water interfaces **from scratch**.

| Path | Contents |
|------|----------|
| `system.json` | Target system definitions: five interfaces (air–water, graphene–water, GO–O12/O25/O50) with composition, cell size, oxidation-level definition, and construction specification |
| `dpmp-config/train.py` | Deepmd-jax DPMP training configuration (the model architecture and hyperparameters) |

No initial structures, labeled data, or reference models are supplied. The agent
must build all five supercells from the composition/cell specs, generate
first-principles labels, train the potential, and validate it.
