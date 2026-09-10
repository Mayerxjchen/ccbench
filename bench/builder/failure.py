"""Failure taxonomy and classification for case builder runs."""

from __future__ import annotations

from enum import Enum


class BuilderFailureCode(str, Enum):
    """Failure classifications for builder stage errors."""

    INTAKE_PARSE_ERROR = "INTAKE_PARSE_ERROR"
    SOURCE_TAINT_VIOLATION = "SOURCE_TAINT_VIOLATION"
    CASE_IR_INVALID = "CASE_IR_INVALID"
    ADMISSION_REJECTED = "ADMISSION_REJECTED"
    VERIFIER_COMPILE_ERROR = "VERIFIER_COMPILE_ERROR"
    MOUNT_SMOKE_FAILED = "MOUNT_SMOKE_FAILED"
    CALIBRATION_THRESHOLD_UNMET = "CALIBRATION_THRESHOLD_UNMET"
    RELEASE_CHECK_FAILED = "RELEASE_CHECK_FAILED"
    PUBLISH_CONFLICT = "PUBLISH_CONFLICT"
