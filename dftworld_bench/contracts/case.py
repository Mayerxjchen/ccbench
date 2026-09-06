"""Versioned dual-mode case contract.

Canonical v2 manifests declare ``[execution]`` / ``[candidate]`` / ``[hpc]``
blocks; legacy manifests declare ``task.execution_backend`` and are normalized
through an explicit alias table. The execution class is **never** inferred from
the case number or directory name — a manifest that declares none fails closed.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import jsonschema
import yaml

ExecutionClass = Literal["local_sandbox", "hpc_controller"]

# Explicit compatibility aliases only. New names are added deliberately; the
# harness never guesses an execution class from a case directory name.
EXECUTION_ALIASES = {"real_hpc_controller": "hpc_controller"}
EXECUTION_CLASSES = frozenset({"local_sandbox", "hpc_controller"})

# Capability-matrix names a case may gate on in [hpc.qualification].requires.
# Mirrors the case.schema.json pattern; enforced here too so the legacy
# (non-schema-validated) path cannot skip the shape check.
QUALIFICATION_REQUIRES_RE = re.compile(r"^(dispatcher|runtime)\.[a-z0-9-]+$")

# Infra registry (capability-matrix spec §5b): declared runtime families →
# qualification capabilities.  Curated and site-independent (validated against
# the frozen runtime-lock catalog).  A family listed here AUTO-ADDS its
# capability to a case's effective gate.  KNOWN_RUNTIME_FAMILIES is the union
# of the registry keys and families the infra knows with NO qualification gate
# yet (deepmd-jax): those add nothing but must not fail closed.  dispatcher.*
# tiers are NEVER auto-derived — the coarse legacy [hpc] required_capabilities
# declares batch_jobs+gpu for all of 031–034 yet they gate differently, so the
# tier declaration is always explicit in [hpc.qualification].requires.
RUNTIME_FAMILY_CAPABILITIES: dict[str, str] = {
    "matclaw-cips": "runtime.matclaw-gpu",
    "ai2kit": "runtime.ai2kit",
    "cp2k": "runtime.cp2k",
}
KNOWN_RUNTIME_FAMILIES: frozenset[str] = frozenset(
    set(RUNTIME_FAMILY_CAPABILITIES) | {"deepmd-jax"}
)

# Fields that belong to the infrastructure layer, not the case manifest.
# Cases declare scientific requirements; the harness resolves these to
# concrete images, models, and API configurations.
INFRA_OWNED_CASE_FIELDS = frozenset({
    "provider", "model", "endpoint", "api_key", "retry",
    "request_timeout", "max_turns", "agent_walltime", "skills", "fallback",
})

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "case.schema.json"
VOCABULARIES_PATH = Path(__file__).resolve().parents[2] / "schemas" / "coverage-vocabularies.yaml"
_COVERAGE_VOCABULARIES: dict[str, set[str]] | None = None


def load_coverage_vocabularies() -> dict[str, set[str]]:
    """Load curated coverage vocabularies from schemas/coverage-vocabularies.yaml.

    Returns mapping of dimension_name -> set of allowed values.
    scientific_domain has empty set (open vocabulary).
    """
    global _COVERAGE_VOCABULARIES
    if _COVERAGE_VOCABULARIES is not None:
        return _COVERAGE_VOCABULARIES

    if not VOCABULARIES_PATH.is_file():
        raise CaseContractError(f"coverage vocabularies file missing at {VOCABULARIES_PATH}")

    data = yaml.safe_load(VOCABULARIES_PATH.read_text(encoding="utf-8")) or {}
    dims = data.get("dimensions") or {}
    vocab: dict[str, set[str]] = {}
    for dim_name, info in dims.items():
        if isinstance(info, dict):
            vocab[dim_name] = set(info.get("values") or [])
        else:
            vocab[dim_name] = set()

    _COVERAGE_VOCABULARIES = vocab
    return vocab


def validate_coverage_tags(coverage: CoverageTags) -> None:
    """Validate coverage tags against curated vocabularies.

    Raises CaseContractError if a curated dimension carries an invalid value.
    """
    vocab = load_coverage_vocabularies()
    for dim in ("method_family", "material_class", "computation_type"):
        val = getattr(coverage, dim, "")
        if val:
            allowed = vocab.get(dim, set())
            if allowed and val not in allowed:
                raise CaseContractError(
                    f"invalid coverage tag for {dim}: {val!r}; expected one of {sorted(allowed)}"
                )


# Implicit legacy public-files rule, mirroring the old `COPY public/ /app/`
# image staging. Canonical v2 manifests override this with explicit rules.
_LEGACY_PUBLIC_RULE = {"source": "public/**", "destination": ".", "strip_prefix": "public"}


class CaseContractError(ValueError):
    """Raised when a case manifest violates the versioned case contract."""


@dataclass(frozen=True)
class PublicFileRule:
    """One allowlist public-file mapping: case-relative source -> candidate path."""

    source: str
    destination: str
    strip_prefix: str | None = None


@dataclass(frozen=True)
class RuntimeRequirement:
    """Scientific runtime requirement declared by a case.

    Cases declare what they need (e.g., "deepmd-jax >=0.2"); the harness
    resolves this to a concrete image and version.  ``family`` is the runtime
    family (e.g. "deepmd-jax"); it must match the registry's profile family.
    """

    name: str
    family: str = ""
    version: str = ""


@dataclass(frozen=True)
class ScientificCapabilities:
    """Scientific compute capabilities an HPC case must/may run.

    ``required`` is fixed by the paper (e.g. CP2K); ``optional`` names
    heavy-science choices that remain a Discovery decision (e.g. GPU DPMP
    training).  Category plugins consume these capabilities; no plugin ever
    dispatches on a case id.
    """

    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()


@dataclass(frozen=True)
class CoverageTags:
    """Portfolio coverage dimensions for representativeness analysis.

    All fields are optional; empty string means untagged.  Dimension
    vocabularies live in ``schemas/coverage-vocabularies.yaml`` (single
    source of truth — no dual-write).
    """

    scientific_domain: str = ""
    method_family: str = ""
    material_class: str = ""
    computation_type: str = ""
    paradigm: str = "standard"


def _reject_infra_owned_fields(raw: dict[str, Any]) -> None:
    """Reject case manifests that contain infra-owned fields.

    These fields belong to the infrastructure layer (harness, profiles, resolver)
    and must not appear in case manifests. Cases declare scientific requirements;
    the harness resolves them to concrete configurations.
    """
    found = sorted(INFRA_OWNED_CASE_FIELDS & set(raw))
    agent = raw.get("agent") or {}
    found += [f"agent.{k}" for k in sorted(INFRA_OWNED_CASE_FIELDS & set(agent))]
    if found:
        raise CaseContractError(f"Infra-owned Case fields are forbidden: {found}")


@dataclass(frozen=True)
class CaseSpec:
    """Frozen, validated view of a case manifest.

    Only `execution_class`, `public_files`, and the explicit candidate fields
    are governed by the new contract; legacy fields (timeouts, resources) are
    carried through for provenance and the legacy harness.
    """

    case_id: str
    case_version: str
    schema_version: str
    execution_class: ExecutionClass
    instruction_path: str
    public_files: tuple[PublicFileRule, ...]
    submission_root: str
    candidate_image: str | None
    agent_timeout_sec: float
    verifier_timeout_sec: float
    verifier_env: dict[str, str]
    candidate_resources: dict[str, Any]
    legacy_execution_value: str | None
    legacy_submission_layout: bool
    # Runtime requirements declared by the case (scientific needs, not images)
    runtime_requirements: tuple[RuntimeRequirement, ...] = ()
    # Case-declared agent budget bound (candidate property), only when the case
    # needs more than the infra standard; absent means the infra default.
    max_agent_seconds: float | None = None
    # Legacy agent fields that must be migrated before formal runs
    legacy_agent_fields: tuple[str, ...] = ()
    # Structural pre-Verifier submission contract (shape checks only)
    submission_contract: dict[str, Any] = field(default_factory=dict)
    # Scientific compute capabilities declared by an hpc_controller case
    # (required / optional); consumed by category plugins, never by core dispatch.
    scientific_capabilities: ScientificCapabilities | None = None
    # Qualification capabilities this case declares in [hpc.qualification].
    #requires (capability-matrix names, e.g. "dispatcher.gpu" /
    # "runtime.matclaw-gpu"); the site receipt must derive PASS for every name
    # via case_requirements_satisfied before a formal run.  Declared names are
    # authoritative for the dispatcher tiers; registry auto-derivation (see
    # effective_qualification_requires) can only add runtime.* gates.
    qualification_requires: tuple[str, ...] = ()
    # Portfolio coverage dimensions (optional; empty = untagged).
    coverage: CoverageTags = field(default_factory=CoverageTags)
    # Case root, populated by CaseSpec.load; needed by the packager to resolve
    # glob sources and the instruction file.
    case_dir: Path | None = None

    @property
    def path(self) -> Path:
        """Alias for the case root directory (set by ``CaseSpec.load``)."""
        if self.case_dir is None:
            raise CaseContractError("CaseSpec carries no case root; use CaseSpec.load")
        return self.case_dir

    @property
    def effective_qualification_requires(self) -> tuple[str, ...]:
        """Effective qualification gate = declared ∪ registry auto-derivation.

        The registry (RUNTIME_FAMILY_CAPABILITIES) maps each declared
        ``[runtime].requirements`` family to a runtime capability
        (matclaw-cips → runtime.matclaw-gpu, ai2kit → runtime.ai2kit,
        cp2k → runtime.cp2k); auto-derivation can only ADD gates, never remove
        a declared one.  Families the infra knows with no gate yet (deepmd-jax)
        add nothing.  Unknown non-empty families were rejected at load
        (fail-closed), so this never silently skips a gate.
        """
        requires = list(self.qualification_requires)
        if self.execution_class == "hpc_controller":
            for req in self.runtime_requirements:
                capability = RUNTIME_FAMILY_CAPABILITIES.get(
                    (req.family or "").strip()
                )
                if capability and capability not in requires:
                    requires.append(capability)
        return tuple(requires)

    @classmethod
    def load(cls, case_dir: Path) -> "CaseSpec":
        case_dir = Path(case_dir)
        if not case_dir.is_dir() and (case_dir.parent / "cases" / case_dir.name).is_dir():
            case_dir = case_dir.parent / "cases" / case_dir.name
        case_toml = case_dir / "case.toml"
        task_toml = case_dir / "task.toml"
        yaml_path = case_dir / "task.yaml"
        if case_toml.exists():
            raw = tomllib.loads(case_toml.read_text(encoding="utf-8"))
        elif task_toml.exists() and yaml_path.exists():
            raise CaseContractError(
                f"both task.toml and task.yaml present in {case_dir}; "
                "refusing to guess which is authoritative"
            )
        elif task_toml.exists():
            raw = tomllib.loads(task_toml.read_text(encoding="utf-8"))
        elif yaml_path.exists():
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        else:
            raise CaseContractError(f"no case.toml, task.toml or task.yaml in {case_dir}")
        if not isinstance(raw, dict):
            raise CaseContractError(f"case manifest {case_dir} is not a table")
        return cls._from_raw(raw, case_dir)

    @classmethod
    def _from_raw(cls, raw: dict[str, Any], case_dir: Path) -> "CaseSpec":
        # Reject infra-owned fields before any other processing
        _reject_infra_owned_fields(raw)

        explicit = raw.get("execution")
        if explicit is not None and not isinstance(explicit, dict):
            raise CaseContractError("[execution] must be a table")
        explicit_class = None if explicit is None else explicit.get("class")

        task_tbl = raw.get("task")
        legacy_value = None
        if isinstance(task_tbl, dict):
            legacy_value = task_tbl.get("execution_backend")

        if explicit_class is not None and legacy_value is not None:
            raise CaseContractError(
                "both [execution].class and task.execution_backend are present; "
                "ambiguous"
            )
        if explicit_class is None and legacy_value is None:
            raise CaseContractError(
                "no execution.class declared; execution is never inferred from "
                "the case number or directory name"
            )

        raw_value = explicit_class if explicit_class is not None else legacy_value
        normalized = EXECUTION_ALIASES.get(raw_value, raw_value)
        if normalized not in EXECUTION_CLASSES:
            raise CaseContractError(
                f"unknown execution value {raw_value!r}; expected one of "
                f"{sorted(EXECUTION_CLASSES)}"
            )
        execution_class: ExecutionClass = normalized

        hpc_block = raw.get("hpc")
        if execution_class == "hpc_controller" and hpc_block is None:
            raise CaseContractError(
                "hpc_controller requires an [hpc] block (contract_version and "
                "required_capabilities)"
            )
        if execution_class == "local_sandbox" and hpc_block is not None and explicit_class is not None:
            raise CaseContractError("[hpc] is not allowed for local_sandbox execution")

        contract_doc: dict[str, Any] = {}
        for key in ("execution", "candidate", "hpc", "runtime", "coverage", "submission_contract"):
            if key in raw:
                contract_doc[key] = raw[key]
        if explicit_class is not None:
            cls._validate_schema(contract_doc)

        instruction = "task.md" if (case_dir / "task.md").is_file() else "instruction.md"
        is_case_v2 = (case_dir / "case.toml").is_file()
        submission_root = "final" if is_case_v2 else "."
        legacy_layout = False if is_case_v2 else True
        candidate_image: str | None = None
        max_agent_seconds: float | None = None
        public_rules: list[PublicFileRule] = []
        candidate = raw.get("candidate")
        if isinstance(candidate, dict):
            instruction = str(candidate.get("instruction", instruction))
            submission_root = str(candidate.get("submission_root", submission_root))
            legacy_layout = bool(candidate.get("legacy_submission_layout", legacy_layout))
            image = candidate.get("image")
            candidate_image = None if image is None else str(image)
            max_agent_seconds = candidate.get("max_agent_seconds")
            if max_agent_seconds is not None:
                max_agent_seconds = float(max_agent_seconds)
            for rule in candidate.get("files") or []:
                public_rules.append(_parse_public_rule(rule))
        if not public_rules:
            if (case_dir / "input").is_dir():
                public_rules.append(_parse_public_rule({"source": "input/**", "destination": ".", "strip_prefix": "input"}))
            else:
                public_rules.append(_parse_public_rule(_LEGACY_PUBLIC_RULE))

        agent = raw.get("agent") or {}
        verifier = raw.get("verifier") or {}
        environment = raw.get("environment") or {}
        resources = {
            key: environment.get(key)
            for key in ("cpus", "memory_mb", "storage_mb", "gpus", "allow_internet")
        }

        # Parse runtime requirements from the runtime block
        runtime_requirements: list[RuntimeRequirement] = []
        runtime = raw.get("runtime")
        if isinstance(runtime, dict):
            for req in runtime.get("requirements") or []:
                if isinstance(req, dict):
                    runtime_requirements.append(RuntimeRequirement(
                        name=str(req.get("name", "")),
                        family=str(req.get("family", "")),
                        version=str(req.get("version", "")),
                    ))

        # Track legacy agent fields that must be migrated
        legacy_agent_fields: list[str] = []
        if agent.get("timeout_sec") is not None:
            legacy_agent_fields.append("agent.timeout_sec")

        # Structural submission contract (shape checks only; never executes)
        submission_contract_raw = raw.get("submission_contract")
        submission_contract: dict[str, Any] = {}
        if isinstance(submission_contract_raw, dict):
            submission_contract = submission_contract_raw

        # Coverage tags for portfolio representativeness (optional).
        coverage_raw = raw.get("coverage")
        coverage = CoverageTags()
        if isinstance(coverage_raw, dict):
            paradigm_raw = str(coverage_raw.get("paradigm", "standard"))
            if paradigm_raw not in ("standard", "research_question"):
                raise CaseContractError(
                    f"invalid coverage.paradigm: {paradigm_raw!r}; "
                    "expected 'standard' or 'research_question'"
                )
            coverage = CoverageTags(
                scientific_domain=str(coverage_raw.get("scientific_domain", "")),
                method_family=str(coverage_raw.get("method_family", "")),
                material_class=str(coverage_raw.get("material_class", "")),
                computation_type=str(coverage_raw.get("computation_type", "")),
                paradigm=paradigm_raw,
            )
            validate_coverage_tags(coverage)

        # Scientific compute capabilities from the [hpc] block (HPC cases only).
        # ``required``/``optional`` name scientific capabilities (cp2k, dpmp,
        # ...); the category plugin translates them, never core execution.
        scientific_capabilities: ScientificCapabilities | None = None
        qualification_requires: tuple[str, ...] = ()
        if isinstance(hpc_block, dict):
            sc = hpc_block.get("scientific_capabilities")
            if isinstance(sc, dict):
                scientific_capabilities = ScientificCapabilities(
                    required=tuple(str(x) for x in (sc.get("required") or [])),
                    optional=tuple(str(x) for x in (sc.get("optional") or [])),
                )
            # [hpc].qual_requires is WITHDRAWN (capability-matrix spec §5b):
            # a manifest still declaring the bare array is rejected loudly on
            # every path so the migration cannot silently change a case's gate.
            withdrawn = hpc_block.get("qual_requires")
            if withdrawn is not None:
                raise CaseContractError(
                    "[hpc].qual_requires is withdrawn; declare "
                    "[hpc.qualification].requires = [...] instead"
                )
            # [hpc.qualification].requires: capability-matrix names the site
            # receipt must derive PASS for before this case may run formally.
            # Strict shape + pattern check even on the legacy path (fail-
            # closed: a typo'd name must block, never read as satisfied).
            qual_block = hpc_block.get("qualification")
            qr_names = None
            if qual_block is not None:
                if not isinstance(qual_block, dict):
                    raise CaseContractError(
                        "[hpc].qualification must be a table"
                    )
                qr_names = qual_block.get("requires")
            if qr_names is not None:
                if not isinstance(qr_names, list) or any(
                    not isinstance(x, str) for x in qr_names
                ):
                    raise CaseContractError(
                        "[hpc].qualification.requires must be an array of strings"
                    )
                for name in qr_names:
                    if not QUALIFICATION_REQUIRES_RE.match(name):
                        raise CaseContractError(
                            f"invalid [hpc].qualification.requires capability "
                            f"{name!r}; "
                            r"expected ^(dispatcher|runtime)\.[a-z0-9-]+$"
                        )
                qualification_requires = tuple(qr_names)

        # Registry auto-derivation is driven by [runtime].requirements families
        # and applies only to HPC cases (the sole gate consumer).  A non-empty
        # family the infra does not know fails closed at load — a typo or a
        # runtime with no catalog entry must never silently skip a gate.
        if execution_class == "hpc_controller":
            for req in runtime_requirements:
                family = (req.family or "").strip()
                if not family:
                    continue
                if family not in KNOWN_RUNTIME_FAMILIES:
                    raise CaseContractError(
                        f"unknown runtime family {family!r} in "
                        f"[runtime].requirements; known: "
                        f"{sorted(KNOWN_RUNTIME_FAMILIES)}"
                    )

        return cls(
            case_id=str((raw.get("task") or {}).get("name", "")),
            case_version=str(raw.get("case_version", "1.0")),
            schema_version=str(raw.get("schema_version", "1")),
            execution_class=execution_class,
            instruction_path=instruction,
            public_files=tuple(public_rules),
            submission_root=submission_root,
            candidate_image=candidate_image,
            agent_timeout_sec=float(agent.get("timeout_sec", 0.0)),
            verifier_timeout_sec=float(verifier.get("timeout_sec", 0.0)),
            verifier_env={str(k): str(v) for k, v in (verifier.get("env") or {}).items()},
            candidate_resources=resources,
            legacy_execution_value=legacy_value,
            legacy_submission_layout=legacy_layout,
            runtime_requirements=tuple(runtime_requirements),
            max_agent_seconds=max_agent_seconds,
            legacy_agent_fields=tuple(legacy_agent_fields),
            submission_contract=submission_contract,
            scientific_capabilities=scientific_capabilities,
            qualification_requires=qualification_requires,
            coverage=coverage,
            case_dir=case_dir,
        )

    @staticmethod
    def _validate_schema(contract_doc: dict[str, Any]) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        validator = jsonschema.Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(contract_doc), key=lambda e: list(e.path))
        if errors:
            first = errors[0]
            raise CaseContractError(
                f"case contract schema violation at {'/'.join(map(str, first.path))}: "
                f"{first.message}"
            )


def _parse_public_rule(raw: dict[str, Any]) -> PublicFileRule:
    if not isinstance(raw, dict):
        raise CaseContractError("candidate.files entries must be tables")
    source = raw.get("source")
    destination = raw.get("destination")
    if not isinstance(source, str) or not source:
        raise CaseContractError("candidate.files rule missing string source")
    if not isinstance(destination, str) or not destination:
        raise CaseContractError("candidate.files rule missing string destination")
    _reject_unsafe_public_path(source, "source")
    _reject_unsafe_public_path(destination, "destination")
    strip = raw.get("strip_prefix")
    return PublicFileRule(
        source=source,
        destination=destination,
        strip_prefix=None if strip is None else str(strip),
    )


def _reject_unsafe_public_path(value: str, what: str) -> None:
    posix = PurePosixPath(value)
    if posix.is_absolute() or ".." in posix.parts:
        raise CaseContractError(
            f"unsafe {what} path {value!r}: absolute or parent-traversing public "
            "paths are forbidden"
        )
