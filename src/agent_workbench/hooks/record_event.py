#!/usr/bin/env python3
"""Event recorder hook: SubagentStart / SubagentStop / PostToolUseFailure(Agent).

Reads the hook payload from stdin and appends one JSON line to
<project>/.workbench/events-YYYY-MM.jsonl.

Opt-in: a complete no-op (exit 0, no output, nothing written, no directory
created) unless the project root holds `.workbench/project.yaml`
(see wbcommon.find_workbench_root).

Fail-soft: this hook never blocks or breaks the session. Any internal error
exits 0 after a best-effort note in <project>/.workbench/hook-errors.log
(error class + message only, never prompt or response text).

Deliberately does not import the `agent_workbench` package (only the sibling
wbcommon module) so a broken package __init__ can never disable recording.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wbcommon as wb  # noqa: E402

_EVENTS = {
    "SubagentStart": "agent_start",
    "SubagentStop": "agent_stop",
    "PostToolUseFailure": "agent_error",
}
_ARGV_ALIASES = {"start": "SubagentStart", "stop": "SubagentStop", "error": "PostToolUseFailure"}


def build(payload: dict, project: str):
    hook = payload.get("hook_event_name")
    if hook not in _EVENTS and len(sys.argv) > 1:
        hook = _ARGV_ALIASES.get(sys.argv[1], hook)
    event = _EVENTS.get(hook)
    if event is None:
        return None

    agent = payload.get("agent_type")
    summary = None
    usage = None
    if event == "agent_stop":
        summary = wb.sanitize_text(payload.get("last_assistant_message"))
        usage = wb.extract_usage(payload.get("agent_transcript_path"))
    elif event == "agent_error":
        if payload.get("tool_name") not in ("Agent", "Task"):
            return None
        tool_input = payload.get("tool_input")
        if isinstance(tool_input, dict) and tool_input.get("subagent_type"):
            agent = tool_input["subagent_type"]  # the agent that failed, not the caller
        summary = wb.sanitize_text(payload.get("error"))

    return wb.build_event(
        event,
        session_id=payload.get("session_id"),
        agent_id=payload.get("agent_id"),
        agent=agent,
        phase=wb.current_phase(project),
        summary=summary,
        rev=wb.git_rev(project),
        usage=usage,
    )


def main() -> int:
    project = None
    try:
        raw = sys.stdin.read()
        parse_exc = None
        try:
            payload = json.loads(raw) if raw.strip() else {}
            if not isinstance(payload, dict):
                raise ValueError("payload is not a JSON object")
        except Exception as exc:  # reported only inside a workbench project
            payload, parse_exc = {}, exc
        cwd = payload.get("cwd")
        project = wb.find_workbench_root(cwd if isinstance(cwd, str) else None)
        if project is None:
            return 0  # opt-in: not a workbench project, do nothing at all
        if parse_exc is not None:
            raise parse_exc
        record = build(payload, project)
        if record is not None:
            wb.append_event(project, record)
    except Exception as exc:  # fail-soft by design
        root = project or wb.find_workbench_root()
        if root:
            wb.log_error(root, "record_event", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
