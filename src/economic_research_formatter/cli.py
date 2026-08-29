from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from .docx.inspector import inspect_docx
from .lint.engine import lint_inspection
from .report.json_report import serialize_audit
from .report.markdown_report import render_markdown
from .rule_loader import load_rules, validate_rules, validate_rules_structured


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_json(
    payload: dict[str, Any], output: Path, *, protected_input: Path | None = None
) -> None:
    _write_text_atomic(_json_text(payload), output, protected_input=protected_input)


def _write_text_atomic(
    content: str, output: Path, *, protected_input: Path | None = None
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if protected_input is not None:
        _ensure_not_input_alias(protected_input, output)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if protected_input is not None:
            _ensure_not_input_alias(protected_input, output)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def _without_full_text(value: Any) -> Any:
    """Return a JSON-compatible copy without opt-in full-text fields."""

    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if key == "full_text":
                continue
            if key == "text" and "text_preview" in value:
                continue
            if key == "deleted_text" and "deleted_text_preview" in value:
                continue
            cleaned = _without_full_text(item)
            if key in {"text_preview", "deleted_text_preview"} and isinstance(
                cleaned, str
            ):
                cleaned = cleaned[:80]
            sanitized[key] = cleaned
        return sanitized
    if isinstance(value, list):
        return [_without_full_text(item) for item in value]
    return value


def _same_path(left: Path, right: Path) -> bool:
    return left.expanduser().resolve(strict=False) == right.expanduser().resolve(strict=False)


def _ensure_not_input_alias(input_path: Path, output_path: Path) -> None:
    same_file = _same_path(input_path, output_path)
    if not same_file and output_path.exists():
        try:
            same_file = os.path.samefile(input_path, output_path)
        except OSError:
            same_file = False
    if same_file:
        raise ValueError("DOCX input and report output resolve to the same file")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="er-format")
    sub = parser.add_subparsers(dest="command", required=True)
    validate_parser = sub.add_parser(
        "validate-rules", help="Validate rule files against the repository rule schema"
    )
    validate_parser.add_argument(
        "--json", action="store_true", dest="as_json", help="Emit machine-readable JSON"
    )
    sub.add_parser("list-rules", help="List all currently encoded rule IDs")
    inspect_parser = sub.add_parser("inspect", help="Inspect a DOCX without modifying it")
    inspect_parser.add_argument("input", type=Path, help="Input DOCX path")
    inspect_parser.add_argument("--output", type=Path, required=True, help="Inspection JSON path")
    inspect_parser.add_argument(
        "--include-text",
        action="store_true",
        help="Include full document text in inspection JSON (privacy-sensitive)",
    )
    inspect_parser.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)

    lint_parser = sub.add_parser("lint", help="Inspect and lint a DOCX without modifying it")
    lint_parser.add_argument("input", type=Path, help="Input DOCX path")
    lint_parser.add_argument("--output-dir", type=Path, required=True, help="Report directory")
    lint_parser.add_argument(
        "--include-text",
        action="store_true",
        help="Include full document text in inspection.json (privacy-sensitive)",
    )
    lint_parser.add_argument(
        "--exit-zero", action="store_true", help="Return zero even when ERROR findings exist"
    )
    lint_parser.add_argument("--debug", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.command == "validate-rules":
        if args.as_json:
            result = validate_rules_structured()
            print(_json_text(result), end="")
            return 0 if result["valid"] else 1
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

    try:
        if args.command == "inspect":
            _ensure_not_input_alias(args.input, args.output)
            inspection = inspect_docx(args.input, include_text=args.include_text)
            _write_json(inspection, args.output, protected_input=args.input)
            return 0

        if args.command == "lint":
            # Linting needs full paragraph text for high-confidence citation
            # candidates.  It remains in memory unless the user explicitly
            # opts in to full text in inspection.json.
            analysis_inspection = inspect_docx(args.input, include_text=True)
            audit = lint_inspection(analysis_inspection)
            persisted_inspection = (
                analysis_inspection
                if args.include_text
                else _without_full_text(analysis_inspection)
            )
            output_dir: Path = args.output_dir
            inspection_output = output_dir / "inspection.json"
            audit_output = output_dir / "audit.json"
            markdown_output = output_dir / "audit.md"
            for output in (inspection_output, audit_output, markdown_output):
                _ensure_not_input_alias(args.input, output)
            _write_json(
                persisted_inspection,
                inspection_output,
                protected_input=args.input,
            )
            _write_text_atomic(
                serialize_audit(audit), audit_output, protected_input=args.input
            )
            _write_text_atomic(
                render_markdown(audit), markdown_output, protected_input=args.input
            )
            error_count = int(audit.get("summary", {}).get("by_status", {}).get("ERROR", 0))
            return 0 if args.exit_zero or error_count == 0 else 1
    except Exception as exc:
        if getattr(args, "debug", False):
            raise
        print(f"ERROR DOCX: {exc}", file=sys.stderr)
        return 2

    return 2


if __name__ == "__main__":
    sys.exit(main())
