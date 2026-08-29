from __future__ import annotations

from importlib import resources
from pathlib import Path

from economic_research_formatter.rule_loader import project_root


RULE_FILES = (
    "schema.yaml",
    "manuscript.yaml",
    "citations.yaml",
    "references.yaml",
    "conflicts.yaml",
    "unresolved.yaml",
)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _profile_root() -> Path:
    traversable = resources.files(
        "economic_research_formatter.profiles.economic_research"
    )
    return Path(str(traversable))


def test_packaged_profile_is_byte_identical_to_maintained_rule_tree() -> None:
    repository = _repository_root()
    profile = _profile_root()

    for filename in RULE_FILES:
        assert (profile / "rules" / filename).read_bytes() == (
            repository / "rules" / filename
        ).read_bytes()
    assert (profile / "sources" / "source-index.yaml").read_bytes() == (
        repository / "sources" / "normalized" / "source-index.yaml"
    ).read_bytes()


def test_default_rule_root_is_the_packaged_profile() -> None:
    assert project_root().resolve() == _profile_root().resolve()
