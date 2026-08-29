"""Public data-model helpers for the read-only DOCX inspector.

The inspector deliberately returns ordinary JSON-compatible dictionaries.  The
exception type lives here so callers can catch one stable error without
depending on the implementation modules used to inspect individual OOXML
parts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class DocxInspectionError(ValueError):
    """Raised when an input cannot be inspected as a valid DOCX package.

    ``kind`` is a small machine-readable category (for example ``zip`` or
    ``core_part``); ``path`` is retained for diagnostics but is never included
    in the inspection report itself.
    """

    def __init__(self, message: str, *, kind: str = "invalid_docx", path: str | Path | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.path = Path(path) if path is not None else None


Inspection = dict[str, Any]


__all__ = ["DocxInspectionError", "Inspection"]
