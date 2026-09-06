# alternative-valid fixture

An outcome-based alternative pipeline that still passes all gates but differs
scientifically from the expert path (e.g. different active-learning explorer,
different labeling schedule, different interface subset) must yield reward 1.
The verifier only asserts structural/scientific OUTCOMES (V0-V6), never a
specific round count, committee size, or directory naming, so an alternative
valid submission passes by construction. Formal-phase: needs a real alternative
run + container.
