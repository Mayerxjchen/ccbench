# Probe: earlier errors do not hide later integrity findings (v3)

`artifacts/dataset/train.raw` is declared but missing, and
`artifacts/labels/train.lbl` is present with a wrong hash. Expected
classification: `AGENT_FAILURE / SCIENTIFIC_FAIL` naming BOTH the missing
artifact (`declared artifact missing`) and the mismatch
(`integrity mismatch for artifacts/labels/train.lbl`). A `not errors`
guard around hash checks would report only the first.
