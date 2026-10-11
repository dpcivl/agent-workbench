"""CLI: ``python -m agent_workbench.contract validate [--type T] <path>...``

Exit codes: 0 all valid, 1 at least one file invalid, 2 usage or unreadable input.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

from . import validate as v

_VALIDATORS: dict[str, Callable[[Path], list[str]]] = {
    "events": v.validate_events_file,
    "project": v.validate_project,
    "review": v.validate_review,
    "workspace": v.validate_workspace,
}


def detect_type(path: Path) -> Optional[str]:
    """Guess the contract type from the file name / location."""
    name = path.name
    if re.fullmatch(r"events(-\d{4}-\d{2})?\.jsonl", name):
        return "events"
    if name in ("project.yaml", "project.yml"):
        return "project"
    if re.fullmatch(r"workspace(\.example)?\.ya?ml", name):
        return "workspace"
    if path.suffix == ".json" and path.parent.name == "reviews":
        return "review"
    return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent_workbench.contract")
    sub = parser.add_subparsers(dest="command", required=True)
    val = sub.add_parser("validate", help="validate contract files")
    val.add_argument("paths", nargs="+", type=Path)
    val.add_argument("--type", choices=sorted(_VALIDATORS), help="override type detection")
    args = parser.parse_args(argv)

    exit_code = 0
    for path in args.paths:
        kind = args.type or detect_type(path)
        if kind is None:
            print(f"ERROR {path}: cannot detect type; pass --type {{{','.join(sorted(_VALIDATORS))}}}")
            exit_code = max(exit_code, 2)
            continue
        errors = _VALIDATORS[kind](path)
        if errors:
            print(f"FAIL  {path} ({kind}): {len(errors)} error(s)")
            for message in errors:
                print(f"  - {message}")
            exit_code = max(exit_code, 1)
        else:
            print(f"OK    {path} ({kind})")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
