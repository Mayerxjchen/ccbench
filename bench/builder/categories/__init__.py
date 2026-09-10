"""Category plugins for Bench Case Builder."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class CaseCategoryPlugin(ABC):
    """Abstract base class for domain-specific benchmark category plugins."""

    category_name: str

    @abstractmethod
    def validate_source(self, source_dir: Any) -> list[str]:
        """Validate category-specific source files."""
        ...

    @abstractmethod
    def validate_design(self, case_ir: dict[str, Any]) -> list[str]:
        """Validate category-specific fields in Case IR."""
        ...

    @abstractmethod
    def derive_verifier_layers(self, case_ir: dict[str, Any]) -> list[str]:
        """Derive required verifier layers for this category."""
        ...

    @abstractmethod
    def validate_calibration(self, calibration_data: dict[str, Any]) -> bool:
        """Validate threshold calibration consistency."""
        ...
