"""Fixtures for the hook tests."""
from __future__ import annotations

import pytest

from hooks_helpers import Project


@pytest.fixture()
def project(tmp_path) -> Project:
    root = tmp_path / "proj"
    plugin = tmp_path / "plugin"
    (root / ".workbench").mkdir(parents=True)
    # the hooks are opt-in: a workbench project is marked by .workbench/project.yaml
    (root / ".workbench" / "project.yaml").write_text("project:\n  id: demo\nphases: []\n",
                                                      encoding="utf-8")
    (plugin / "agents").mkdir(parents=True)
    return Project(root.resolve(), plugin.resolve())
