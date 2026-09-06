# B-gate isolation evidence (B2, B3)

**When / how**: 2026-08-10, against the final base image
`dftworld-base-ai2kit:0.1.0-cpu` (8a840aa2e477).

## B2 no-skill/old-workflow image leak
In-container grep over `/opt /usr/local /etc /root` for old-water64-workflow
and skill markers, excluding legit runtime content (deepmd libs, tensorflow,
`dist-info/METADATA` package descriptions, cp2k bundled `data/`):

```
pattern=tesla-h2o hits=0
pattern=build-tesla hits=0
pattern=iter-classic-dp-lammps-cp2k hits=0
pattern=compress.pb hits=0
pattern=water64.xyz hits=0
pattern=MODEL_DEVI_COND hits=0
PATTERNS_WITH_HITS=0
B2_CLEAN
```

Notes: the two naive `build-tesla` string hits are the ai2-kit package's own
PyPI `METADATA` (its upstream skill-index description text), not a skill
checkout; `find / -type d -name skills` and `-name build-tesla` return
nothing. No example/git checkout of the prior workflow exists in the image.

## B3 docker copy isolation
```
absent:  /reference
absent:  /solution
absent:  /tests
absent:  /tools
absent:  /base-env-build
PRESENT: /opt/ai2kit/smoke_test.py   # build-time smoke gate, a runtime artifact
```
The case Dockerfile is `FROM dftworld-base-ai2kit:0.1.0-cpu` + `COPY public/ /app/`
only; reference/solution/tests/tools never enter the agent image.

## B3 (agent image, dftworld-034-agent:0.1.0)
Case Dockerfile is `FROM dftworld-base-ai2kit:0.1.0-cpu` + `COPY public/ /app/`.
`docker run --rm dftworld-034-agent:0.1.0` -> /app contains exactly
HPC_ENVIRONMENT.md, system.json, water64.xyz; /reference /solution /tests
/tools all ABSENT. Only the 3 public inputs reach the agent.
