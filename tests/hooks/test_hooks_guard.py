from __future__ import annotations

import os

import pytest

from hooks_helpers import GUARD

DATA_ENGINEER = """\
---
name: data-engineer
description: pipeline work
tools: Read, Write, Edit, Bash
write_scope:
  - src/pipeline/**
  - "data/processed/**"   # processed output
  - docs/data/**
---
Body text with write_scope: ignored
"""

PM_TRACKER = """\
---
name: pm-tracker
write_scope: [".workbench/project.yaml", docs/reports/**]
---
"""

ANALYST = """\
---
name: energy-analyst
write_scope: notebooks/**
---
"""

NO_SCOPE = """\
---
name: freeform
tools: Read
---
"""


@pytest.fixture()
def gp(project):
    project.write(".claude/agents/data-engineer.md", DATA_ENGINEER)
    project.write(".claude/agents/energy-analyst.md", ANALYST)
    project.write(".claude/agents/freeform.md", NO_SCOPE)
    (project.plugin_root / "agents" / "pm-tracker.md").write_text(PM_TRACKER, encoding="utf-8")
    (project.root / "data" / "raw").mkdir(parents=True)
    return project


def call(p, tool, tool_input, agent=None, **extra):
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
               "session_id": "s", "cwd": str(p.root)}
    if agent:
        payload.update(agent_id="agent-1", agent_type=agent)
    payload.update(extra)
    return p.run(GUARD, payload)


def write(p, rel, agent=None, tool="Write", **kw):
    path = rel if os.path.isabs(rel) else str(p.root / rel)
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return call(p, tool, {key: path, "content": "x"}, agent, **kw)


def allowed(r):
    assert r.returncode == 0, r.stderr
    assert r.stdout == ""


def denied(r, *needles):
    assert r.returncode == 2, (r.returncode, r.stderr)
    for n in needles:
        assert n in r.stderr, (n, r.stderr)


# ---------------------------------------------------------------- Write / Edit / NotebookEdit

def test_scoped_agent_allowed_inside_scope(gp):
    allowed(write(gp, "src/pipeline/clean.py", "data-engineer"))
    allowed(write(gp, "src/pipeline/deep/er/x.py", "data-engineer", tool="Edit"))
    allowed(write(gp, "data/processed/out.parquet", "data-engineer"))


def test_scoped_agent_denied_outside_scope_with_clear_message(gp):
    r = write(gp, "docs/research/x.md", "data-engineer")
    denied(r, "data-engineer", "docs/research/x.md", "src/pipeline/**", "docs/data/**")


def test_plugin_namespaced_agent_uses_plugin_spec(gp):
    allowed(write(gp, ".workbench/project.yaml", "agent-workbench:pm-tracker"))
    allowed(write(gp, "docs/reports/weekly-1.md", "agent-workbench:pm-tracker"))
    denied(write(gp, "src/pipeline/x.py", "agent-workbench:pm-tracker"),
           "agent-workbench:pm-tracker", "docs/reports/**")


def test_project_spec_wins_over_plugin_spec(gp):
    gp.write(".claude/agents/pm-tracker.md", "---\nname: pm-tracker\nwrite_scope:\n  - only/here/**\n---\n")
    denied(write(gp, "docs/reports/w.md", "pm-tracker"))
    allowed(write(gp, "only/here/a.md", "pm-tracker"))


def test_notebook_edit_is_scoped(gp):
    allowed(write(gp, "notebooks/eda.ipynb", "energy-analyst", tool="NotebookEdit"))
    denied(write(gp, "src/eda.ipynb", "energy-analyst", tool="NotebookEdit"), "energy-analyst")


def test_dotdot_traversal_is_resolved(gp):
    r = write(gp, "src/pipeline/../../docs/research/x.md", "data-engineer")
    denied(r, "data-engineer")


def test_symlink_into_protected_path_is_denied(gp):
    (gp.root / "src" / "pipeline").mkdir(parents=True)
    os.symlink(gp.root / "data" / "raw", gp.root / "src" / "pipeline" / "evil")
    denied(write(gp, "src/pipeline/evil/a.csv", "data-engineer"), "data/raw")


def test_relative_path_resolves_against_cwd(gp):
    r = call(gp, "Write", {"file_path": "src/pipeline/a.py", "content": "x"}, "data-engineer")
    allowed(r)


def test_agent_without_spec_is_fail_closed(gp):
    denied(write(gp, "docs/x.md", "mystery-agent"), "mystery-agent", "write_scope")
    denied(write(gp, "docs/x.md", "freeform"), "freeform")  # spec without write_scope


def test_agent_type_cannot_traverse_to_a_spec(gp):
    denied(write(gp, "docs/x.md", "../../etc/passwd"))


def test_temp_allowed_for_scoped_and_unscoped_agents(gp, tmp_path):
    outside = tmp_path / "scratch.txt"          # inside the OS temp tree, outside the project
    allowed(write(gp, str(outside), "data-engineer"))                 # scoped agent
    allowed(write(gp, str(outside), "agent-workbench:pm-tracker"))    # scoped agent (plugin spec)
    allowed(write(gp, str(outside), "mystery-agent"))                 # no spec
    allowed(write(gp, str(outside), "freeform"))                      # spec without write_scope
    denied(write(gp, "docs/x.md", "mystery-agent"))                   # inside project: never temp-exempt


@pytest.mark.parametrize("path", ["/tmp/wb-guard-test.txt", "/private/tmp/wb-guard-test.txt",
                                  "/var/folders/zz/wb-guard-test.txt"])
def test_fixed_temp_roots_allowed_for_scoped_agent(gp, path):
    allowed(write(gp, path, "data-engineer"))


def test_project_inside_temp_keeps_scope_rules(gp):
    # the fixture project itself lives under the OS temp dir: the exemption must not leak into it
    denied(write(gp, "docs/research/x.md", "data-engineer"), "data-engineer")
    denied(write(gp, "docs/x.md", "mystery-agent"))
    allowed(write(gp, "src/pipeline/ok.py", "data-engineer"))


def test_scratchpad_dir_from_payload(gp):
    sp = "/opt/wb-scratchpad-test"              # deliberately not an OS temp location
    target = sp + "/n.md"
    denied(write(gp, target, "data-engineer"), "data-engineer")                       # without the field
    allowed(write(gp, target, "data-engineer", scratchpad_dir=sp))                   # scoped agent
    allowed(write(gp, target, "mystery-agent", scratchpad_dir=sp))                   # unscoped agent
    denied(write(gp, "/opt/elsewhere/n.md", "data-engineer", scratchpad_dir=sp))     # only that dir


def test_bogus_scratchpad_dir_is_ignored(gp):
    denied(write(gp, "/etc/wb-guard-test", "data-engineer", scratchpad_dir="/"))
    denied(write(gp, "/etc/wb-guard-test", "data-engineer", scratchpad_dir="relative/dir"))


def test_protected_paths_still_win_over_temp_exemption(gp, tmp_path):
    # project is under the temp tree, so these are both "temp" and "protected"
    denied(write(gp, "data/raw/a.csv", "data-engineer"), "보호 경로")
    denied(write(gp, ".workbench/events-2026-10.jsonl", "agent-workbench:pm-tracker"), "보호 경로")
    denied(write(gp, ".git/config", "data-engineer"), "보호 경로")
    # a second checkout in temp keeps its own protected locations too
    other = tmp_path / "other-checkout"
    denied(write(gp, str(other / ".git" / "config"), "data-engineer"), "보호 경로")
    denied(write(gp, str(other / "data" / "raw" / "a.csv"), "data-engineer"), "보호 경로")
    allowed(write(gp, str(other / "data" / "processed" / "a.csv"), "data-engineer"))
    # ...and a symlink in temp pointing into the project's data/raw
    link = tmp_path / "rawlink"
    os.symlink(gp.root / "data" / "raw", link)
    denied(write(gp, str(link / "x.csv"), "data-engineer"), "보호 경로")


def test_non_temp_out_of_scope_path_still_denied(gp, tmp_path):
    denied(write(gp, "/opt/not-temp/x.md", "data-engineer"), "data-engineer", "/opt/not-temp/x.md")
    # a symlink in temp that points at a non-temp location does not launder the write
    link = tmp_path / "etclink"
    os.symlink("/etc", link)
    denied(write(gp, str(link / "wb-guard-test"), "data-engineer"), "data-engineer")


def test_path_outside_project_denied_for_scoped_agent(gp):
    denied(write(gp, "/etc/hosts-workbench-test", "data-engineer"))


@pytest.mark.parametrize("rel", ["data/raw/a.csv", "data/raw/sub/b.csv", ".git/config",
                                 ".git/hooks/pre-push", ".workbench/events-2026-10.jsonl",
                                 "Data/Raw/c.csv"])
def test_protected_paths_denied_for_main_session(gp, rel):
    denied(write(gp, rel), "보호 경로")


@pytest.mark.parametrize("rel", ["data/raw/a.csv", ".workbench/events-2026-10.jsonl", ".git/HEAD"])
def test_protected_paths_denied_for_scoped_agents_too(gp, rel):
    denied(write(gp, rel, "agent-workbench:pm-tracker"), "보호 경로")


def test_main_session_otherwise_unrestricted(gp, tmp_path):
    allowed(write(gp, "docs/anything/x.md"))
    allowed(write(gp, "src/x.py", tool="Edit"))
    allowed(write(gp, str(tmp_path / "elsewhere.txt")))
    allowed(write(gp, ".workbench/project.yaml"))
    allowed(write(gp, ".gitignore"))
    allowed(write(gp, "data/raw_notes.md"))  # not data/raw/**


def test_fail_soft_on_garbage_and_unknown_tools(gp):
    r = gp.run(GUARD, None, raw="{{ not json")
    assert r.returncode == 0
    allowed(call(gp, "Read", {"file_path": str(gp.root / "data/raw/a.csv")}, "mystery-agent"))
    allowed(call(gp, "Write", {}, "mystery-agent"))


# ---------------------------------------------------------------- Bash (best effort)

def bash(p, cmd, agent=None):
    return call(p, "Bash", {"command": cmd}, agent)


READS_AND_SAFE = [
    "cat data/raw/a.csv",
    "ls -la data/raw",
    "head -n 5 data/raw/a.csv | wc -l",
    "python3 src/pipeline/clean.py --in data/raw --out data/processed",
    "cp data/raw/a.csv data/processed/a.csv",
    "rm data/processed/old.csv",
    "sed -n 1,3p data/raw/a.csv",
    "ls data/raw | tee out.txt",
    "echo ok 2>&1",
    "grep -rn cp data/raw",
    "cat .gitignore",
    "git status && git log --oneline",
    "mkdir -p data/processed && echo x > data/processed/ok.txt",
    "rm -rf build/",
]

WRITES_TO_PROTECTED = [
    "rm -rf data/raw",
    "rm data/raw/a.csv",
    "rm -rf data/*",
    "rm -rf .git",
    "mv data/raw/a.csv /somewhere/",
    "mv new.csv data/raw/a.csv",
    "cp new.csv data/raw/",
    "cp -t data/raw new.csv",
    "echo x > data/raw/a.csv",
    "echo x >> .workbench/events-2026-10.jsonl",
    "python3 gen.py >data/raw/out.csv",
    "sed -i s/a/b/ data/raw/a.csv",
    "sed -i.bak 's/a/b/' data/raw/a.csv",
    "tee data/raw/a.csv < in.csv",
    "echo hi | tee -a .workbench/events-2026-10.jsonl",
    "truncate -s 0 .workbench/events-2026-10.jsonl",
    "cd /tmp && rm data/raw/a.csv",
    "bash -c 'rm data/raw/a.csv'",
    "sudo rm -f .git/index",
    "dd if=/dev/zero of=data/raw/a.csv",
]


@pytest.mark.parametrize("cmd", READS_AND_SAFE)
@pytest.mark.parametrize("agent", [None, "data-engineer"])
def test_bash_reads_and_normal_writes_pass(gp, cmd, agent):
    allowed(bash(gp, cmd, agent))


@pytest.mark.parametrize("cmd", WRITES_TO_PROTECTED)
@pytest.mark.parametrize("agent", [None, "data-engineer"])
def test_bash_writes_to_protected_paths_denied(gp, cmd, agent):
    denied(bash(gp, cmd, agent), "bash-guard")


def test_bash_absolute_path_inside_project(gp):
    denied(bash(gp, f"rm {gp.root}/data/raw/a.csv"), "bash-guard")
    allowed(bash(gp, f"cat {gp.root}/data/raw/a.csv"))


# Documented blind spots (docs/hooks.md 3.5, DECISIONS section 10). These pass the Bash guard on
# (known limitation): if the guard ever learns to catch one, move it to WRITES_TO_PROTECTED and
# update the doc. Coverage today is the reviewer's change check.
KNOWN_BLIND_SPOTS = [
    "cd data/raw && rm a.csv",
    "find data/raw -name '*.csv' -delete",
    "git clean -fdx",
    "python3 -c \"import os; os.remove('data/raw/a.csv')\"",
    "d=data/raw; rm $d/a.csv",
]


@pytest.mark.parametrize("cmd", KNOWN_BLIND_SPOTS)
def test_documented_bash_blind_spots_are_not_detected(gp, cmd):
    allowed(bash(gp, cmd, "data-engineer"))


GIT_OUT = ["git push", "git push origin main", "git commit -m x", "git -C . push",
           "git -c user.name=x commit -m y", "cd x && git push --force", "gh repo create foo",
           "gh pr create --title t", "gh release create v1", "echo go; git push"]


@pytest.mark.parametrize("cmd", GIT_OUT)
def test_outward_git_denied_for_subagents_allowed_for_main(gp, cmd):
    denied(bash(gp, cmd, "data-engineer"), "서브에이전트")
    allowed(bash(gp, cmd))


@pytest.mark.parametrize("cmd", ["git status", "git diff HEAD", "git log -n 3", "git stash push",
                                 "gh pr list", "gh pr view 3", "gh repo view", "git add -A"])
def test_local_git_and_gh_reads_pass_for_subagents(gp, cmd):
    allowed(bash(gp, cmd, "data-engineer"))
