#!/usr/bin/env python3
"""Initialise an agent-workbench project (helper for the project-init skill).

    python3 init_project.py --id <project-id> --name "<name>" --root <project-root>
                            [--plugin-root <plugin-root>] [--workspace <workspace.yaml>]

What it does, in this order (standard library only):
  1. refuse (exit 3) when <root>/.workbench/project.yaml already exists; nothing is touched
  2. create <root>/.workbench/ and write project.yaml from the built-in template
  3. make sure <root>/.gitignore lists `.workbench/events*.jsonl` and `.workbench/*.log`
  4. register id + absolute path in ~/.agent-workbench/workspace.yaml (created if absent;
     no duplicates; a file it cannot edit safely is left alone and the snippet is printed)
  5. validate project.yaml (and workspace.yaml) with the contract validator through
     `uv run --no-project`; skipped with a message when uv or the plugin sources are missing

Exit codes: 0 done, 1 validation failed, 2 usage error, 3 project.yaml exists,
4 project created but workspace registration needs manual action.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
GITIGNORE_LINES = (".workbench/events*.jsonl", ".workbench/*.log")
GITIGNORE_HEADER = "# workbench runtime logs (never committed)"

TEMPLATE = """\
# .workbench/project.yaml: agent-workbench 데이터 계약 (docs/contract.md 3절)
# status 필드는 pm-tracker만 고친다. 그 밖의 필드는 프로젝트에 맞게 직접 편집한다.
project:
  id: {id}
  name: {name}
  description: ""
agents:
  - id: example-agent
    role: "예시 에이전트. 프로젝트의 단계 담당 에이전트로 교체한다 (.claude/agents 아래에 명세 필요)"
  - id: pm-tracker
    role: "이벤트와 산출물 기반 상태 갱신, 주간 보고"
  - id: reviewer
    role: "단계 종료 시 독립 검증, 결론마다 반대 증거 확인"
  - id: fieldbook-writer
    role: "승인된 단계의 챕터 초안 작성"
phases:
  - id: p1-example
    name: "예시 단계 (이름과 내용을 바꾼다)"
    status: todo
    agents:
      - example-agent
    deliverables:
      - docs/example.md
    gate: phase-end
    tasks:
      - id: p1-t1
        title: "예시 태스크"
        status: todo
        agent: example-agent
"""


def q(value: str) -> str:
    """YAML double-quoted scalar (JSON strings are valid YAML)."""
    return json.dumps(value, ensure_ascii=False)


def ensure_gitignore(root: Path) -> list[str]:
    path = root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    present = {line.strip() for line in existing.splitlines()}
    missing = [line for line in GITIGNORE_LINES if line not in present]
    if not missing:
        return []
    chunk = ""
    if existing and not existing.endswith("\n"):
        chunk += "\n"
    if existing.strip():
        chunk += "\n"
    chunk += GITIGNORE_HEADER + "\n" + "\n".join(missing) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(chunk)
    return missing


_ID_LINE = re.compile(r"^\s*-\s*id:\s*(.+?)\s*$")
_PATH_LINE = re.compile(r"^\s*path:\s*(.+?)\s*$")


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1]
    return s


def _real(p: str) -> str:
    return os.path.realpath(os.path.expanduser(p))


def register(workspace: Path, project_id: str, root: Path):
    """Return (state, detail). state in: created, appended, exists, conflict, manual."""
    entry = f"  - id: {project_id}\n    path: {q(str(root))}\n"
    snippet = f"projects:\n{entry}"
    if not workspace.exists():
        workspace.parent.mkdir(parents=True, exist_ok=True)
        with open(workspace, "x", encoding="utf-8") as fh:
            fh.write(snippet)
        return "created", ""

    text = workspace.read_text(encoding="utf-8")
    lines = text.splitlines()
    entries, cur = [], None
    for line in lines:
        m = _ID_LINE.match(line)
        if m:
            cur = {"id": _unquote(m.group(1)), "path": None}
            entries.append(cur)
            continue
        m = _PATH_LINE.match(line)
        if m and cur is not None and cur["path"] is None:
            cur["path"] = _unquote(m.group(1))
    for e in entries:
        same_path = e["path"] is not None and _real(e["path"]) == str(root)
        if e["id"] == project_id:
            if same_path:
                return "exists", ""
            return "conflict", f"id '{project_id}' is already registered with another path ({e['path']})"
        if same_path:
            return "conflict", f"this path is already registered under id '{e['id']}'"

    stripped = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    if not stripped:  # empty or comments only
        body = text if (not text or text.endswith("\n")) else text + "\n"
        body += snippet
    elif any(re.match(r"^projects:\s*\[\s*\]\s*$", ln) for ln in stripped) and len(stripped) == 1:
        new = [ln for ln in lines if not re.match(r"^projects:\s*\[\s*\]\s*$", ln)]
        new.append("projects:")
        body = "\n".join(new) + "\n" + entry
    elif any(re.match(r"^projects:\s*$", ln) for ln in stripped) and all(
        re.match(r"^projects:\s*$", ln) or re.match(r"^\s+(-\s*id:|path:)", ln) for ln in stripped
    ):
        body = text if text.endswith("\n") else text + "\n"
        body += entry
    else:
        return "manual", snippet
    tmp = workspace.with_name(workspace.name + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, workspace)
    return "appended", ""


def validate(plugin_root, files: list[Path]) -> tuple[str, int]:
    if not shutil.which("uv"):
        return "검증 생략: uv를 찾지 못했습니다. 수동 검증 명령은 skills/project-init/SKILL.md를 참고하세요.", 0
    if not plugin_root or not (Path(plugin_root) / "src" / "agent_workbench" / "contract").is_dir():
        return "검증 생략: 플러그인 소스(src/agent_workbench/contract)를 찾지 못했습니다.", 0
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(plugin_root) / "src")
    cmd = ["uv", "run", "--no-project", "--with", "pyyaml", "--with", "jsonschema",
           "python", "-m", "agent_workbench.contract", "validate", *map(str, files)]
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"검증 생략: 실행하지 못했습니다 ({type(exc).__name__}).", 0
    out = (proc.stdout + proc.stderr).strip()
    return out, proc.returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="init_project.py", add_help=True)
    ap.add_argument("--id", dest="pid")
    ap.add_argument("--name")
    ap.add_argument("--root")
    ap.add_argument("--plugin-root")
    ap.add_argument("--workspace")
    args = ap.parse_args(argv)

    usage = '사용법: /agent-workbench:project-init <project-id> "<name>"'
    if not args.pid or not args.name or not args.name.strip():
        print(usage)
        return 2
    if not ID_RE.match(args.pid):
        print(f"project-id가 올바르지 않습니다 (영문·숫자·_·-, 첫 글자는 영문 또는 숫자): {args.pid}")
        print(usage)
        return 2
    name = args.name.strip()
    if any(ord(c) < 32 for c in name):
        print("name에 제어 문자(줄바꿈 포함)를 쓸 수 없습니다.")
        print(usage)
        return 2

    root = Path(os.path.realpath(os.path.expanduser(args.root or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())))
    if not root.is_dir():
        print(f"프로젝트 루트를 찾지 못했습니다: {root}")
        return 2

    target = root / ".workbench" / "project.yaml"
    if target.exists():
        print(f"중단: {target.relative_to(root)} 가 이미 있습니다. 덮어쓰지 않았고 다른 파일도 바꾸지 않았습니다.")
        return 3

    done: list[str] = []
    (root / ".workbench").mkdir(exist_ok=True)
    with open(target, "x", encoding="utf-8") as fh:
        fh.write(TEMPLATE.format(id=args.pid, name=q(name)))
    done.append(f"만듦: {target.relative_to(root)}")

    added = ensure_gitignore(root)
    done.append("추가: .gitignore <- " + ", ".join(added) if added else ".gitignore: 이미 포함됨")

    ws = Path(os.path.expanduser(args.workspace)) if args.workspace else Path.home() / ".agent-workbench" / "workspace.yaml"
    state, detail = register(ws, args.pid, root)
    rc = 0
    if state == "created":
        done.append(f"만듦: {ws} (프로젝트 '{args.pid}' 등록)")
    elif state == "appended":
        done.append(f"등록: {ws} <- {args.pid}")
    elif state == "exists":
        done.append(f"{ws}: 이미 등록됨 ({args.pid})")
    elif state == "conflict":
        done.append(f"등록 보류: {detail}. {ws}는 바꾸지 않았습니다.")
        rc = 4
    else:
        done.append(f"등록 보류: {ws}의 형식을 안전하게 편집할 수 없어 바꾸지 않았습니다. 아래를 직접 추가하세요.")
        done.append(detail.rstrip("\n"))
        rc = 4

    print("\n".join(done))

    files = [target]
    if state in ("created", "appended", "exists"):
        files.append(ws)
    message, vrc = validate(args.plugin_root or os.environ.get("CLAUDE_PLUGIN_ROOT"), files)
    print("검증:\n" + message)
    if vrc != 0:
        return 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
