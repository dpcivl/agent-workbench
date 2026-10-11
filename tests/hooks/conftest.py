"""Fixtures for the hook tests."""
from __future__ import annotations

import pytest

from hooks_helpers import Project


@pytest.fixture()
def project(tmp_path) -> Project:
    root = tmp_path / "proj"
    plugin = tmp_path / "plugin"
    (root / ".workbench").mkdir(parents=True)
    (plugin / "agents").mkdir(parents=True)
    return Project(root.resolve(), plugin.resolve())
