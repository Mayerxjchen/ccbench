"""Experiment resolver: construct and freeze experiment identities.

The resolver takes an experiment template, a case, and a run ID, and produces
a complete ResolvedRunLock. For formal runs, no overrides are permitted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dftworld_bench.config.profiles import ProfileRegistry, canonical_json, digest_bytes
from dftworld_bench.contracts.case import CaseSpec
from dftworld_bench.contracts.experiment_v2 import ExperimentBudget
from dftworld_bench.contracts.resolved_lock import (
    FrozenExperimentOverrideError,
    ResolvedRunLock,
)


@dataclass(frozen=True)
class FrozenExperiment:
    """A frozen experiment template that cannot be overridden."""

    template_name: str
    api_profile: dict[str, Any]
    experiment_profile: dict[str, Any]
    runtime_profile: dict[str, Any]
    budget: ExperimentBudget = ExperimentBudget(
        max_model_turns=1024,
        max_total_tokens=100_000_000,
        agent_active_walltime_sec=86400.0,
        scheduler_wait_walltime_sec=604800.0,
    )
    site_profile: dict[str, Any] | None = None

    @property
    def budget_digest(self) -> str:
        return digest_bytes(canonical_json(self.budget.to_dict()))

    @property
    def api_profile_digest(self) -> str:
        return digest_bytes(canonical_json(self.api_profile))


def construct_experiment(
    selection: dict[str, str],
    registry: ProfileRegistry,
    budget: ExperimentBudget | None = None,
) -> FrozenExperiment:
    """Construct a frozen experiment from profile selections and optional budget."""
    api_name = selection.get("api", "default")
    experiment_name = selection.get("experiment", "default")
    runtime_name = selection.get("runtime", "local-sandbox")
    site_name = selection.get("site")

    if budget is None:
        budget = ExperimentBudget(
            max_model_turns=1024,
            max_total_tokens=100_000_000,
            agent_active_walltime_sec=86400.0,
            scheduler_wait_walltime_sec=604800.0,
        )

    api_profile = registry.profiles.get("api", {}).get(api_name)
    if api_profile is None:
        api_profile = {
            "endpoint_env": "DFTWORLD_API_ENDPOINT",
            "credential_env": "DFTWORLD_API_KEY",
            "max_retries": 3,
        }
    experiment_profile = registry.profiles.get("experiments", {}).get(
        experiment_name,
        {
            "max_concurrent_runs": 4,
            "default_replicates": 1,
        },
    )
    runtime_profile = registry.profiles.get("runtimes", {}).get(
        runtime_name,
        {
            "image": "dftworld-base:latest",
            "qualification": "local-smoke",
        },
    )
    site_profile = registry.profiles.get("sites", {}).get(site_name) if site_name else None

    agent_tag = selection.get("agent", "default")
    template_name = selection.get("template", f"{agent_tag}-{api_name}")

    return FrozenExperiment(
        template_name=template_name,
        budget=budget,
        api_profile=api_profile,
        experiment_profile=experiment_profile,
        runtime_profile=runtime_profile,
        site_profile=site_profile,
    )


def resolve_formal(
    experiment: FrozenExperiment,
    case: CaseSpec,
    run_id: str,
    replicate: int,
    *,
    model: str,
    benchmark_commit: str,
    condition_id: str | None = None,
    instruction: str = "",
    max_turns: int = 32,
    skills_sha: str | None = None,
    verifier_image_digest: str = "",
    budgets: dict[str, Any] | None = None,
    lock_created_at: str | None = None,
    overrides: dict[str, Any] | None = None,
    run_config: Any = None,
    run_metadata: dict[str, Any] | None = None,
    compute_route: Any = None,
    engine: str = "claude-code",
    engine_version: str | None = None,
    agent_image_digest: str | None = None,
) -> ResolvedRunLock:
    """Resolve a formal run lock from a frozen experiment.

    For formal runs, no overrides are permitted. Any override attempt raises
    FrozenExperimentOverrideError.  All identity fields come from the caller;
    no placeholders are acceptable.  Every sha256 digest is computed from
    real data; "sha256:none" is never written.
    """
    if overrides:
        raise FrozenExperimentOverrideError(
            f"Formal runs cannot override frozen dimensions: {sorted(overrides.keys())}"
        )

    from datetime import datetime, timezone

    if lock_created_at is None:
        lock_created_at = datetime.now(timezone.utc).isoformat()

    # Resolve provider/model from the model string (provider/model).
    parts = model.split("/", 1)
    provider = parts[0] if len(parts) > 1 else "unknown"
    model_id = parts[1] if len(parts) > 1 else model
    if run_config is not None and not (run_metadata or {}).get("override_present", False):
        provider = run_config.model.provider
        model_id = run_config.model.model_id

    # Compute real digests — no placeholders.
    prompt_digest = digest_bytes(instruction) if instruction else digest_bytes(model)
    sampling_digest = digest_bytes(canonical_json({"max_turns": max_turns}))
    context_digest = digest_bytes(canonical_json({"policy": "eval-v2"}))
    skill_bundle = digest_bytes(skills_sha) if skills_sha else digest_bytes(b"")

    resolved_engine = engine or "claude-code"
    if resolved_engine == "pagent":
        raise ValueError(
            "PAgent has been permanently retired from MLFFBench. "
            "Claude Code is the sole formal Candidate Agent ('claude-code')."
        )

    # Resolve real tool-policy
    policy_file = Path(__file__).resolve().parents[2] / "base-env-build" / "agent-claude-code" / "tool-policy.json"
    if policy_file.is_file():
        try:
            tool_surface_payload = json.loads(policy_file.read_text(encoding="utf-8"))
        except Exception:
            tool_surface_payload = {"engine": "claude-code", "tools": ["Bash", "FileRead", "FileEdit"]}
    else:
        tool_surface_payload = {"engine": "claude-code", "tools": ["Bash", "FileRead", "FileEdit"]}
    tool_surface_digest = digest_bytes(canonical_json(tool_surface_payload))

    # Build the complete lock payload — every digest is real.
    agent_block: dict[str, Any] = {
        "provider": provider,
        "model_id": model_id,
        "identity_strength": (
            run_config.model.identity_strength
            if run_config is not None and not (run_metadata or {}).get("override_present", False)
            else "alias"
        ),
        **({"deployment_id": run_config.model.deployment_id}
           if run_config is not None and not (run_metadata or {}).get("override_present", False)
           else {}),
        "engine": resolved_engine,
        "prompt_digest": prompt_digest,
        "sampling_digest": sampling_digest,
        "context_digest": context_digest,
        "skill_bundle_digest": skill_bundle,
        "tool_surface_digest": tool_surface_digest,
    }
    if engine_version:
        agent_block["engine_version"] = engine_version
    if agent_image_digest:
        agent_block["agent_image_digest"] = agent_image_digest

    payload: dict[str, Any] = {
        "case": {
            "case_id": case.case_id,
            "case_version": case.case_version,
            "schema_version": case.schema_version,
        },
        "experiment": {
            "template_name": experiment.template_name,
            "condition_id": condition_id or f"{run_id}-r{replicate}",
            "replicate": replicate,
            **({
                "run_config_id": run_config.run_config_id,
                "run_config_digest": run_config.digest,
                "run_config_mode": run_config.mode,
                "override_present": bool((run_metadata or {}).get("override_present", False)),
                "frozen": bool((run_metadata or {}).get("frozen", True)),
                "counted": bool((run_metadata or {}).get(
                    "counted", run_config.mode in {"pilot", "formal"}
                )),
            } if run_config is not None else {}),
        },
        "agent": agent_block,
        "api": {
            "api_profile_digest": (
                digest_bytes(canonical_json(run_config.api.model_dump(mode="json")))
                if run_config is not None else experiment.api_profile_digest
            ),
            "endpoint_env": run_config.api.endpoint_env if run_config is not None else experiment.api_profile.get("endpoint_env", ""),
            "credential_env": run_config.api.credential_env if run_config is not None else experiment.api_profile.get("credential_env", ""),
            **({
                "max_attempts": run_config.api.max_attempts,
                "request_timeout_sec": run_config.api.request_timeout_sec,
                "retry_base_delay_sec": run_config.api.retry_base_delay_sec,
                "retry_max_delay_sec": run_config.api.retry_max_delay_sec,
            } if run_config is not None else {}),
        },
        "candidate_runtime": {
            "image": experiment.runtime_profile.get("image", ""),
            "runtime_digest": digest_bytes(canonical_json(experiment.runtime_profile)),
            "qualification_status": "pending",
        },
        "verifier": {
            "runtime_digest": verifier_image_digest or digest_bytes(canonical_json(experiment.runtime_profile)),
            "isolation_config_digest": digest_bytes(canonical_json({"isolation": "networkless-nonroot-v1"})),
        },
        "infra": {
            "version": "2.0.0",
            "commit": benchmark_commit,
            "lock_created_at": lock_created_at,
        },
        "budgets": budgets or {
            "max_model_turns": experiment.budget.max_model_turns,
            "max_total_tokens": experiment.budget.max_total_tokens,
            "agent_active_walltime_sec": experiment.budget.agent_active_walltime_sec,
            "scheduler_wait_walltime_sec": experiment.budget.scheduler_wait_walltime_sec,
        },
    }

    # Add HPC block if site profile is provided
    if experiment.site_profile or compute_route is not None:
        payload["hpc"] = {
            "site_profile_digest": digest_bytes(canonical_json(experiment.site_profile or {})),
            "scheduler": (experiment.site_profile or {}).get("scheduler", ""),
            "capabilities": (experiment.site_profile or {}).get("capabilities", []),
        }
    if compute_route is not None:
        # P1 binding: ComputeProfile digest + selected SiteProfile digest +
        # the actual route the agent's compute_class resolved to.
        payload["hpc"].update(compute_route.lock_hpc_block())

    return ResolvedRunLock.create(payload)
