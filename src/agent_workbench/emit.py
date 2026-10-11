"""Record an orchestrator event in the project's events log.

    python3 -m agent_workbench.emit <event> [--gate ID] [--phase ID] [--summary TEXT]

Writes one line in the same format the hooks use, with agent="orchestrator".
Project dir: $CLAUDE_PROJECT_DIR, else the nearest ancestor of the current
directory that holds `.workbench/`, else the current directory.
"""
from __future__ import annotations

import argparse
import os
import sys

from agent_workbench.hooks import wbcommon as wb

EVENTS = ("gate_requested", "gate_approved", "change_requested", "change_approved")
GATE_REQUIRED = ("gate_requested", "gate_approved")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python3 -m agent_workbench.emit",
                                 description="Record an orchestrator event.")
    ap.add_argument("event", choices=EVENTS)
    ap.add_argument("--gate", help="gate id, e.g. dataset-selection (required for gate_* events)")
    ap.add_argument("--phase", help="phase id (default: first 'doing' phase in project.yaml)")
    ap.add_argument("--summary", help="short note, masked and capped at 200 chars")
    args = ap.parse_args(argv)

    if args.event in GATE_REQUIRED and not args.gate:
        ap.error(f"--gate is required for {args.event}")

    project = wb.project_dir()
    record = wb.build_event(
        args.event,
        session_id=os.environ.get("CLAUDE_SESSION_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID") or None,
        agent_id=None,
        agent="orchestrator",
        phase=args.phase or wb.current_phase(project),
        gate=args.gate,
        summary=wb.sanitize_text(args.summary),
        rev=wb.git_rev(project),
        usage=None,
    )
    try:
        path = wb.append_event(project, record)
    except OSError as exc:
        print(f"emit: cannot write events log: {exc}", file=sys.stderr)
        return 1
    print(f"{record['event']} -> {os.path.relpath(path, project)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
