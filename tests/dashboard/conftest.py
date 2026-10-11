"""Shared helpers for the dashboard tests: demo project path and a synthetic project builder."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "examples" / "demo-project"


@pytest.fixture
def demo_path() -> Path:
    return DEMO


def event(ts: str, kind: str, agent: Optional[str] = "worker", agent_id: Optional[str] = None, phase: Optional[str] = "p1", gate: Optional[str] = None, usage: Any = None, **extra: Any) -> dict:
    """A contract-shaped event dict (timestamps are written with +00:00 for easy arithmetic)."""
    if agent_id is None and kind.startswith("agent_") and kind != "agent_error":
        agent_id = f"id-{agent}-{ts}"
    base = {
        "v": 1,
        "ts": ts,
        "event": kind,
        "session_id": "s1",
        "agent_id": agent_id,
        "agent": agent,
        "phase": phase,
        "gate": gate,
        "summary": None,
        "rev": None,
        "usage": usage,
    }
    base.update(extra)
    return base


def usage(model: str = "sonnet", i: int = 10, o: int = 20, cc: int = 100, cr: int = 1000) -> dict:
    return {
        "model": model,
        "input_tokens": i,
        "output_tokens": o,
        "cache_creation_input_tokens": cc,
        "cache_read_input_tokens": cr,
    }


def make_project(
    root: Path,
    phases: Optional[list[dict]] = None,
    events: Iterable[Any] = (),
    events_name: str = "events-2026-10.jsonl",
    files: Iterable[str] = (),
    project_id: str = "synthetic",
) -> Path:
    """Create a minimal project under ``root``. ``events`` items may be dicts or raw strings."""
    wb = root / ".workbench"
    wb.mkdir(parents=True, exist_ok=True)
    doc = {
        "project": {"id": project_id, "name": "Synthetic", "description": ""},
        "agents": [{"id": "worker", "role": "does things"}],
        "phases": phases
        if phases is not None
        else [
            {
                "id": "p1",
                "name": "Phase one",
                "status": "doing",
                "agents": ["worker"],
                "deliverables": [],
                "gate": "phase-end",
                "tasks": [],
            }
        ],
    }
    (wb / "project.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8")
    lines = [e if isinstance(e, str) else json.dumps(e) for e in events]
    if lines:
        (wb / events_name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    for rel in files:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")
    return root


def phase(pid: str = "p1", status: str = "doing", deliverables: Optional[list[str]] = None, **extra: Any) -> dict:
    return {
        "id": pid,
        "name": f"Phase {pid}",
        "status": status,
        "agents": ["worker"],
        "deliverables": deliverables or [],
        "gate": "phase-end",
        "tasks": [],
        **extra,
    }
