"""Shared helpers for the agent-workbench hooks and the emit CLI.

Standard library only (PyYAML is used when importable, never required).
Nothing in here may raise out of a public helper that a hook calls on its
hot path unless the docstring says so; hooks wrap everything in fail-soft
handlers anyway.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1
SUMMARY_MAX = 200

# Absolute local paths: /Users/.., /home/.., a few other system roots, C:\.. or C:/..
# The lookbehind keeps relative paths ("a/Users/x") and URLs ("https://") intact.
_PATH_RE = re.compile(
    r"(?<![\w.~/\\-])"
    r"(?:/(?:Users|home|root|tmp|var|private|opt|mnt|srv|Volumes)/[^\s\"'<>`)\],;]*"
    r"|[A-Za-z]:[\\/][^\s\"'<>`)\],;]*)"
)


# Secret / token shapes. Each one is replaced by <secret> (or keeps its key name).
_SECRET_RE = re.compile(
    # private key block: header through the END marker (or to the end of the text)
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----.*?"
    r"(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----|$)"
    # GitHub tokens
    r"|(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,})"
    # AWS access key ids (long-term AKIA, temporary ASIA)
    r"|(?<![A-Za-z0-9])(?:AKIA|ASIA)[0-9A-Z]{16}(?![A-Za-z0-9])"
    # sk- style API keys (>= 20 chars after the prefix; also sk-ant-..., sk-proj-...)
    r"|(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}",
    re.DOTALL,
)
_BEARER_RE = re.compile(r"\b(Bearer\s+)([A-Za-z0-9._~+/=-]{8,})", re.IGNORECASE)
_ASSIGN_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"((?:password|passwd|token|secret|api[_-]?key|access[_-]?key)[\"']?\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[\"']?[^\s\"',;]+)",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@"
    r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}(?![A-Za-z0-9-])"
)
_LONG_RUN_RE = re.compile(r"(?<![A-Za-z0-9_+/=-])[A-Za-z0-9_+/=-]{32,}(?![A-Za-z0-9_+/=-])")
_SEG_SPLIT_RE = re.compile(r"[_+/=-]+")
_WORDLIKE_RE = re.compile(r"[A-Za-z]+[0-9]{0,4}|[0-9]{1,8}")
_INPUT_CAP = 1_000_000  # characters examined at most; keeps masking fast on huge messages


def _bearer_sub(m: "re.Match") -> str:
    tok = m.group(2)
    # keep ordinary prose such as "Bearer authentication"
    if tok.isalpha() and len(tok) < 20 and (tok.islower() or tok.istitle()):
        return m.group(0)
    return m.group(1) + "<secret>"


def _assign_sub(m: "re.Match") -> str:
    if m.group(2).startswith("<secret>"):  # already masked: keeps the sanitizer idempotent
        return m.group(0)
    return m.group(1) + "<secret>"


def _long_run_sub(m: "re.Match") -> str:
    run = m.group(0)
    if not (any(c.isalpha() for c in run) and any(c.isdigit() for c in run)):
        return run
    segs = [x for x in _SEG_SPLIT_RE.split(run) if x]
    # snake_case / kebab-case identifiers and relative paths are made of short word-like
    # segments; random tokens are not. Keep the former.
    if len(segs) > 1 and all(_WORDLIKE_RE.fullmatch(x) for x in segs):
        return run
    return "<secret>"


def mask_text(text) -> str:
    """Mask absolute paths, e-mail addresses and secret-like strings. Never truncates."""
    s = str(text)[:_INPUT_CAP]
    s = _PATH_RE.sub("<path>", s)
    s = " ".join(s.split())
    s = _SECRET_RE.sub("<secret>", s)
    s = _BEARER_RE.sub(_bearer_sub, s)
    s = _ASSIGN_RE.sub(_assign_sub, s)
    s = _EMAIL_RE.sub("<email>", s)
    s = _LONG_RUN_RE.sub(_long_run_sub, s)
    return s


def sanitize_text(text, limit: int = SUMMARY_MAX):
    """Mask paths / e-mails / secrets, collapse whitespace, then cap length. Empty -> None.

    Masking happens BEFORE the cut so a secret straddling the limit cannot leak a prefix.
    Fail-soft: if masking itself fails, nothing is recorded (None), never the raw text.
    """
    if text is None:
        return None
    try:
        s = mask_text(text)
        if len(s) > limit:
            s = s[:limit]
        return s or None
    except Exception:
        return None


def now_iso() -> str:
    """Local time, ISO 8601 with UTC offset."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def project_dir(start: str | None = None) -> str:
    """CLAUDE_PROJECT_DIR, else the nearest ancestor holding .workbench/, else start/cwd."""
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return env
    base = Path(start or os.getcwd())
    for d in [base, *base.parents]:
        if (d / ".workbench").is_dir():
            return str(d)
    return str(base)


# --------------------------------------------------------------------------- phase

def _scalar(value: str) -> str:
    v = value.strip()
    if v[:1] in ("'", '"') and len(v) >= 2 and v[-1] == v[0]:
        return v[1:-1]
    return re.split(r"\s+#", v, maxsplit=1)[0].strip().strip("'\"")


_KV_RE = re.compile(r"^(\s*)(-\s+)?([A-Za-z_][\w-]*)\s*:\s*(.*)$")


def pick_phase(items):
    """Apply the `phase` rule to ``(id, status)`` pairs given in file order.

    1. the first phase whose status is `doing` (its id, which may be None for a
       malformed entry: `doing` always wins over `review`);
    2. otherwise the id of the phase in `review`, but ONLY when exactly one phase
       is in `review` (two or more is ambiguous and yields None);
    3. otherwise None.
    """
    pairs = [(pid, str(st or "").strip().lower()) for pid, st in items]
    for pid, st in pairs:
        if st == "doing":
            return None if pid is None else str(pid)
    reviews = [pid for pid, st in pairs if st == "review"]
    if len(reviews) == 1 and reviews[0] is not None:
        return str(reviews[0])
    return None


def phases_from_text(text: str) -> list:
    """Tolerant line parser for the known block-style `phases:` schema.

    Returns ``[(id | None, status), ...]`` in file order for every phase item that
    has its own `status`. Nested `tasks:` entries (deeper indentation) are ignored.
    """
    in_phases = False
    item_indent = None  # column of the '-' that starts a phase item
    key_col = None      # column of the keys that belong to the current phase item
    cur: dict = {}
    out: list = []

    def flush():
        if "status" in cur:
            out.append((cur.get("id") or None, cur["status"]))

    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if not in_phases:
            if indent == 0 and re.match(r"phases\s*:\s*(#.*)?$", raw):
                in_phases = True
            continue
        if indent == 0 and not stripped.startswith("-"):
            break  # next top-level key
        m = _KV_RE.match(raw)
        if not m:
            continue
        lead, dash, key, val = m.groups()
        if dash:
            if item_indent is None:
                item_indent = len(lead)
            if len(lead) != item_indent:
                continue  # nested list (tasks, deliverables, ...)
            flush()
            cur = {}
            key_col = len(lead) + len(dash)
        elif key_col is None or len(lead) != key_col:
            continue
        if key in ("id", "status"):
            cur[key] = _scalar(val)
    flush()
    return out


def phase_from_text(text: str):
    """Phase id for the event `phase` field from raw project.yaml text (fallback parser)."""
    return pick_phase(phases_from_text(text))


def current_phase(project: str):
    """Event `phase` derived from .workbench/project.yaml.

    The id of the first `doing` phase; if none is `doing`, the id of the single
    `review` phase (only when exactly one phase is in `review`); otherwise None.
    """
    try:
        text = (Path(project) / ".workbench" / "project.yaml").read_text(encoding="utf-8")
    except OSError:
        return None
    if not os.environ.get("WORKBENCH_SIMPLE_YAML"):
        try:
            import yaml  # type: ignore

            data = yaml.safe_load(text)
            items = []
            for ph in (data or {}).get("phases") or []:
                if isinstance(ph, dict):
                    pid = ph.get("id")
                    items.append((None if pid is None else str(pid), str(ph.get("status", ""))))
            return pick_phase(items)
        except Exception:  # ImportError, YAML syntax errors, odd shapes -> tolerant parser
            pass
    try:
        return phase_from_text(text)
    except Exception:
        return None


# --------------------------------------------------------------------------- git

def git_rev(project: str):
    """Short HEAD hash of the project, None outside a repo or without commits."""
    try:
        out = subprocess.run(
            ["git", "-C", project, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=3,
        )
        rev = out.stdout.strip()
        return rev if out.returncode == 0 and rev else None
    except Exception:
        return None


# --------------------------------------------------------------------------- usage

def _as_int(v) -> int:
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


_USAGE_FIELDS = (
    ("input_tokens", "input_tokens"),
    ("output_tokens", "output_tokens"),
    ("cache_creation_input_tokens", "cache_creation_input_tokens"),
    ("cache_read_input_tokens", "cache_read_input_tokens"),
)


def extract_usage(transcript_path):
    """Token usage of a subagent transcript, or None.

    Result: {model, input_tokens, output_tokens, cache_creation_input_tokens,
    cache_read_input_tokens}. input_tokens alone is misleading because most of
    the input is served from or written to the prompt cache, so both cache
    counters are reported next to it.

    The transcript repeats the same API request across several entries, so
    entries are de-duplicated by requestId (fallback message.id) first. Within
    one request the largest observed value of each counter wins (streaming
    updates only grow); the per-request values are then summed.
    Only entries with message.role == "assistant" count. Any failure -> None.
    """
    try:
        if not transcript_path:
            return None
        per_request: dict = {}
        with open(os.path.expanduser(str(transcript_path)), "r", encoding="utf-8") as fh:
            for n, line in enumerate(fh):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict):
                    continue
                msg = entry.get("message")
                if not isinstance(msg, dict) or msg.get("role") != "assistant":
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict):
                    continue
                key = entry.get("requestId") or msg.get("id") or ("line", n)
                rec = per_request.setdefault(key, {"model": None, **{k: 0 for k, _ in _USAGE_FIELDS}})
                model = msg.get("model")
                if isinstance(model, str) and model and not model.startswith("<"):
                    rec["model"] = model
                for out_key, src_key in _USAGE_FIELDS:
                    rec[out_key] = max(rec[out_key], _as_int(usage.get(src_key)))
        if not per_request:
            return None
        models = Counter(r["model"] for r in per_request.values() if r["model"])
        result = {"model": models.most_common(1)[0][0] if models else None}
        for out_key, _ in _USAGE_FIELDS:
            result[out_key] = sum(r[out_key] for r in per_request.values())
        return result
    except Exception:
        return None


# --------------------------------------------------------------------------- output

def build_event(event, *, session_id=None, agent_id=None, agent=None, phase=None,
                gate=None, summary=None, rev=None, usage=None) -> dict:
    return {
        "v": SCHEMA_VERSION,
        "ts": now_iso(),
        "event": event,
        "session_id": session_id,
        "agent_id": agent_id,
        "agent": agent,
        "phase": phase,
        "gate": gate,
        "summary": summary,
        "rev": rev,
        "usage": usage,
    }


def append_event(project: str, record: dict) -> Path:
    """Append one JSON line with a single O_APPEND write (safe for parallel writers)."""
    wb = Path(project) / ".workbench"
    wb.mkdir(parents=True, exist_ok=True)
    path = wb / f"events-{record['ts'][:7]}.jsonl"
    data = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return path


def log_error(project, hook: str, exc: BaseException) -> None:
    """Best-effort error log. Never records payload content, only the error class and message."""
    try:
        wb = Path(project) / ".workbench"
        wb.mkdir(parents=True, exist_ok=True)
        line = f"{now_iso()}\t{hook}\t{type(exc).__name__}: {str(exc)[:200]}\n"
        with open(wb / "hook-errors.log", "a", encoding="utf-8") as fh:
            fh.write(line.replace("\r", " "))
    except Exception:
        pass
