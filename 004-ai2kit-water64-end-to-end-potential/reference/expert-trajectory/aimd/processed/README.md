# aimd/processed/ — the hidden scientific reference set

`aimd.xyz` is the **190-frame labeled extxyz mother set** produced by
`tools/cp2k2extxyz.py` from the raw CP2K AIMD outputs in `../raw/`.

Conversion (expert command, see `reproduction-notes.md` §1.3):

```bash
python tools/cp2k2extxyz.py \
  --dir ../raw \
  --prefix water64_aimd \
  --min-temp 250 --max-temp 350 \
  --output aimd.xyz \
  --report filter.tsv
```

`filter.tsv` records, per raw step: `step`, `time_fs`, `temperature_K`,
`selected` (0/1), `reason` (`initial_step`, `temp_lt_min`, `temp_gt_max`,
`duplicate`, `kept`). Result: 214 raw frames → 190 selected.

**Hidden-validation role**: this exact set (as `dft-validation.extxyz`) is the
verifier's hidden E/F (L7) reference and the source of the RDF reference (L9).
The agent generates its own AIMD from the unrelaxed PACKMOL structure and never
sees these frames, so E/F on them is a fair out-of-training test.
