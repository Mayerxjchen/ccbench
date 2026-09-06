"""Category plugins for HPC-controller cases (Task 16).

A plugin translates a case's *scientific* requirements and declared
``scientific_capabilities`` into the remote compute command a Candidate
submits through the common Gateway (``bench-hpc submit job.yaml``).  Plugins
are shared across cases and are resolved by capability, never by case id —
``select_mlp_plugin`` keys on ``runtime_requirements`` / capabilities, so
adding a seventh HPC case never touches core execution or a plugin branch.

A plugin owns only capability/scientific translation.  It never references a
case id, never stages a hidden ``solution/``, and never carries scheduler
policy (account/partition/QoS/gres) — those live in the site profile and the
Gateway, not in a plugin.
"""

from __future__ import annotations

from ccbench.plugins.mlp import (
    MLPPlugin,
    PLUGINS,
    MLPPluginError,
    build_canary_job,
    canary_output,
    select_mlp_plugin,
)

__all__ = [
    "MLPPlugin",
    "MLPPluginError",
    "PLUGINS",
    "build_canary_job",
    "canary_output",
    "select_mlp_plugin",
]
