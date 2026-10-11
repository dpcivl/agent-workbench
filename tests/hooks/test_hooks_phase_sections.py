"""The optional `review:` / `fieldbook:` sections must not leak into the phase result.

Both sections contain `- id:` list items that look like phase items to a line
based parser. The PyYAML path and the fallback parser (WORKBENCH_SIMPLE_YAML=1)
have to agree on the phase list and on the derived event `phase`.
"""
from __future__ import annotations

import importlib
import sys

import pytest
import yaml

from hooks_helpers import HOOKS, RECORD

REVIEW = """\
review:
  sources:
    - docs/research/**
  checks:
    - id: leakage
      text: 학습·검증 분리, 미래 정보 사용 여부를 점검한다
      phases: [p4-analysis, p5-forecast]
    - id: units
      text: >-
        단위와 시간대를 점검한다.
        phases: 같은 문장이 본문에 있어도 단계가 아니다.
      phases:
        - p3-data
        - p5-forecast
    - id: sources
      text: 모든 주장에 출처와 접근일이 있다   # no phases = every phase
fieldbook:
  title: Manufacturing AX Fieldbook
"""

HEAD = """\
project:
  id: demo
  name: Demo
  description: x
agents:
  - id: data-engineer
    role: preprocessing
  - id: reviewer
    role: review
"""

PHASES = """\
phases:
  - id: p3-data
    name: data
    status: done
    agents: [data-engineer]
    deliverables:
      - data/processed/*
    gate: phase-end
    tasks:
      - {id: t1, title: x, status: doing, agent: data-engineer}
  - id: p4-analysis
    name: analysis
    status: doing
    agents: [data-engineer]
    deliverables: []
    gate: phase-end
    tasks:
      - id: t2
        title: y
        status: todo
        agent: data-engineer
  - id: p5-forecast
    name: forecast
    status: review
    agents: [data-engineer]
    deliverables: []
    gate: phase-end
    tasks: []
"""

# the same document in every placement a user could reasonably choose
LAYOUTS = {
    "review-before-phases": HEAD + REVIEW + PHASES,
    "review-after-phases": HEAD + PHASES + REVIEW,
    "review-between-sections": HEAD.split("agents:")[0] + REVIEW + "agents:" + HEAD.split("agents:")[1] + PHASES,
    "comment-line-after-phases": HEAD + PHASES + "# optional sections\n" + REVIEW,
    "fieldbook-only-after-phases": HEAD + PHASES + "fieldbook:\n  title: Only A Title\n",
    "zero-indented-phase-items": HEAD + "phases:\n" + "\n".join(
        line[2:] if line.startswith("  ") else line for line in PHASES.splitlines()[1:]) + "\n" + REVIEW,
}
EXPECTED_PHASES = [("p3-data", "done"), ("p4-analysis", "doing"), ("p5-forecast", "review")]


def _wb(monkeypatch):
    monkeypatch.syspath_prepend(str(HOOKS))
    sys.modules.pop("wbcommon", None)
    return importlib.import_module("wbcommon")


def _pyyaml_phases(text: str) -> list:
    data = yaml.safe_load(text)
    return [(p["id"], p["status"]) for p in data["phases"]]


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
def test_fallback_parser_ignores_review_and_fieldbook_items(monkeypatch, layout):
    wb = _wb(monkeypatch)
    text = LAYOUTS[layout]
    assert _pyyaml_phases(text) == EXPECTED_PHASES  # fixture sanity: the YAML itself is what we think
    assert wb.phases_from_text(text) == EXPECTED_PHASES
    assert wb.phase_from_text(text) == "p4-analysis"


@pytest.mark.parametrize("layout", sorted(LAYOUTS))
@pytest.mark.parametrize("simple", [False, True], ids=["pyyaml", "fallback-parser"])
def test_event_phase_is_same_with_review_and_fieldbook_sections(project, layout, simple):
    project.write(".workbench/project.yaml", LAYOUTS[layout])
    env = {"WORKBENCH_SIMPLE_YAML": "1"} if simple else None
    payload = {"session_id": "s1", "hook_event_name": "SubagentStart",
               "agent_id": "agent-1", "agent_type": "agent-workbench:pm-tracker", "cwd": "."}
    project.run(RECORD, payload, env_extra=env)
    assert project.events()[0]["phase"] == "p4-analysis"


def test_sections_alone_yield_no_phase(monkeypatch):
    """A project.yaml with review items but no phases must not invent phases from `- id:` entries."""
    wb = _wb(monkeypatch)
    text = HEAD + REVIEW
    assert wb.phases_from_text(text) == []
    assert wb.phase_from_text(text) is None


def test_review_items_with_status_key_are_not_phases(monkeypatch):
    """Even a check that carries a `status:` line (invalid per schema) is outside `phases:`."""
    wb = _wb(monkeypatch)
    text = HEAD + REVIEW.replace("    - id: sources\n", "    - id: sources\n      status: doing\n") + PHASES
    assert wb.phases_from_text(text) == EXPECTED_PHASES
