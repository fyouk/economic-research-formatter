"""Models used by strict rule validation.

The validator intentionally returns plain JSON-compatible mappings at its public
boundary.  These small dataclasses keep issue construction and serialization
consistent without coupling callers to a third-party validation framework.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RuleValidationIssue:
    """One validation problem with a stable location and machine-readable code."""

    code: str
    message: str
    filename: str
    field_path: str
    rule_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "filename": self.filename,
            "field_path": self.field_path,
            "rule_id": self.rule_id,
        }

    def legacy_message(self) -> str:
        """Render the backwards-compatible one-line representation."""

        owner = self.rule_id or "<file>"
        return (
            f"{self.filename}::{owner}::{self.field_path}: "
            f"{self.code}: {self.message}"
        )


@dataclass
class RuleValidationResult:
    """Aggregate result returned by :func:`validate_rules_structured`."""

    valid: bool
    errors: list[RuleValidationIssue] = field(default_factory=list)
    rule_count: int = 0
    conflict_count: int = 0
    unresolved_count: int = 0
    source_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        errors = [issue.to_dict() for issue in self.errors]
        return {
            "valid": self.valid,
            "errors": errors,
            "rule_count": self.rule_count,
            "conflict_count": self.conflict_count,
            "unresolved_count": self.unresolved_count,
            "source_count": self.source_count,
            "error_count": len(errors),
        }
