# Paper suite template

Copy this directory outside the infra repository, then edit the case instructions, public inputs and private verifier. The included integer-sum example exercises the workflow and is not a scientific benchmark.

```text
paper/
├── suite.toml                    Paper identity and case directory
└── cases/001-case-name/
    ├── case.toml                 Agent, verifier, resource and public-input contract
    ├── task.md                   Public task instruction
    ├── input/                    Public inputs exported to Candidate
    └── verifier/                 Private launcher and validation code
```

Use `bench suite validate --root /path/to/paper`, then `bench run /path/to/paper/cases/001-case-name --config /path/to/config.toml`. Copy `examples/candidate-config.toml` for a user config and keep API credentials in the host environment or local .env.

The template selects the currently registered `claude-mvp` Agent and `matclaw-cips-v1` verifier profile. These resolve shared images from `infra/config/agent-profiles.toml`; change the profile when choosing another qualified runtime. An image tag in a template does not qualify a new host automatically.

Only the explicit Candidate allowlist is public. Private `reference/` and `solution/` can live under `verifier/`; never include them in the allowlist.
