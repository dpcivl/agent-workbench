"""CLI: ``python -m agent_workbench.dashboard [--project PATH] [--workspace FILE] [-- STREAMLIT_ARGS]``.

Checks the inputs (Korean messages, no traceback), then replaces this process
with ``streamlit run`` on the bundled app. The server binds to 127.0.0.1 only
and usage statistics are switched off.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

from .loader import PROJECT_ENV, WORKSPACE_ENV, LoadError, load_registry, workspace_path
from .messages import describe_error

APP_PATH = Path(__file__).resolve().parent / "app.py"
EXIT_USAGE = 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agent_workbench.dashboard",
        description="agent-workbench 대시보드 (로컬 전용, 읽기 전용). "
        "`--` 뒤의 인자는 streamlit run에 그대로 전달합니다 (예: -- --server.port 8600).",
    )
    parser.add_argument("--project", metavar="PATH", help="등록부 없이 이 프로젝트 하나만 연다")
    parser.add_argument("--workspace", metavar="FILE", help="등록부 파일 (기본: ~/.agent-workbench/workspace.yaml)")
    return parser


def preflight(project: Optional[str], workspace: Optional[str], environ: "os._Environ[str] | dict") -> Optional[str]:
    """Return a Korean error message, or None if the inputs look usable."""
    try:
        if project:
            root = Path(project).expanduser()
            if not root.is_dir():
                raise LoadError("project_dir_missing", project)
            if not (root / ".workbench" / "project.yaml").is_file():
                raise LoadError("project_yaml_missing", ".workbench/project.yaml")
        else:
            load_registry(workspace, environ)
    except LoadError as exc:
        return describe_error(exc)
    return None


def build_launch(argv: Sequence[str], environ: Optional[dict] = None) -> "tuple[list[str], dict[str, str]] | str":
    """Return ``(command, env)``, or a Korean error message string."""
    argv = list(argv)
    extra: list[str] = []
    if "--" in argv:
        cut = argv.index("--")
        argv, extra = argv[:cut], argv[cut + 1 :]
    args = _parser().parse_args(argv)
    env = dict(os.environ if environ is None else environ)

    if importlib.util.find_spec("streamlit") is None:
        return "streamlit이 설치되어 있지 않습니다. `pip install 'agent-workbench[dashboard]'`로 설치하세요."
    problem = preflight(args.project, args.workspace, env)
    if problem:
        return problem

    # The streamlit process must be able to import this package.
    package_parent = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [package_parent, env.get("PYTHONPATH", "")]))
    env["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
    env.pop(PROJECT_ENV, None)
    if args.project:
        env[PROJECT_ENV] = str(Path(args.project).expanduser().resolve())
    if args.workspace:
        env[WORKSPACE_ENV] = str(workspace_path(args.workspace, env).resolve())

    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(APP_PATH),
        "--browser.gatherUsageStats",
        "false",
        "--server.address",
        "127.0.0.1",
        "--client.toolbarMode",
        "minimal",
        *extra,
    ]
    return command, env


def main(argv: Optional[Sequence[str]] = None, exec_fn: Callable = os.execvpe) -> int:
    result = build_launch(sys.argv[1:] if argv is None else argv)
    if isinstance(result, str):
        print(result, file=sys.stderr)
        return EXIT_USAGE
    command, env = result
    exec_fn(command[0], command, env)  # replaces the process; returns only in tests
    return 0


if __name__ == "__main__":
    sys.exit(main())
