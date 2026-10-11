#!/usr/bin/env python3
"""Write-scope guard: PreToolUse hook for Write/Edit/NotebookEdit and Bash.

Policy (docs/DECISIONS.md sections 4 and 10, revision 1):
  * Protected paths are denied for everyone, main session included:
    data/raw/**, .workbench/events*.jsonl, .git/**
  * A subagent may write only inside the `write_scope` globs declared in its
    spec frontmatter (.claude/agents/<name>.md, else <plugin>/agents/<name>.md).
    No spec or no write_scope -> writes denied (fail closed).
  * Exception for every subagent, scoped or not: OS temp locations outside the
    project (tempfile.gettempdir(), /tmp, /private/tmp, /var/folders and the
    `scratchpad_dir` field of the hook payload). Protected-path rules still win,
    and a project that itself lives under a temp dir keeps its normal scope
    rules (the exemption only covers paths outside the project root).
  * The main session (no agent_type) is otherwise unrestricted.
  * Bash is BEST EFFORT only: it flags destructive verbs and redirects aimed
    at protected paths by looking at the command text. It cannot see what a
    script does internally. Reads are never blocked. Subagents (only) are
    additionally denied git push/commit and gh repo/pr create / release.

Deny = exit code 2 with the reason on stderr. Internal errors never block
(exit 0, logged to .workbench/hook-errors.log).
"""
from __future__ import annotations

import fnmatch
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wbcommon as wb  # noqa: E402

WRITE_TOOLS = ("Write", "Edit", "NotebookEdit")

# ----------------------------------------------------------------- frontmatter


def _split_frontmatter(text: str):
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return []
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return lines[1:i]
    return []


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        return v[1:-1]
    return re.split(r"\s+#", v, maxsplit=1)[0].strip().strip("'\"")


def parse_frontmatter_key(text: str, key: str):
    """Value of a frontmatter key as str or list[str]; None when absent. No PyYAML."""
    fm = _split_frontmatter(text)
    for i, line in enumerate(fm):
        m = re.match(rf"^{re.escape(key)}\s*:\s*(.*)$", line)
        if not m:
            continue
        rest = m.group(1).strip()
        if rest.startswith("["):
            inner = rest[1:rest.rfind("]")] if "]" in rest else rest[1:]
            return [_unquote(p) for p in inner.split(",") if _unquote(p)]
        if rest and not rest.startswith("#"):
            return _unquote(rest)
        items = []
        for nxt in fm[i + 1:]:
            if not nxt.strip() or nxt.lstrip().startswith("#"):
                continue
            im = re.match(r"^\s*-\s+(.*)$", nxt)
            if not im:
                break
            items.append(_unquote(im.group(1)))
        return items
    return None


def _spec_name(agent_type: str) -> str:
    return agent_type.rsplit(":", 1)[-1]


def find_spec(agent_type: str, project_root: str, plugin_root: str):
    """Path of the agent spec, or None. Project specs win over plugin specs.

    Looks for <name>.md first; the docs say agent_type is the frontmatter
    `name`, so a file whose frontmatter name matches is accepted as a fallback.
    """
    name = _spec_name(agent_type)
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name) or name.startswith("."):
        return None
    bases = (os.path.join(project_root, ".claude", "agents"),
             os.path.join(plugin_root, "agents"))
    for base in bases:
        cand = os.path.join(base, name + ".md")
        if os.path.isfile(cand):
            return cand
    for base in bases:
        try:
            files = sorted(f for f in os.listdir(base) if f.endswith(".md"))[:200]
        except OSError:
            continue
        for f in files:
            try:
                with open(os.path.join(base, f), "r", encoding="utf-8") as fh:
                    if parse_frontmatter_key(fh.read(), "name") == name:
                        return os.path.join(base, f)
            except OSError:
                continue
    return None


def load_scope(agent_type: str, project_root: str, plugin_root: str):
    """(spec_path_or_None, [globs])."""
    spec = find_spec(agent_type, project_root, plugin_root)
    if spec is None:
        return None, []
    try:
        with open(spec, "r", encoding="utf-8") as fh:
            value = parse_frontmatter_key(fh.read(), "write_scope")
    except OSError:
        return spec, []
    if isinstance(value, str):
        value = [value]
    return spec, [g for g in (value or []) if g]


# ----------------------------------------------------------------- path logic


def glob_to_regex(pattern: str):
    p = pattern.strip().replace("\\", "/")
    if p.startswith("./"):
        p = p[2:]
    if not p or p.startswith("/") or ".." in p.split("/"):
        return None  # absolute or escaping patterns never match
    if p.endswith("/"):
        p += "**"
    out, i = [], 0
    while i < len(p):
        if p.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif p.startswith("**", i):
            out.append(".*")
            i += 2
        elif p[i] == "*":
            out.append("[^/]*")
            i += 1
        elif p[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(p[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def in_scope(rel: str, globs) -> bool:
    for g in globs:
        rx = glob_to_regex(g)
        if rx is not None and rx.match(rel):
            return True
    return False


def _rel_to(root_real: str, real: str):
    """POSIX relative path of `real` under `root_real`, or None when outside."""
    try:
        rel = os.path.relpath(real, root_real)
    except ValueError:
        return None
    rel = rel.replace(os.sep, "/")
    if rel == ".." or rel.startswith("../"):
        return None
    return rel


def protected_reason(rel: str):
    """Name of the protected rule `rel` (project-relative, POSIX) falls under, or None."""
    cf = rel.casefold()  # macOS volumes are usually case-insensitive
    if cf == "data/raw" or cf.startswith("data/raw/"):
        return "data/raw/**"
    if fnmatch.fnmatchcase(cf, ".workbench/events*.jsonl"):
        return ".workbench/events*.jsonl"
    if ".git" in cf.split("/"):
        return ".git/**"
    return None


_FIXED_TEMP_ROOTS = ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders")


def temp_roots(payload: dict):
    """Real paths of the OS temp locations (plus the payload's scratchpad_dir)."""
    cands = [tempfile.gettempdir(), os.environ.get("TMPDIR"), *_FIXED_TEMP_ROOTS]
    sp = payload.get("scratchpad_dir")
    if isinstance(sp, str) and os.path.isabs(os.path.expanduser(sp)):
        cands.append(sp)
    roots = set()
    for c in cands:
        if isinstance(c, str) and c:
            real = os.path.realpath(os.path.expanduser(c))
            if real != os.sep:  # a bogus "/" must never turn into "everything is temp"
                roots.add(real)
    return roots


def _under(real: str, roots) -> bool:
    return any(real == r or real.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def _protected_anywhere(real: str):
    """Protected rule hit by an absolute path that is NOT inside the project.

    The project-relative rules are applied to every position of the path, so a
    second checkout under /tmp keeps its .git and data/raw protected.
    """
    parts = [p.casefold() for p in real.split(os.sep) if p]
    if ".git" in parts:
        return ".git/**"
    for i, part in enumerate(parts):
        if part == "data" and i + 1 < len(parts) and parts[i + 1] == "raw":
            return "data/raw/**"
        if part == ".workbench" and i + 1 < len(parts) and fnmatch.fnmatchcase(parts[i + 1], "events*.jsonl"):
            return ".workbench/events*.jsonl"
    return None


def decide_write(payload: dict, project_root: str, plugin_root: str):
    """Deny reason (str) or None to allow, for Write/Edit/NotebookEdit."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    target = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path")
    if not isinstance(target, str) or not target:
        return None

    base = payload.get("cwd") if isinstance(payload.get("cwd"), str) and payload.get("cwd") else project_root
    expanded = os.path.expanduser(target)
    if not os.path.isabs(expanded):
        expanded = os.path.join(base, expanded)
    real = os.path.realpath(expanded)
    root_real = os.path.realpath(project_root)
    rel = _rel_to(root_real, real)

    if rel is not None:
        rule = protected_reason(rel)
        if rule:
            return (f"[write-scope] 보호 경로입니다: {rel} (규칙 {rule}). "
                    "data/raw/**, .workbench/events*.jsonl, .git/** 는 누구도 직접 쓸 수 없습니다.")

    agent = payload.get("agent_type")
    if not agent:
        return None  # main session: unrestricted apart from protected paths

    shown = rel if rel is not None else target
    if rel is None and _under(real, temp_roots(payload)):
        # OS temp / scratchpad outside the project: allowed for scoped and unscoped agents,
        # unless the path itself looks like a protected location (another checkout's .git ...).
        rule = _protected_anywhere(real)
        if rule is None:
            return None
        return (f"[write-scope] 임시 폴더라도 보호 경로 규칙({rule})에 걸리는 경로는 쓸 수 없습니다: {shown}")

    spec, globs = load_scope(str(agent), project_root, plugin_root)
    if spec and globs:
        if rel is not None and in_scope(rel, globs):
            return None
        return (f"[write-scope] '{agent}' 에이전트는 '{shown}' 에 쓸 수 없습니다. "
                f"허용 범위: {', '.join(globs)} (+ OS 임시 폴더). 범위 밖 산출물은 메인 세션에 요청하세요.")

    # No spec, or no write_scope: fail closed.
    return (f"[write-scope] '{agent}' 에이전트의 write_scope를 찾지 못해 '{shown}' 쓰기를 차단합니다. "
            "에이전트 명세(.claude/agents/<name>.md 또는 플러그인 agents/)에 write_scope를 선언하세요.")


# ----------------------------------------------------------------- bash (best effort)

_SPLIT_RE = re.compile(r"&&|\|\||[;|\n`()]|(?<![<>])&(?![>&])")
_REDIRECT_RE = re.compile(r"(?:\d*|&)>>?\|?\s*(\S+)")
_REMOVERS = {"rm", "rmdir", "unlink", "shred"}
_MUTATORS = _REMOVERS | {"mv", "tee", "truncate", "touch"}
_BROAD_TARGETS = {"data", "data/*", "data/**", ".workbench", ".workbench/*", ".*"}
_PROTECTED_TOKEN_RES = (
    (re.compile(r"(?:^|/)data/raw(?:/|$|[*?\[])"), "data/raw"),
    (re.compile(r"(?:^|/)\.workbench/events"), ".workbench/events*"),
    (re.compile(r"(?:^|/)\.git(?:/|$)"), ".git"),
)
_GIT_OUT_RE = re.compile(
    r"(?:^|[\s;&|(`'\"/])git"
    r"(?:\s+(?:-[Cc]\s+\S+|--[\w-]+(?:=\S+)?|-\S+))*"
    r"\s+(push|commit)(?![\w-])"
)
_GH_OUT_RE = re.compile(r"(?:^|[\s;&|(`'\"/])gh\s+(?:repo\s+create|pr\s+create|release)(?![\w-])")
_SED_INPLACE_RE = re.compile(r"^(?:-[A-Za-z]*i|--in-place)")


def _protected_token(tok: str, root_real: str, root_raw: str, removal: bool):
    t = tok.strip("\"'")
    if not t:
        return None
    for root in (root_real, root_raw):
        if root and t.startswith(root.rstrip("/") + "/"):
            t = t[len(root.rstrip("/")) + 1:]
            break
    else:
        if t.startswith("/"):
            return None  # absolute path outside this project
    while t.startswith("./"):
        t = t[2:]
    cf = t.casefold().rstrip("/")
    if removal and cf in _BROAD_TARGETS:
        return cf
    for rx, name in _PROTECTED_TOKEN_RES:
        if rx.search(cf + "/"):
            return name
    return None


_WRAPPERS = {"sudo", "doas", "command", "builtin", "exec", "nohup", "time", "nice", "env", "xargs"}
_SHELLS = {"sh", "bash", "zsh", "dash"}


def _scan_command(cmd: str, root_real: str, project_root: str, depth: int = 0):
    for seg in _SPLIT_RE.split(cmd):
        for m in _REDIRECT_RE.finditer(seg):
            hit = _protected_token(m.group(1), root_real, project_root, removal=False)
            if hit:
                return _bash_deny(hit, ">")
        toks = [t.strip("\"'") for t in seg.split()]
        i = 0
        while i < len(toks) and (re.match(r"^\w+=", toks[i]) or toks[i].startswith("-")
                                 or os.path.basename(toks[i]) in _WRAPPERS):
            i += 1
        if i >= len(toks):
            continue
        verb = os.path.basename(toks[i])
        args = toks[i + 1:]
        if verb in _SHELLS and depth < 2 and "-c" in args:
            inner = " ".join(args[args.index("-c") + 1:])
            hit = _scan_command(inner, root_real, project_root, depth + 1)
            if hit:
                return hit
            continue
        positional = [a for a in args if not a.startswith("-")]
        if verb in _MUTATORS:
            targets = positional
        elif verb == "sed" and any(_SED_INPLACE_RE.match(a) for a in args):
            targets = positional
        elif verb == "cp":
            dest = None
            for j, a in enumerate(args):
                if a in ("-t", "--target-directory") and j + 1 < len(args):
                    dest = args[j + 1]
                elif a.startswith("--target-directory="):
                    dest = a.split("=", 1)[1]
            if dest is None and positional:
                dest = positional[-1]
            targets = [dest] if dest else []
        elif verb == "dd":
            targets = [a[3:] for a in args if a.startswith("of=")]
        else:
            continue
        for t in targets:
            hit = _protected_token(t, root_real, project_root, removal=verb in _REMOVERS)
            if hit:
                return _bash_deny(hit, verb)
    return None


def decide_bash(payload: dict, project_root: str):
    tool_input = payload.get("tool_input")
    cmd = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(cmd, str) or not cmd.strip():
        return None
    if payload.get("agent_type"):
        if _GIT_OUT_RE.search(cmd) or _GH_OUT_RE.search(cmd):
            return ("[bash-guard] 서브에이전트는 git push/commit, gh repo create, gh pr create, "
                    "gh release 를 실행할 수 없습니다. 메인 세션에서 사용자 승인 후 실행하세요.")
    return _scan_command(cmd, os.path.realpath(project_root), project_root)


def _bash_deny(target: str, verb: str) -> str:
    return (f"[bash-guard] 보호 경로({target})에 대한 쓰기/삭제 명령({verb})은 차단됩니다. "
            "읽기는 허용됩니다. (Bash 점검은 best-effort입니다.)")


# ----------------------------------------------------------------- entry


def decide(payload: dict):
    project_root = wb.project_dir(payload.get("cwd") if isinstance(payload.get("cwd"), str) else None)
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT") or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    tool = payload.get("tool_name")
    if tool in WRITE_TOOLS:
        return decide_write(payload, project_root, plugin_root)
    if tool == "Bash":
        return decide_bash(payload, project_root)
    return None


def main() -> int:
    project = None
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("payload is not a JSON object")
        project = wb.project_dir(payload.get("cwd") if isinstance(payload.get("cwd"), str) else None)
        reason = decide(payload)
    except Exception as exc:  # fail-soft: never block on our own bug
        wb.log_error(project or wb.project_dir(), "write_guard", exc)
        return 0
    if reason:
        sys.stderr.write(reason + "\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
