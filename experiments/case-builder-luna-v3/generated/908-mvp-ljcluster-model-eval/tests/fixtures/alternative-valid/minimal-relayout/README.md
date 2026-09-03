# Alternative-valid fixture: minimal-relayout

Same submission contract as `positive/structural-minimal`, reached a
different way for this model_evaluation case: one evaluation round instead of
two training rounds, different artifact filenames (`model.txt`,
`eval-table.csv`), no labels artifact, and a `metrics` field present (empty).
The verifier must accept this layout: scoring is outcome-based and never
requires the expert's exact paths.
