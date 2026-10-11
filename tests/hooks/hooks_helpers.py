"""Helpers for the hook tests. Hooks are always exercised through subprocess."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
HOOKS = SRC / "agent_workbench" / "hooks"
RECORD = HOOKS / "record_event.py"
GUARD = HOOKS / "write_guard.py"
SCRIPTS = REPO / "scripts"


def unix_home(name: str = "alice", tail: str = "proj/file.py") -> str:
    """A local-looking absolute path, built at runtime so this file stays clean."""
    return "/" + "Users" + "/" + name + "/" + tail


def clean_env(**extra) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CLAUDE_") and k not in ("WORKBENCH_SIMPLE_YAML", "PYTHONPATH")}
    env.update(extra)
    return env


class Project:
    def __init__(self, root: Path, plugin_root: Path):
        self.root = root
        self.plugin_root = plugin_root

    def env(self, **extra) -> dict:
        return clean_env(CLAUDE_PROJECT_DIR=str(self.root),
                         CLAUDE_PLUGIN_ROOT=str(self.plugin_root), **extra)

    def run(self, script: Path, payload, env_extra=None, raw=None):
        data = raw if raw is not None else json.dumps(payload)
        return subprocess.run(
            [sys.executable, str(script)], input=data, capture_output=True, text=True,
            env=self.env(**(env_extra or {})), cwd=str(self.root), timeout=30,
        )

    def events(self) -> list:
        out = []
        for f in sorted((self.root / ".workbench").glob("events-*.jsonl")):
            out += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line]
        return out

    def write(self, rel: str, text: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p


PHASES_YAML = """\
project:
  id: demo
  name: Demo # comment
agents:
  - id: data-engineer
    role: preprocessing
phases:
  - id: p1-research
    name: research
    status: done
    agents: [domain-researcher]
    tasks:
      - id: t1
        title: still marked doing inside a finished phase
        status: doing
        agent: domain-researcher
  - id: "p3-data"
    name: 'data prep'
    status: doing   # active
    agents: [data-engineer]
    tasks:
      - {id: t2, title: x, status: todo, agent: data-engineer}
  - id: p4-analysis
    status: doing
"""
