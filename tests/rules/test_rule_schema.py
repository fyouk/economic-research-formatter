from economic_research_formatter.rule_loader import validate_rules


def test_rule_files_validate():
    assert validate_rules() == []
