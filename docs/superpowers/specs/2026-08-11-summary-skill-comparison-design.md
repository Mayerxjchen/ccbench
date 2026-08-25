# dftworld Skill Comparison Summary Design

## Goal

Make skill-enabled and no-skill benchmark results easy to inspect and compare
without modifying immutable per-run records.

## Data Boundary

`jobs/<run-id>/summary.json` remains the immutable source of truth. The
generator writes two derived, replaceable views:

- `jobs/summary.json` for structured consumers;
- `jobs/SUMMARY.md` for human review.

Both derived files may be regenerated at any time and are ignored by Git with
the rest of `jobs/`.

## Structured View

`jobs/summary.json` contains:

- `generated_at`: UTC generation timestamp;
- `source`: the immutable per-run path contract;
- `with_skill.completed_cases`: latest explicit PASS per case whose condition
  is `with-skill`, including actual invoked skills;
- `no_skill.completed_cases`: latest explicit PASS per case whose condition is
  `no-skill`;
- `comparison`: one row per case observed in either condition, with each side's
  latest PASS and signed with-skill-minus-no-skill deltas for calls, tokens,
  and elapsed seconds when both sides exist;
- `matclaw`: explicit 031–033 construction status, separate from Agent results;
- `history.with_skill` and `history.no_skill`: every execution in descending
  time order.

Missing sides and unavailable deltas are encoded as JSON `null`, not zero.

## Markdown View

`jobs/SUMMARY.md` is ordered as follows:

1. overview;
2. With Skill completed cases;
3. No Skill completed cases;
4. direct comparison;
5. MatClaw 031–033 construction status;
6. With Skill history;
7. No Skill history;
8. metric definitions.

The With Skill table retains actual skill names and displays `未调用` when
skills were available but unused. The No Skill table displays `无 Skill 环境`.

## Integrity Rules

- A completed row requires an explicit PASS in a per-run summary.
- Each condition independently selects its latest PASS; a later failure is
  flagged but does not erase historical completion.
- Comparison rows are numerically sorted by case ID and unique.
- No derived view may be loaded as an input run.
- Regeneration must leave every `jobs/<run-id>/summary.json` byte-for-byte
  unchanged.
