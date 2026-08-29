"""Typed, privacy-conscious citation evidence shared by lint rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


CitationKind = Literal["parenthetical", "narrative", "unknown"]


@dataclass(frozen=True)
class CitationCandidate:
    """A bounded citation span extracted from one inspected paragraph.

    ``start`` and ``end`` use Python string offsets into the paragraph's
    available text.  ``raw_preview`` is deliberately bounded because the
    candidate is routinely copied into audit evidence.  Normalising the tuple
    and confidence here keeps callers from accidentally emitting non-JSON
    structures or out-of-range confidence values.
    """

    kind: CitationKind
    paragraph_id: str
    start: int
    end: int
    authors: tuple[str, ...]
    year: str | None
    page: str | None
    raw_preview: str
    confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", str(self.kind))
        object.__setattr__(self, "paragraph_id", str(self.paragraph_id))
        object.__setattr__(self, "start", max(0, int(self.start)))
        object.__setattr__(self, "end", max(0, int(self.end)))
        object.__setattr__(self, "authors", tuple(str(author) for author in self.authors))
        object.__setattr__(self, "year", str(self.year) if self.year is not None else None)
        object.__setattr__(self, "page", str(self.page) if self.page is not None else None)
        object.__setattr__(self, "raw_preview", str(self.raw_preview)[:80])
        object.__setattr__(self, "confidence", max(0.0, min(1.0, float(self.confidence))))

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible representation."""

        return {
            "kind": self.kind,
            "paragraph_id": self.paragraph_id,
            "start": self.start,
            "end": self.end,
            "authors": list(self.authors),
            "year": self.year,
            "page": self.page,
            "raw_preview": self.raw_preview,
            "confidence": self.confidence,
        }


__all__ = ["CitationCandidate", "CitationKind"]
