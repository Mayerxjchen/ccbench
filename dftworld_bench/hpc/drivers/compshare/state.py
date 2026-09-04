"""Account-scoped coordination primitives for the CompShare provider.

The provider account is a shared resource even when every benchmark run has
its own local manager object.  This module contains the small, dependency-free
coordination layer used by those managers:

* :func:`account_scope_hash` derives a stable, non-sensitive lock namespace;
* :class:`AccountScopeLock` combines a process-local ``RLock`` with a durable
  ``flock`` so threads and separate manager processes serialize the same
  account scope; and
* :class:`CompSharePolicyError` plus
  :func:`assert_account_capacity` provide one strict inventory/policy check.

The lock file is deliberately the only state created by this module.  A
caller supplies the state root explicitly; no credentials, provider command,
or network operation is inferred here.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from dftworld_bench.hpc.drivers.compshare.policy import (
    SAFE_DELETED_STATES,
    extract_verified_instance_id,
    instance_requires_cleanup,
    is_canonical_ownership_marker,
    matches_ownership_marker,
)


class CompShareStateError(RuntimeError):
    """The durable account-scoped coordination state is unusable."""


class AccountScopeLockTimeout(CompShareStateError):
    """The account lock could not be acquired before its deadline."""


# A descriptive alias makes the failure mode easy to discover for callers
# which use the more conventional ``...Error`` suffix.
AccountScopeLockTimeoutError = AccountScopeLockTimeout


class CompSharePolicyError(CompShareStateError):
    """Provider inventory or ownership policy cannot be proven safe."""


def _require_scope_string(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def account_scope_material(
    provider: str,
    target_binding: str,
    account: str,
    managed_account_scope_id: str | None = None,
) -> dict[str, str | None]:
    """Return the canonical non-secret identity used for account locking.

    ``managed_account_scope_id`` is an optional operator-defined account
    boundary.  It is included alongside the profile account (rather than
    replacing it silently), so changing either binding changes the lock
    namespace.  The resulting object contains only policy identity strings,
    never credential material.
    """

    provider = _require_scope_string("provider", provider)
    target_binding = _require_scope_string("target_binding", target_binding)
    account = _require_scope_string("account", account)
    if managed_account_scope_id is not None:
        managed_account_scope_id = _require_scope_string(
            "managed_account_scope_id", managed_account_scope_id
        )
    return {
        "provider": provider,
        "target_binding": target_binding,
        "account": account,
        "managed_account_scope_id": managed_account_scope_id,
        # The effective scope is useful in diagnostics while still being
        # included in the digest as an explicit policy decision.
        "effective_scope": managed_account_scope_id or account,
    }


def account_scope_hash(
    provider: str,
    target_binding: str,
    account: str,
    managed_account_scope_id: str | None = None,
) -> str:
    """Return the stable SHA-256 digest for one provider account scope."""

    encoded = json.dumps(
        account_scope_material(
            provider,
            target_binding,
            account,
            managed_account_scope_id,
        ),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# Short aliases keep the public vocabulary flexible without introducing a
# second implementation of the hash contract.
stable_account_scope_hash = account_scope_hash
account_scope_digest = account_scope_hash


_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}


def _thread_lock_for(path: Path) -> threading.RLock:
    key = str(path)
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _THREAD_LOCKS[key] = lock
        return lock


class AccountScopeLock:
    """Re-entrant lock for one provider/target/account scope.

    The process-local lock prevents two threads in the same Python process
    from racing before ``flock`` is reached.  The adjacent lock file extends
    the same critical section across independent manager processes.  Nested
    acquisition by one manager is supported: only the outermost acquisition
    owns the file descriptor and calls ``flock``/unlock.
    """

    def __init__(
        self,
        state_root: str | os.PathLike[str],
        *,
        provider: str,
        target_binding: str,
        account: str,
        managed_account_scope_id: str | None = None,
        timeout_sec: float | None = 30.0,
    ) -> None:
        root = Path(state_root)
        if not str(root):
            raise ValueError("state_root must be non-empty")
        if timeout_sec is not None:
            if isinstance(timeout_sec, bool) or not isinstance(
                timeout_sec, (int, float)
            ):
                raise ValueError("timeout_sec must be a finite non-negative number")
            if timeout_sec < 0:
                raise ValueError("timeout_sec must be a finite non-negative number")
        self.state_root = root
        self.scope_hash = account_scope_hash(
            provider,
            target_binding,
            account,
            managed_account_scope_id,
        )
        self.lock_path = (
            self.state_root / "locks" / f"account-{self.scope_hash}.lock"
        )
        self.timeout_sec = float(timeout_sec) if timeout_sec is not None else None
        self._thread_lock = _thread_lock_for(self.lock_path)
        self._handle: Any | None = None
        self._depth = 0

    @property
    def locked(self) -> bool:
        """Whether this lock object owns the account lock in this thread."""

        return self._depth > 0

    @property
    def depth(self) -> int:
        """Current nested acquisition depth (primarily useful in tests)."""

        return self._depth

    def _remaining(self, deadline: float | None) -> float | None:
        if deadline is None:
            return None
        return max(0.0, deadline - time.monotonic())

    def acquire(self, timeout_sec: float | None = None) -> "AccountScopeLock":
        """Acquire the account lock or raise :class:`AccountScopeLockTimeout`.

        ``timeout_sec=None`` means use the timeout configured at construction;
        passing a value overrides it for this acquisition.  A zero timeout
        performs a single non-blocking attempt, which makes lock-timeout
        behavior deterministic in tests and operator probes.
        """

        timeout = self.timeout_sec if timeout_sec is None else timeout_sec
        if timeout is not None:
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
                raise ValueError("timeout_sec must be a finite non-negative number")
            if timeout < 0:
                raise ValueError("timeout_sec must be a finite non-negative number")
            timeout = float(timeout)

        # RLock is intentionally acquired first: without this, two local
        # threads could both reach flock and rely on platform-specific
        # same-process flock semantics.
        if timeout is None:
            acquired = self._thread_lock.acquire()
        else:
            acquired = self._thread_lock.acquire(timeout=timeout)
        if not acquired:
            raise AccountScopeLockTimeout(
                f"timed out acquiring account scope lock {self.lock_path}"
            )

        # Re-entry by the same manager/thread does not open a second fd or
        # release the outer flock prematurely.
        if self._depth:
            self._depth += 1
            return self

        deadline = None if timeout is None else time.monotonic() + timeout
        handle: Any | None = None
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.lock_path, "a+", encoding="utf-8")
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN):
                        raise CompShareStateError(
                            f"failed to acquire account lock {self.lock_path}: {exc}"
                        ) from exc
                    remaining = self._remaining(deadline)
                    if remaining is not None and remaining <= 0:
                        raise AccountScopeLockTimeout(
                            f"timed out acquiring account scope lock {self.lock_path}"
                        )
                    time.sleep(
                        0.01
                        if remaining is None
                        else min(0.01, max(0.001, remaining))
                    )
            self._handle = handle
            self._depth = 1
            return self
        except Exception:
            if handle is not None:
                handle.close()
            self._thread_lock.release()
            raise

    def release(self) -> None:
        """Release one nested acquisition."""

        if self._depth <= 0:
            raise RuntimeError("cannot release an unheld account scope lock")
        if self._depth > 1:
            self._depth -= 1
            self._thread_lock.release()
            return

        handle = self._handle
        self._handle = None
        self._depth = 0
        try:
            if handle is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()
        finally:
            self._thread_lock.release()

    def __enter__(self) -> "AccountScopeLock":
        return self.acquire()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.release()


# A descriptive class alias for callers that want to emphasize persistence.
PersistentAccountScopeLock = AccountScopeLock


def _marker_like(instance: Mapping[str, Any]) -> bool:
    """Return whether an item claims an MLFFBench ownership namespace."""

    return any(
        isinstance(instance.get(key), str)
        and (
            instance[key].startswith("mlffbench-")
            or instance[key].startswith("mlffbench:")
        )
        for key in ("name", "remark")
    )


def _validate_managed_item(item: Mapping[str, Any]) -> tuple[str, str] | None:
    """Normalize one provider item or raise on an ambiguous managed record."""

    if not _marker_like(item):
        return None
    if not matches_ownership_marker(item):
        raise CompSharePolicyError(
            "provider instance has a malformed or ambiguous MLFFBench ownership marker"
        )

    # The current marker must be a complete pair.  Legacy markers remain
    # discoverable for cleanup, but are not silently upgraded to current
    # ownership.
    name = item.get("name")
    remark = item.get("remark")
    canonical_namespace = (
        isinstance(name, str)
        and name.startswith("mlffbench-")
        and not name.endswith("-worker")
    ) or (
        isinstance(remark, str)
        and remark.startswith("mlffbench:run:")
        and not remark.endswith(":worker")
    )
    if canonical_namespace and not is_canonical_ownership_marker(name, remark):
        raise CompSharePolicyError(
            "provider instance has an incomplete canonical ownership marker"
        )

    try:
        instance_id = extract_verified_instance_id(item)
    except Exception as exc:
        raise CompSharePolicyError(
            "managed provider record has no verified instance_id"
        ) from exc
    if not instance_id:
        raise CompSharePolicyError(
            "managed provider record has no verified instance_id"
        )
    status = item.get("status")
    if not isinstance(status, str) or not status.strip():
        raise CompSharePolicyError(
            f"managed provider record {instance_id!r} has malformed status"
        )
    return instance_id, status.strip().lower()


def assert_account_capacity(
    cli: Any,
    *,
    max_instances: int = 1,
) -> list[str]:
    """Fail closed unless the complete account inventory is below capacity.

    The helper owns the provider-list contract used by create, reconcile, and
    zero-orphan code.  It always requests ``instance_list(all=True)`` so the
    CLI consumes every page.  Unmanaged records are ignored, while a malformed
    managed marker/ID/status or any provider query failure raises
    :class:`CompSharePolicyError`.  Active means every state other than the
    two explicit safe terminal states (``DELETED`` and ``TERMINATED``).

    Returns the verified active managed IDs when capacity is available (thus
    normally ``[]`` for ``max_instances=1``).  A caller that wants to allow a
    known existing instance should perform that decision separately; this
    helper intentionally models *create* capacity only.
    """

    if isinstance(max_instances, bool) or not isinstance(max_instances, int):
        raise CompSharePolicyError("max_instances must be a positive integer")
    if max_instances < 1:
        raise CompSharePolicyError("max_instances must be a positive integer")
    try:
        items = cli.instance_list(all=True)
    except Exception as exc:
        raise CompSharePolicyError(
            f"provider instance_list(all=True) failed: {exc}"
        ) from exc
    if not isinstance(items, list):
        raise CompSharePolicyError(
            "provider instance_list(all=True) returned a non-list"
        )

    active: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise CompSharePolicyError(
                "provider instance_list(all=True) contained a non-object"
            )
        normalized = _validate_managed_item(item)
        if normalized is None:
            continue
        instance_id, status = normalized
        if status not in SAFE_DELETED_STATES and instance_requires_cleanup(status):
            active.append(instance_id)

    active = sorted(set(active))
    if len(active) >= max_instances:
        raise CompSharePolicyError(
            f"managed CompShare account capacity exhausted: active={active}, "
            f"max_instances={max_instances}"
        )
    return active


__all__ = [
    "AccountScopeLock",
    "AccountScopeLockTimeout",
    "AccountScopeLockTimeoutError",
    "CompSharePolicyError",
    "CompShareStateError",
    "PersistentAccountScopeLock",
    "account_scope_digest",
    "account_scope_hash",
    "account_scope_material",
    "assert_account_capacity",
    "stable_account_scope_hash",
]
