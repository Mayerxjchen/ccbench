"""ComputeProfile + ComputeRouter: the routing identity above SiteProfile.

A run selects a *compute class* (``cpu`` or ``gpu``); only the operator-owned
ComputeProfile maps that class to a named SiteProfile.  The Case, the PAgent
and the ``hpc-submit`` skill never see site names, partitions, regions or
image IDs — :meth:`ComputeProfile.public_view` strips them by construction.

Fail-closed by design:

* an unknown compute class is an error, never a fallback;
* a profile without a route for the requested class is an error (it never
  "tries the other site");
* a route naming an unregistered site profile is an error at construction;
* ``cpu`` must carry ``gpus == 0`` and ``gpu`` must carry ``gpus >= 1`` —
  the request's own fields are re-checked against its class at routing time,
  so a tampered or legacy-inferred request cannot sneak past the router.

The :meth:`ComputeRouter.lock_hpc_block` output is the P1 RunLock binding:
ComputeProfile digest + selected SiteProfile digest + the actual route.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import jsonschema

from bench.hpc.site_profile import HpcSiteProfile, SiteProfileError

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "schemas" / "compute-profile.schema.json"
)

COMPUTE_CLASSES: tuple[str, ...] = ("cpu", "gpu")


class ComputeProfileError(Exception):
    """A compute profile violates the frozen routing contract."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ComputeProfile:
    """Immutable ``compute_class -> site_profile`` routing table.

    Construct only via :meth:`from_dict` / :meth:`from_file` so the schema
    gate and the digest always run.
    """

    profile_id: str
    routes: Mapping[str, str] = field(default_factory=dict)
    digest: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ComputeProfile":
        if not isinstance(payload, dict):
            raise ComputeProfileError("compute profile must be a mapping")
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        errors = sorted(
            jsonschema.Draft202012Validator(schema).iter_errors(payload),
            key=lambda err: list(err.path),
        )
        if errors:
            first = errors[0]
            where = ".".join(str(p) for p in first.path) or "$"
            raise ComputeProfileError(
                f"compute profile violates compute-profile.schema.json at "
                f"{where}: {first.message}"
            )
        routes = {
            compute_class: route["site_profile"]
            for compute_class, route in sorted(payload["routes"].items())
        }
        return cls(
            profile_id=payload["profile_id"],
            routes=routes,
            digest=_digest(
                {"profile_id": payload["profile_id"], "routes": routes}
            ),
        )

    @classmethod
    def from_file(cls, path: Path) -> "ComputeProfile":
        text = Path(path).read_text(encoding="utf-8")
        payload = (
            tomllib.loads(text)
            if str(path).endswith(".toml")
            else json.loads(text)
        )
        return cls.from_dict(payload)

    def site_profile_name(self, compute_class: str) -> str:
        name = self.routes.get(compute_class)
        if name is None:
            raise ComputeProfileError(
                f"compute profile {self.profile_id!r} has no {compute_class!r} "
                f"route; refusing to fall back to another site "
                f"(routes={sorted(self.routes)})"
            )
        return name

    def public_view(self) -> dict[str, Any]:
        """What an Agent may see: which classes route at all, plus the digest.

        Site profile names are stripped by construction — the public shape
        carries no provider vocabulary even when a site profile happens to be
        named like one.
        """
        return {
            "profile_id": self.profile_id,
            "compute_classes": [
                compute_class
                for compute_class in COMPUTE_CLASSES
                if compute_class in self.routes
            ],
            "digest": self.digest,
        }


@dataclass(frozen=True)
class ResolvedRoute:
    """One routing decision: class -> named site profile -> concrete resource."""

    compute_profile_id: str
    compute_profile_digest: str
    compute_class: str
    site_profile_name: str
    site_profile: HpcSiteProfile
    resource: Any  # site_profile.resolve_workload output (ResolvedResource)

    def lock_hpc_block(self) -> dict[str, Any]:
        """The P1 RunLock binding: profile digest + site digest + route."""
        return {
            "compute_profile_id": self.compute_profile_id,
            "compute_profile_digest": self.compute_profile_digest,
            "compute_route": {
                "compute_class": self.compute_class,
                "site_profile": self.site_profile_name,
            },
            "site_profile_digest": self.site_profile.digest,
        }


class ComputeRouter:
    """Binds a ComputeProfile to the operator-registered SiteProfiles."""

    def __init__(
        self,
        profile: ComputeProfile,
        *,
        site_profiles: Mapping[str, HpcSiteProfile],
    ) -> None:
        self._profile = profile
        self._site_profiles = dict(site_profiles)
        # Config-tampering gate: every route must name a registered profile.
        for compute_class in COMPUTE_CLASSES:
            name = profile.routes.get(compute_class)
            if name is None:
                continue
            if name not in self._site_profiles:
                raise ComputeProfileError(
                    f"route {compute_class!r} names unregistered site profile "
                    f"{name!r}; registered: {sorted(self._site_profiles)}"
                )

    @property
    def profile(self) -> ComputeProfile:
        return self._profile

    def public_view(self) -> dict[str, Any]:
        return self._profile.public_view()

    def route(self, compute_class: str) -> ResolvedRoute:
        if compute_class not in COMPUTE_CLASSES:
            raise ComputeProfileError(
                f"unknown compute class {compute_class!r}; "
                f"allowed: {list(COMPUTE_CLASSES)}"
            )
        name = self._profile.site_profile_name(compute_class)
        site = self._site_profiles[name]
        try:
            resource = site.resolve_workload(compute_class)
        except SiteProfileError as exc:
            raise ComputeProfileError(
                f"route {compute_class!r}->{name!r} cannot satisfy the class: {exc}"
            ) from exc
        return ResolvedRoute(
            compute_profile_id=self._profile.profile_id,
            compute_profile_digest=self._profile.digest,
            compute_class=compute_class,
            site_profile_name=name,
            site_profile=site,
            resource=resource,
        )

    def route_request(self, request: Any) -> ResolvedRoute:
        """Route an ExecutionRequestV2, re-checking its own consistency.

        The mechanical rule is enforced again here (the request schema and
        from_dict already gate it) so a legacy-inferred or hand-built request
        cannot disagree with the route it asks for.
        """
        compute_class = getattr(request, "compute_class", "")
        if compute_class not in COMPUTE_CLASSES:
            raise ComputeProfileError(
                f"request carries unusable compute_class {compute_class!r}; "
                "v2 requests must state it explicitly"
            )
        gpus = int(getattr(request.resources, "gpus", 0))
        if compute_class == "cpu" and gpus != 0:
            raise ComputeProfileError(
                f"compute_class=cpu requires gpus=0, request carries gpus={gpus}"
            )
        if compute_class == "gpu" and gpus < 1:
            raise ComputeProfileError(
                f"compute_class=gpu requires gpus>=1, request carries gpus={gpus}"
            )
        return self.route(compute_class)
