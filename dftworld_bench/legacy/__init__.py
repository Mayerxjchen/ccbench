"""Legacy compatibility layer for MLFFBench.

Houses historical agent wrappers and transports (e.g. PAgent) for read-only
backwards compatibility with historical runs and run records.
These classes MUST NEVER be used in active formal benchmark executions.
"""

from __future__ import annotations
