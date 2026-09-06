# Packmol official-output reference

Use the installed Packmol version and the official user guide as the source of
truth. Record the executable path and version before interpreting output.

An auditable success requires all of these independent facts:

- process exit code is zero;
- the complete log contains `Success!` and no fatal marker;
- maximum target-distance violation is present and no greater than `0.01`;
- maximum constraint violation is present and no greater than `0.01`;
- the declared output exists, is non-empty, and has the expected composition.

Packmol's commonly used `tolerance 2.0` is guidance for many room-temperature
all-atom systems, not a universal default. Preserve the user's requested value.
Packmol generates initial coordinates; its success does not prove force-field,
topology, energy, or production-simulation readiness.

Official user guide: <https://m3g.github.io/packmol/userguide.shtml>

Retrieved: 2026-07-14.
