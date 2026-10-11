"""Read-only data loader for the dashboard.

Pure Python (no streamlit). Nothing in this module writes to a project: it only
reads ``.workbench/`` files, ``results/<phase>/metrics.json`` and the file tree
needed to expand ``phases[].deliverables``.

Validation is delegated to ``agent_workbench.contract``; the loader is tolerant
on purpose (a viewer should show what it can) and reports problems as counts
and warning strings instead of failing.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import yaml

from agent_workbench.contract import (
    glob_matches,
    validate_event,
    validate_project,
    validate_review,
    validate_workspace,
)

WORKSPACE_ENV = "AGENT_WORKBENCH_WORKSPACE"
PROJECT_ENV = "AGENT_WORKBENCH_PROJECT"
DEFAULT_WORKSPACE = "~/.agent-workbench/workspace.yaml"

KNOWN_EVENTS = (
    "agent_start",
    "agent_stop",
    "agent_error",
    "gate_requested",
    "gate_approved",
    "change_requested",
    "change_approved",
)
PHASE_STATUSES = ("todo", "doing", "review", "blocked", "done")
METRIC_KEYS = ("model", "savings")  # the only top-level keys of metrics.json that are read

# Directories never worth descending into when expanding deliverable globs.
SKIP_DIRS = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", ".ipynb_checkpoints"})
MAX_SCAN_ENTRIES = 50_000
MAX_SUMMARY_BYTES = 200_000
MAX_SUMMARY_CHARS = 200  # the contract caps `summary` at the first 200 characters of a response
CHAPTER_GLOB = "docs/fieldbook/*.md"

_REVIEW_NAME = re.compile(r"^(?P<phase>.+)-(?P<n>\d+)\.json$")
_PHASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_GLOB_CHARS = "*?["


# --------------------------------------------------------------------------
# errors and path display
# --------------------------------------------------------------------------


class LoadError(Exception):
    """A problem that stops loading. ``code`` is machine readable; the UI words it in Korean."""

    def __init__(self, code: str, detail: str = "", errors: Iterable[str] = ()):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail
        self.errors = list(errors)


def display_path(path: "str | os.PathLike[str]", root: "Optional[Path]" = None) -> str:
    """A path safe to show: project-relative, or ``~/...``; never an absolute home path."""
    p = Path(path)
    if root is not None:
        try:
            return p.relative_to(root).as_posix() or "."
        except ValueError:
            pass
    try:
        return "~/" + p.relative_to(Path.home()).as_posix()
    except ValueError:
        pass
    return f".../{p.name}" if p.is_absolute() else str(p)


def mask_paths(text: str, root: "Optional[Path]" = None) -> str:
    """Defensively replace absolute project/home paths in free text."""
    out = text
    if root is not None and str(root) not in ("", "/"):
        out = out.replace(str(root), "<project>")
    home = str(Path.home())
    if home not in ("", "/"):
        out = out.replace(home, "~")
    return re.sub(r"(?:/Users|/home)/[^/\s]+", "~", out)


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectRef:
    id: str
    path: Path  # absolute project root
    declared: str  # as written in the registry (may start with ~)


@dataclass(frozen=True)
class Registry:
    source: Path
    projects: tuple[ProjectRef, ...]


def workspace_path(explicit: "Optional[str | os.PathLike[str]]" = None, environ: Optional[Mapping[str, str]] = None) -> Path:
    """Registry location: explicit argument > ``AGENT_WORKBENCH_WORKSPACE`` > default."""
    environ = os.environ if environ is None else environ
    raw = str(explicit) if explicit else (environ.get(WORKSPACE_ENV) or DEFAULT_WORKSPACE)
    return Path(raw).expanduser()


def load_registry(path: "Optional[str | os.PathLike[str]]" = None, environ: Optional[Mapping[str, str]] = None) -> Registry:
    source = workspace_path(path, environ)
    if not source.is_file():
        raise LoadError("registry_missing", display_path(source))
    errors = validate_workspace(source)
    if errors:
        raise LoadError("registry_invalid", display_path(source), errors)
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    refs = []
    for entry in data.get("projects") or []:
        declared = str(entry["path"])
        root = Path(declared).expanduser()
        if not root.is_absolute():  # relative paths are relative to the registry file
            root = source.parent / root
        refs.append(ProjectRef(id=str(entry["id"]), path=Path(os.path.abspath(root)), declared=declared))
    if not refs:
        raise LoadError("registry_empty", display_path(source))
    return Registry(source=source, projects=tuple(refs))


def single_project_ref(path: "str | os.PathLike[str]") -> ProjectRef:
    root = Path(os.path.abspath(Path(path).expanduser()))
    return ProjectRef(id=root.name, path=root, declared=str(path))


# --------------------------------------------------------------------------
# value objects
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    status: str
    agent: str


@dataclass(frozen=True)
class Phase:
    id: str
    name: str
    status: str
    agents: tuple[str, ...]
    deliverables: tuple[str, ...]
    gate: Optional[str]
    tasks: tuple[Task, ...]


@dataclass(frozen=True)
class DeliverableStatus:
    pattern: str
    is_glob: bool
    matches: tuple[str, ...]  # project-relative, sorted, possibly truncated by MAX_SCAN_ENTRIES
    latest_mtime: Optional[datetime]
    truncated: bool = False

    @property
    def match_count(self) -> int:
        return len(self.matches)

    @property
    def exists(self) -> bool:
        return self.match_count > 0


@dataclass(frozen=True)
class Usage:
    model: Optional[str]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    cache_creation_input_tokens: Optional[int]
    cache_read_input_tokens: Optional[int]

    @property
    def complete(self) -> bool:
        return None not in (
            self.input_tokens,
            self.output_tokens,
            self.cache_creation_input_tokens,
            self.cache_read_input_tokens,
        )

    @property
    def total_input(self) -> Optional[int]:
        """input + cache creation + cache read; None unless all three are known."""
        parts = (self.input_tokens, self.cache_creation_input_tokens, self.cache_read_input_tokens)
        return None if None in parts else sum(parts)  # type: ignore[arg-type]


@dataclass(frozen=True)
class Event:
    ts: datetime
    event: str
    session_id: Optional[str]
    agent_id: Optional[str]
    agent: Optional[str]
    phase: Optional[str]
    gate: Optional[str]
    usage: Optional[Usage]
    source: str = ""  # file name
    lineno: int = 0
    # Decision 11-2: `summary` (masked, single line, <= MAX_SUMMARY_CHARS) is carried ONLY for
    # `agent_error` events. For every other event kind it stays None, so the dashboard
    # cannot show response text from normal runs even by accident.
    summary: Optional[str] = None
    # Decision 12-1: True when `phase` was NOT recorded in the file (it was null) and was
    # attributed by the reader from the nearest earlier gate event of the same session.
    # Computed on read only, never written back; the UI must mark such values as inferred.
    phase_inferred: bool = False


@dataclass
class EventsReport:
    files: list[str] = field(default_factory=list)
    lines_read: int = 0
    events_loaded: int = 0
    skipped_malformed: int = 0  # not JSON / not an object
    skipped_invalid: int = 0  # JSON object, but unknown event kind or unusable ts
    schema_warnings: int = 0  # loaded, but validate_event reported problems
    warning_examples: list[str] = field(default_factory=list)

    @property
    def skipped(self) -> int:
        return self.skipped_malformed + self.skipped_invalid


@dataclass(frozen=True)
class Finding:
    category: str
    severity: str
    resolved: bool


@dataclass(frozen=True)
class Review:
    phase: str
    n: int
    verdict: Optional[str]  # None if the file could not be read
    findings: tuple[Finding, ...]
    counter_evidence_count: Optional[int]
    rel_path: str
    summary_rel_path: Optional[str]
    summary_text: Optional[str]
    errors: tuple[str, ...]  # contract validation problems, or read failure

    @property
    def unresolved(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if not f.resolved)

    @property
    def valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class Metrics:
    phase_id: str
    rel_path: str
    mtime: Optional[datetime]
    sections: Mapping[str, tuple[tuple[str, "int | float"], ...]]  # key -> (label, value) numeric entries
    present_keys: tuple[str, ...]  # which of METRIC_KEYS exist in the file
    error: Optional[str] = None


@dataclass(frozen=True)
class FileInfo:
    rel_path: str
    mtime: Optional[datetime]


@dataclass
class ProjectData:
    root: Path
    id: str
    name: str
    description: str
    agents: tuple[tuple[str, str], ...]
    phases: tuple[Phase, ...]
    deliverables: dict[str, tuple[DeliverableStatus, ...]]
    events: list[Event]
    events_report: EventsReport
    reviews: tuple[Review, ...]
    metrics: tuple[Metrics, ...]
    chapters: tuple[FileInfo, ...]
    warnings: list[str] = field(default_factory=list)

    def phase(self, phase_id: str) -> Optional[Phase]:
        return next((p for p in self.phases if p.id == phase_id), None)

    def latest_review(self, phase_id: str) -> Optional[Review]:
        mine = [r for r in self.reviews if r.phase == phase_id]
        return max(mine, key=lambda r: r.n) if mine else None


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

_FRACTION = re.compile(r"\.(\d+)")


def parse_ts(value: Any) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp that carries an offset; None if unusable."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    text = _FRACTION.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), text, count=1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _str_or_none(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None


def clean_summary(value: Any, root: "Optional[Path]" = None) -> Optional[str]:
    """An event ``summary`` made safe to show: absolute paths masked, control characters and
    line breaks collapsed to single spaces, capped at ``MAX_SUMMARY_CHARS``. ``None`` if unusable."""
    if not isinstance(value, str):
        return None
    text = " ".join(mask_paths(value, root).split())  # str.split() also folds \t \r \n U+2028 ...
    text = "".join(ch for ch in text if ch.isprintable())
    if not text:
        return None
    return text if len(text) <= MAX_SUMMARY_CHARS else text[: MAX_SUMMARY_CHARS - 1] + "…"


def _int_or_none(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _mtime(path: Path) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------

GATE_EVENTS = ("gate_requested", "gate_approved")


def infer_phases(events: Iterable[Event]) -> list[Event]:
    """Decision 12-1 (2): attribute null-phase events to a phase, marked as inferred.

    ``events`` must be in chronological order (file order breaks ties). An event whose
    ``phase`` is null takes the phase of the most recent EARLIER event of the SAME
    ``session_id`` that is a gate event (``gate_requested`` / ``gate_approved``) with a
    non-null phase. Rules:

    * a recorded (non-null) phase is never overwritten;
    * events without a ``session_id``, and events before any gate event of their
      session, stay null; a different session never inherits;
    * attributed events get ``phase_inferred=True``;
    * pure: new Event objects are returned, nothing is written to any file.

    Idempotent: a second pass finds no null phase that a first pass could have filled.
    """
    last_gate_phase: dict[str, str] = {}
    out: list[Event] = []
    for ev in events:
        sid = ev.session_id
        if ev.phase is None and sid is not None and sid in last_gate_phase:
            ev = replace(ev, phase=last_gate_phase[sid], phase_inferred=True)
        if ev.event in GATE_EVENTS and ev.phase is not None and sid is not None:
            last_gate_phase[sid] = ev.phase
        out.append(ev)
    return out


def _parse_usage(raw: Any) -> Optional[Usage]:
    if not isinstance(raw, dict):
        return None
    return Usage(
        model=_str_or_none(raw.get("model")),
        input_tokens=_int_or_none(raw.get("input_tokens")),
        output_tokens=_int_or_none(raw.get("output_tokens")),
        cache_creation_input_tokens=_int_or_none(raw.get("cache_creation_input_tokens")),
        cache_read_input_tokens=_int_or_none(raw.get("cache_read_input_tokens")),
    )


def load_events(root: Path) -> tuple[list[Event], EventsReport]:
    """Read every ``.workbench/events-*.jsonl``; skip (and count) unusable lines."""
    report = EventsReport()
    events: list[Event] = []
    wb = root / ".workbench"
    files = sorted(p for p in wb.glob("events-*.jsonl") if p.is_file()) if wb.is_dir() else []
    for path in files:
        report.files.append(path.name)
        try:
            text = path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            report.warning_examples.append(f"{path.name}: cannot read file")
            continue
        for lineno, line in enumerate(text.split("\n"), start=1):  # not splitlines(): U+2028 inside strings
            line = line.rstrip("\r")
            if not line.strip():
                continue
            report.lines_read += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                report.skipped_malformed += 1
                continue
            if not isinstance(obj, dict):
                report.skipped_malformed += 1
                continue
            ts = parse_ts(obj.get("ts"))
            kind = obj.get("event")
            if ts is None or kind not in KNOWN_EVENTS:
                report.skipped_invalid += 1
                continue
            problems = validate_event(obj)
            if problems:
                report.schema_warnings += 1
                if len(report.warning_examples) < 5:
                    report.warning_examples.append(f"{path.name}:{lineno}: {problems[0]}")
            events.append(
                Event(
                    ts=ts,
                    event=kind,
                    session_id=_str_or_none(obj.get("session_id")),
                    agent_id=_str_or_none(obj.get("agent_id")),
                    agent=_str_or_none(obj.get("agent")),
                    phase=_str_or_none(obj.get("phase")),
                    gate=_str_or_none(obj.get("gate")),
                    usage=_parse_usage(obj.get("usage")) if kind == "agent_stop" else None,
                    source=path.name,
                    lineno=lineno,
                    summary=clean_summary(obj.get("summary"), root) if kind == "agent_error" else None,
                )
            )
            report.events_loaded += 1
    events.sort(key=lambda e: (e.ts, e.source, e.lineno))
    return infer_phases(events), report


# --------------------------------------------------------------------------
# project.yaml and deliverables
# --------------------------------------------------------------------------


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(v) for v in value if isinstance(v, (str, int)) and str(v))


def _parse_phases(raw: Any) -> tuple[Phase, ...]:
    phases: list[Phase] = []
    if not isinstance(raw, list):
        return ()
    for item in raw:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        tasks = []
        for t in item.get("tasks") or []:
            if isinstance(t, dict) and t.get("id") is not None:
                tasks.append(
                    Task(
                        id=str(t.get("id")),
                        title=str(t.get("title") or ""),
                        status=str(t.get("status") or "todo"),
                        agent=str(t.get("agent") or ""),
                    )
                )
        phases.append(
            Phase(
                id=str(item["id"]),
                name=str(item.get("name") or item["id"]),
                status=str(item.get("status") or "todo"),
                agents=_strings(item.get("agents")),
                deliverables=_strings(item.get("deliverables")),
                gate=_str_or_none(item.get("gate")),
                tasks=tuple(tasks),
            )
        )
    return tuple(phases)


def _pattern_parts(pattern: str) -> list[str]:
    text = pattern.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return [p for p in text.split("/") if p not in ("", ".")]


def _collect(root: Path, prefix: list[str], max_depth: Optional[int]) -> tuple[list[str], bool]:
    """Project-relative POSIX paths (dirs and files) below ``root/prefix``."""
    base = root.joinpath(*prefix) if prefix else root
    if not base.is_dir():
        return [], False
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        rel_dir = Path(dirpath).relative_to(root)
        depth = len(rel_dir.parts) - len(prefix)  # 0 = directly inside base
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        if max_depth is not None and depth + 1 >= max_depth:
            names_dirs = list(dirnames)
            dirnames[:] = []  # do not descend further, but the dirs themselves are candidates
        else:
            names_dirs = list(dirnames)
        for name in names_dirs + sorted(filenames):
            out.append((rel_dir / name).as_posix())
            if len(out) >= MAX_SCAN_ENTRIES:
                return out, True
    return out, False


def resolve_deliverable(root: Path, pattern: str) -> DeliverableStatus:
    """Expand one declared deliverable (path or glob) against the real file tree."""
    parts = _pattern_parts(pattern)
    is_glob = any(c in part for part in parts for c in _GLOB_CHARS)
    if not parts or ".." in parts or pattern.startswith(("/", "~")):
        return DeliverableStatus(pattern, is_glob, (), None)  # unsafe: never touch the file system
    if not is_glob:
        target = root.joinpath(*parts)
        if target.exists():
            return DeliverableStatus(pattern, False, ("/".join(parts),), _mtime(target))
        return DeliverableStatus(pattern, False, (), None)
    literal: list[str] = []
    for part in parts:
        if any(c in part for c in _GLOB_CHARS):
            break
        literal.append(part)
    remaining = parts[len(literal):]
    max_depth = None if "**" in remaining else len(remaining)
    candidates, truncated = _collect(root, literal, max_depth)
    matches = sorted(glob_matches(pattern, candidates))
    mtimes = [m for m in (_mtime(root / m) for m in matches) if m is not None]
    return DeliverableStatus(pattern, True, tuple(matches), max(mtimes) if mtimes else None, truncated)


# --------------------------------------------------------------------------
# reviews, metrics, chapters
# --------------------------------------------------------------------------


def _read_text_capped(path: Path) -> Optional[str]:
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_SUMMARY_BYTES + 1)
    except OSError:
        return None
    text = data[:MAX_SUMMARY_BYTES].decode("utf-8", errors="replace")
    return text + "\n\n... (이하 생략)" if len(data) > MAX_SUMMARY_BYTES else text


def load_reviews(root: Path) -> tuple[Review, ...]:
    folder = root / ".workbench" / "reviews"
    if not folder.is_dir():
        return ()
    reviews: list[Review] = []
    for path in sorted(folder.glob("*.json")):
        rel = path.relative_to(root).as_posix()
        match = _REVIEW_NAME.match(path.name)
        phase = match["phase"] if match else path.stem
        n = int(match["n"]) if match else 0
        errors = tuple(validate_review(path))
        data: Any = None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if not isinstance(data, dict):
            reviews.append(Review(phase, n, None, (), None, rel, None, None, errors or ("cannot read review file",)))
            continue
        findings = tuple(
            Finding(
                category=str(f.get("category") or ""),
                severity=str(f.get("severity") or ""),
                resolved=f.get("resolved") is True,
            )
            for f in (data.get("findings") or [])
            if isinstance(f, dict)
        )
        md = path.with_suffix(".md")
        summary_rel = md.relative_to(root).as_posix() if md.is_file() else None
        summary_text = mask_paths(_read_text_capped(md) or "", root) if summary_rel else None
        # strip the path prefix of load errors: keep messages project-relative
        clean_errors = tuple(mask_paths(e, root) for e in errors)
        reviews.append(
            Review(
                phase=phase,
                n=n,
                verdict=_str_or_none(data.get("verdict")),
                findings=findings,
                counter_evidence_count=_int_or_none(data.get("counter_evidence_count")),
                rel_path=rel,
                summary_rel_path=summary_rel,
                summary_text=summary_text,
                errors=clean_errors,
            )
        )
    reviews.sort(key=lambda r: (r.phase, r.n))
    return tuple(reviews)


def numeric_entries(value: Any, prefix: str = "", depth: int = 0) -> list[tuple[str, "int | float"]]:
    """Numeric leaves of ``value`` (dicts are flattened to dotted labels, max 3 levels)."""
    if _is_number(value):
        return [(prefix, value)]
    out: list[tuple[str, "int | float"]] = []
    if isinstance(value, dict) and depth < 3:
        for key, sub in value.items():
            out.extend(numeric_entries(sub, f"{prefix}.{key}" if prefix else str(key), depth + 1))
    return out


def load_metrics(root: Path, phase_id: str) -> Optional[Metrics]:
    """``results/<phase-id>/metrics.json``; reads only the top-level ``model`` and ``savings`` keys."""
    if not _PHASE_ID.match(phase_id):
        return None
    path = root / "results" / phase_id / "metrics.json"
    if not path.is_file():
        return None
    rel = path.relative_to(root).as_posix()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return Metrics(phase_id, rel, _mtime(path), {}, (), error=f"cannot parse: {type(exc).__name__}")
    if not isinstance(data, dict):
        return Metrics(phase_id, rel, _mtime(path), {}, (), error="top level is not an object")
    sections: dict[str, tuple[tuple[str, "int | float"], ...]] = {}
    present: list[str] = []
    for key in METRIC_KEYS:
        if key in data:
            present.append(key)
            entries = numeric_entries(data[key], "")
            sections[key] = tuple((label or key, value) for label, value in entries)
    return Metrics(phase_id, rel, _mtime(path), sections, tuple(present))


def load_chapters(root: Path) -> tuple[FileInfo, ...]:
    folder = root / "docs" / "fieldbook"
    if not folder.is_dir():
        return ()
    files = [p for p in folder.glob("*.md") if p.is_file()]
    return tuple(FileInfo(p.relative_to(root).as_posix(), _mtime(p)) for p in sorted(files))


# --------------------------------------------------------------------------
# project
# --------------------------------------------------------------------------


def load_project(root: "str | os.PathLike[str]") -> ProjectData:
    root = Path(os.path.abspath(Path(root).expanduser()))
    if not root.is_dir():
        raise LoadError("project_dir_missing", display_path(root))
    yaml_path = root / ".workbench" / "project.yaml"
    if not yaml_path.is_file():
        raise LoadError("project_yaml_missing", ".workbench/project.yaml")
    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise LoadError("project_yaml_invalid", type(exc).__name__) from exc
    if not isinstance(raw, dict):
        raise LoadError("project_yaml_invalid", "top level is not a mapping")

    warnings = [mask_paths(e, root) for e in validate_project(yaml_path)]
    meta = raw.get("project") if isinstance(raw.get("project"), dict) else {}
    pid = str(meta.get("id") or root.name)
    agents = tuple(
        (str(a.get("id")), str(a.get("role") or ""))
        for a in (raw.get("agents") or [])
        if isinstance(a, dict) and a.get("id")
    )
    phases = _parse_phases(raw.get("phases"))
    deliverables = {p.id: tuple(resolve_deliverable(root, d) for d in p.deliverables) for p in phases}
    events, report = load_events(root)
    metrics = tuple(m for m in (load_metrics(root, p.id) for p in phases) if m is not None)
    return ProjectData(
        root=root,
        id=pid,
        name=str(meta.get("name") or pid),
        description=str(meta.get("description") or ""),
        agents=agents,
        phases=phases,
        deliverables=deliverables,
        events=events,
        events_report=report,
        reviews=load_reviews(root),
        metrics=metrics,
        chapters=load_chapters(root),
        warnings=warnings,
    )
