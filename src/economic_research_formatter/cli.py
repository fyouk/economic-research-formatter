from __future__ import annotations

import argparse
import sys

from .rule_loader import load_rules, validate_rules


def main() -> int:
    parser = argparse.ArgumentParser(prog="er-format")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-rules", help="Validate rule files against the repository rule schema")
    sub.add_parser("list-rules", help="List all currently encoded rule IDs")
    args = parser.parse_args()

    if args.command == "validate-rules":
        errors = validate_rules()
        if errors:
            for error in errors:
                print(f"ERROR {error}")
            return 1
        rules = load_rules()
        print(f"OK: {len(rules)} rules validated")
        return 0

    if args.command == "list-rules":
        for rule in load_rules():
            print(f"{rule['id']}\t{rule['target']}\t{rule['normativity']}\t{rule['autofix']}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
