"""Validators for the agent-workbench data contract.

Every ``validate_*`` function returns a list of human-readable error strings;
an empty list means the input is valid. JSON Schemas live in the repo-level
``schema/`` directory (bundled as ``contract/schema/`` in built wheels) and
hold the structural rules. Rules that a schema cannot express (references
between fields, path safety, timestamps) are implemented here.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Union

import yaml
from jsonschema import Draft202012Validator

Source = Union[str, Path, dict]

_MAX_MESSAGE = 200
_REVIEW_NAME = re.compile(r"^(?P<phase>.+)-(?P<n>\d+)\.json$")


# --------------------------------------------------------------------------
# schema loading and generic helpers
# --------------------------------------------------------------------------


def _schema_dir() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in (here / "schema", here.parents[2] / "schema"):
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError("agent-workbench schema directory not found")


@lru_cache(maxsize=None)
def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((_schema_dir() / f"{name}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _loc(parts) -> str:
    out = "$"
    for part in parts:
        out += f"[{part}]" if isinstance(part, int) else f".{part}"
    return out


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_MESSAGE else text[: _MAX_MESSAGE - 3] + "..."


def _schema_errors(name: str, obj: Any) -> list[str]:
    errors = sorted(_validator(name).iter_errors(obj), key=lambda e: (_loc(e.absolute_path), e.message))
    return [f"{_loc(e.absolute_path)}: {_clip(e.message)}" for e in errors]


def _load(source: Source, kind: str) -> tuple[Any, list[str]]:
    """Return (data, errors). ``source`` is an already-parsed dict or a file path."""
    if isinstance(source, dict):
        return source, []
    path = Path(source)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, [f"{path}: cannot read file: {exc.strerror or exc}"]
    try:
        data = yaml.safe_load(text) if kind == "yaml" else json.loads(text)
    except (yaml.YAMLError, json.JSONDecodeError) as exc:
        return None, [f"{path}: invalid {kind.upper()}: {exc}"]
    return data, []


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    dups: list[str] = []
    for value in values:
        if value in seen and value not in dups:
            dups.append(value)
        seen.add(value)
    return dups


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------

_FRACTION = re.compile(r"\.\d+")


def validate_event(obj: Any) -> list[str]:
    """Validate one event object (one line of events-YYYY-MM.jsonl)."""
    errors = _schema_errors("events", obj)
    ts = obj.get("ts") if isinstance(obj, dict) else None
    if isinstance(ts, str) and not any(e.startswith("$.ts:") for e in errors):
        try:  # the schema pattern checks the shape; this checks real calendar/clock values
            datetime.fromisoformat(_FRACTION.sub("", ts).replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"$.ts: {ts!r} is not a valid timestamp")
    return errors


def validate_events_file(path: Union[str, Path]) -> list[str]:
    """Validate a JSON Lines events file. Errors are prefixed with ``line N``.

    Blank lines are ignored. A truncated last line (interrupted append) is
    reported as invalid JSON like any other malformed line.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}: cannot read file: {exc.strerror or exc}"]
    errors: list[str] = []
    # split on "\n" only: str.splitlines() would also split on U+2028 inside strings
    for lineno, line in enumerate(text.split("\n"), start=1):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {lineno}: invalid JSON: {exc.msg} (column {exc.colno})")
            continue
        errors.extend(f"line {lineno}: {msg}" for msg in validate_event(obj))
    return errors


# --------------------------------------------------------------------------
# project.yaml
# --------------------------------------------------------------------------


def _bad_deliverable(path: str) -> str | None:
    if PurePosixPath(path).is_absolute() or PureWindowsPath(path).is_absolute() or path.startswith("~"):
        return "must be a project-relative path, not absolute"
    if ".." in PurePosixPath(path.replace("\\", "/")).parts:
        return "must not contain '..'"
    return None


def _project_semantic_errors(data: dict) -> list[str]:
    errors: list[str] = []
    agent_ids = [a["id"] for a in data["agents"]]
    known = set(agent_ids)

    for dup in _duplicates(agent_ids):
        errors.append(f"$.agents: duplicate agent id {dup!r}")
    for dup in _duplicates([p["id"] for p in data["phases"]]):
        errors.append(f"$.phases: duplicate phase id {dup!r}")

    doing = [p["id"] for p in data["phases"] if p["status"] == "doing"]
    if len(doing) > 1:
        errors.append(f"$.phases: at most one phase may be 'doing' (event.phase is derived from it); found {doing}")

    for pi, phase in enumerate(data["phases"]):
        base = f"$.phases[{pi}]"
        for ai, agent in enumerate(phase["agents"]):
            if agent not in known:
                errors.append(f"{base}.agents[{ai}]: unknown agent {agent!r} (not in top-level agents)")
        for di, path in enumerate(phase["deliverables"]):
            problem = _bad_deliverable(path)
            if problem:
                errors.append(f"{base}.deliverables[{di}]: {path!r} {problem}")
        for dup in _duplicates([t["id"] for t in phase["tasks"]]):
            errors.append(f"{base}.tasks: duplicate task id {dup!r}")
        for ti, task in enumerate(phase["tasks"]):
            if task["agent"] not in known:
                errors.append(f"{base}.tasks[{ti}].agent: unknown agent {task['agent']!r} (not in top-level agents)")
    return errors


def validate_project(source: Source) -> list[str]:
    """Validate .workbench/project.yaml (path or already-parsed dict)."""
    data, errors = _load(source, "yaml")
    if errors:
        return errors
    errors = _schema_errors("project", data)
    if not errors:  # semantic checks assume a structurally valid document
        errors = _project_semantic_errors(data)
    return errors


# --------------------------------------------------------------------------
# reviews/<phase>-<n>.json
# --------------------------------------------------------------------------


def _review_semantic_errors(data: dict) -> list[str]:
    """Rules a schema cannot express: when a ``pass`` verdict is allowed.

    ``needs_change`` is always allowed. ``pass`` needs at least one counter
    evidence and no unresolved ``high`` finding.
    """
    errors: list[str] = []
    if data["verdict"] != "pass":
        return errors
    if data["counter_evidence_count"] < 1:
        errors.append("$.verdict: 'pass' requires counter_evidence_count >= 1")
    for i, finding in enumerate(data["findings"]):
        if finding["severity"] == "high" and not finding["resolved"]:
            errors.append(
                f"$.findings[{i}]: 'pass' is not allowed while a high-severity finding "
                f"({finding['category']!r}) is unresolved"
            )
    return errors


def validate_review(source: Source) -> list[str]:
    """Validate a review file (path or already-parsed dict).

    Checks the schema, then the ``pass`` rules (see ``_review_semantic_errors``).
    With a path, ``phase``/``n`` (if present) must match ``<phase>-<n>.json``.
    """
    data, errors = _load(source, "json")
    if errors:
        return errors
    errors = _schema_errors("review", data)
    if errors:  # semantic checks assume a structurally valid document
        return errors
    errors = _review_semantic_errors(data)
    if isinstance(source, dict):
        return errors
    match = _REVIEW_NAME.match(Path(source).name)
    if not match:
        return errors + [f"{Path(source).name}: file name must be <phase>-<n>.json"]
    if "phase" in data and data["phase"] != match["phase"]:
        errors.append(f"$.phase: {data['phase']!r} does not match file name phase {match['phase']!r}")
    if "n" in data and data["n"] != int(match["n"]):
        errors.append(f"$.n: {data['n']!r} does not match file name iteration {match['n']!r}")
    return errors


# --------------------------------------------------------------------------
# workspace.yaml
# --------------------------------------------------------------------------


def validate_workspace(source: Source) -> list[str]:
    """Validate ~/.agent-workbench/workspace.yaml (path or already-parsed dict)."""
    data, errors = _load(source, "yaml")
    if errors:
        return errors
    errors = _schema_errors("workspace", data)
    if not errors:
        errors = [
            f"$.projects: duplicate project id {dup!r}"
            for dup in _duplicates([p["id"] for p in data["projects"]])
        ]
    return errors
