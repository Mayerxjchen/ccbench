"""bench-hpc CLI: ``python -m dftworld_bench.hpc <command> [args]``.

Protocol v2 commands:

- ``capabilities``                          what the gateway offers
- ``submit <job-file> OPERATION_ID ATTEMPT`` submit under explicit attempt identity
- ``status OPERATION_ID [--attempt N]``      operation state (latest or given attempt)
- ``logs OPERATION_ID [--attempt N]``
- ``fetch OPERATION_ID [--attempt N]``
- ``cancel OPERATION_ID [--attempt N]``
- ``usage``                                  gateway account usage
- ``help``                                   usage on stdout, exit 0

v2 commands need ``BENCH_HPC_RUN_ID`` alongside the gateway URL and token.
Historical protocol-v1 invocations (``status <job-id>``) keep working through
a hidden compatibility path and are not part of the v2 Agent interface.

Every command prints one stable JSON document on stdout. Errors go to stderr
with a nonzero exit code. An unknown command names every legal command on
stderr and exits 2, so the CLI is self-describing to a controller operator.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from dftworld_bench.hpc.client import HpcClient, HpcClientError, Transport
from dftworld_bench.hpc.job import JobError, JobSpec

GATEWAY_ENV = "BENCH_HPC_GATEWAY_URL"
TOKEN_ENV = "BENCH_HPC_RUN_TOKEN"
RUN_ENV = "BENCH_HPC_RUN_ID"

COMMANDS = ("capabilities", "submit", "status", "logs", "fetch", "cancel", "usage", "help")

# Hidden compatibility rule (not documented to agents): a bare historical
# scheduler job id — all digits or ``job-<digits>`` — routes to the frozen v1
# read paths instead of the v2 operation namespace.
_JOB_ID_RE = re.compile(r"^(?:job-)?\d+$")

_OPERATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")


def main(argv: list[str] | None = None, *, transport: Transport | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return _usage()
    command, args = argv[0], argv[1:]
    if command not in COMMANDS:
        return _fail(
            f"unknown command: {command!r}; try one of: {' | '.join(COMMANDS)}",
            code=2,
        )
    if command == "help":
        print(
            "usage: bench-hpc capabilities | usage\n"
            "       bench-hpc submit JOB_FILE OPERATION_ID ATTEMPT\n"
            "       bench-hpc status|logs|fetch|cancel OPERATION_ID [--attempt N]"
        )
        return 0
    gateway = os.environ.get(GATEWAY_ENV)
    token = os.environ.get(TOKEN_ENV)
    if not gateway or not token:
        missing = [name for name, value in ((GATEWAY_ENV, gateway), (TOKEN_ENV, token)) if not value]
        return _fail(f"set {', '.join(missing)}", code=2)
    client = HpcClient(gateway, token, transport=transport)
    try:
        result = _dispatch(client, command, args)
    except _ArgError as exc:
        return _fail(str(exc), code=2)
    except (JobError, HpcClientError, FileNotFoundError) as exc:
        return _fail(str(exc))
    print(json.dumps(result, sort_keys=True))
    return 0


def _split_attempt(args: list[str]) -> tuple[list[str], int | None]:
    """Pop a trailing ``--attempt N`` flag from the argument list."""
    if "--attempt" not in args:
        return args, None
    index = args.index("--attempt")
    if index != len(args) - 2:
        raise _ArgError("--attempt must be the last flag followed by a number")
    raw = args[index + 1]
    try:
        attempt = int(raw)
    except ValueError as exc:
        raise _ArgError(f"--attempt needs an integer, got {raw!r}") from exc
    if attempt < 1:
        raise _ArgError("attempt must be >= 1")
    return args[:index], attempt


def _dispatch(client: HpcClient, command: str, args: list[str]) -> dict[str, Any]:
    if command == "capabilities":
        return client.capabilities()
    if command == "submit":
        if len(args) == 1:
            # Frozen v1 form: bare job file.
            spec = JobSpec.load(Path(args[0]))
            return client.submit(spec.to_dict())
        if len(args) != 3:
            raise _ArgError("submit needs JOB_FILE [OPERATION_ID ATTEMPT]")
        spec = JobSpec.load(Path(args[0]))
        operation_id = args[1]
        try:
            attempt = int(args[2])
        except ValueError as exc:
            raise _ArgError(f"ATTEMPT must be an integer, got {args[2]!r}") from exc
        return client.submit_v2(
            spec.to_dict(),
            run_id=_run_id(),
            operation_id=operation_id,
            attempt=attempt,
        )
    if command in ("status", "logs", "fetch", "cancel"):
        targets, attempt = _split_attempt(args)
        if len(targets) != 1:
            raise _ArgError(f"{command} needs exactly one OPERATION_ID")
        target = targets[0]
        run_id = os.environ.get(RUN_ENV)
        looks_v1 = attempt is None and _JOB_ID_RE.match(target) is not None
        if looks_v1:
            # Hidden v1 compatibility route (historical job ids).
            if command == "status":
                return client.status(target)
            if command == "logs":
                return client.logs(target)
            if command == "fetch":
                return client.fetch(target)
            return client.cancel(target)
        # v2 operation route: requires run identity.
        if not run_id:
            raise _ArgError(f"set {RUN_ENV} for operation-scoped commands")
        if not _OPERATION_RE.match(target):
            raise _ArgError(f"unsafe OPERATION_ID: {target!r}")
        method = {
            "status": client.status_operation,
            "logs": client.logs_operation,
            "fetch": client.fetch_operation,
            "cancel": client.cancel_operation,
        }[command]
        return method(run_id, target, attempt)
    if command == "usage":
        return client.usage()
    raise AssertionError(f"unreachable command {command}")


def _run_id() -> str:
    value = os.environ.get(RUN_ENV)
    if not value:
        raise _ArgError(f"submit with OPERATION_ID ATTEMPT requires {RUN_ENV}")
    return value


class _ArgError(Exception):
    """Bad CLI invocation: usage problem, exit code 2."""


def _usage() -> int:
    print(f"usage: {sys.argv[0]} <{'|'.join(COMMANDS)}>", file=sys.stderr)
    return 2


def _fail(message: str, *, code: int = 1) -> int:
    print(f"bench-hpc: {message}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
