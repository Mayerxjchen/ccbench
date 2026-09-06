# Packmol troubleshooting reference

Diagnose from the exit code, complete log, input, component files, and output
state. Preserve failed artifacts before changing anything.

## Preflight failures

- `packmol: command not found`: verify `command -v packmol`, runtime/container,
  PATH, and image version. Do not claim Packmol ran.
- A nonzero or timed-out `packmol --version` probe does not prove the executable
  is unusable: some releases do not support that flag. Preserve the probe
  status and use the `Packmol Version ...` banner from the actual execution log
  as the authoritative version when present.
- Input XYZ error: recheck first-line atom count, header, coordinate-row count,
  elements, finite coordinates, and readability.
- Unknown keyword/syntax: record Packmol version and compare the input with the
  official syntax for that installed version.

## Packing failures

Messages such as `Could not find a suitable position` usually mean the
requested packing is too constrained. Check, in this order:

1. box dimensions and units;
2. component counts and template geometry;
3. whether density/composition is physically plausible;
4. consistency of PBC and `inside box` bounds;
5. tolerance versus available volume;
6. random seed sensitivity.

Do not silently alter scientific composition or target density. If permitted,
retry one controlled change at a time: a different seed, slightly larger box,
or a documented tolerance adjustment. Report the changed value and retain the
original attempt.

## Timeout or apparent hang

Inspect whether the process is still consuming CPU and whether the log grows.
Dense systems can converge slowly. Use a bounded timeout, retain partial logs,
and avoid blind repeated submission. Recheck feasibility before increasing
runtime.

## Output/QC failures

- Success text but missing/empty XYZ: task failed; verify output path and
  permissions.
- Atom-count mismatch: task failed; compare declared component order/counts
  with the output header.
- Very short minimum-image distance: confirm molecule grouping first, then
  regenerate or relax constraints; never hide the failed contact.
- Coordinates outside `[0,L)`: first determine whether an intact molecule
  crosses PBC. This alone is not a packing failure and must not be fixed by
  independently wrapping atoms.

After recovery, rerun all execution checks and QC. Mark the run as recovered
only when the final output independently passes them; earlier failed commands
remain part of the event history.
