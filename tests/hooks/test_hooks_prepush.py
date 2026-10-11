from __future__ import annotations

import subprocess

import pytest

from hooks_helpers import SCRIPTS, clean_env, unix_home

CHECK = SCRIPTS / "pre-push-check.sh"
INSTALL = SCRIPTS / "install-pre-push.sh"

GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]


def sh(args, cwd, **kw):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=clean_env(), timeout=60, **kw)


def git(repo, *args):
    r = sh([*GIT[:7], *args], repo)
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.fixture()
def repo(tmp_path):
    d = tmp_path / "repo"
    d.mkdir()
    git(d, "init", "-q")
    (d / "README.md").write_text("# demo\nnothing to see\n")
    (d / "src").mkdir()
    (d / "src" / "a.py").write_text("print('hi')\n")
    return d


def commit(repo, msg="c"):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)


def check(repo):
    return sh(["bash", str(CHECK), str(repo)], repo)


def test_clean_repo_passes(repo):
    commit(repo)
    r = check(repo)
    assert r.returncode == 0, r.stderr
    assert "clean" in r.stdout


def test_repo_without_commits_is_not_an_error(repo):
    assert check(repo).returncode == 0


def test_local_absolute_path_is_flagged(repo):
    (repo / "src" / "a.py").write_text(f"ROOT = '{unix_home('someone', 'data')}'\n")
    commit(repo)
    r = check(repo)
    assert r.returncode == 1
    assert "src/a.py:1" in r.stderr and "local-path" in r.stderr


def test_home_path_is_flagged(repo):
    (repo / "notes.md").write_text("see /" + "home" + "/bob/x\n")
    commit(repo)
    assert check(repo).returncode == 1


def test_token_is_flagged_and_never_printed(repo):
    token = "ghp_" + "A1b2C3d4E5" * 4
    (repo / "src" / "cfg.py").write_text(f"TOKEN = '{token}'\n")
    commit(repo)
    r = check(repo)
    assert r.returncode == 1
    assert "secret" in r.stderr and "src/cfg.py:1" in r.stderr
    assert token not in r.stdout + r.stderr


@pytest.mark.parametrize("line", [
    "-----BEGIN " + "RSA PRIVATE KEY-----",
    "key = 'AKIA" + "ABCDEFGHIJKLMNOP'",
    "client = Client('sk-" + "ant-api03-abcdefghijklmnopqrstuvwxyz')",
    "github_pat_" + "11ABCDEFG0123456789_abcdefghij",
    "pass" + "word = 'hunter22'",
    "DB_PASS" + "WORD=hunter22extra",
])
def test_secret_patterns_flagged(repo, line):
    (repo / "x.txt").write_text(line + "\n")
    commit(repo)
    assert check(repo).returncode == 1


def test_secret_lookalikes_pass(repo):
    (repo / "x.md").write_text("risk-assessment-framework-final-version\n"
                               "password = get_password()\n"
                               "use the password field in the form\n")
    commit(repo)
    assert check(repo).returncode == 0


def test_email_flagged_but_noreply_and_example_allowed(repo):
    (repo / "ok.md").write_text("12345+user@users.noreply.github.com\nsomebody@example.com\n")
    commit(repo)
    assert check(repo).returncode == 0
    (repo / "bad.md").write_text("contact: jane.doe@" + "corp-mail.io\n")
    commit(repo)
    r = check(repo)
    assert r.returncode == 1 and "email" in r.stderr and "bad.md:1" in r.stderr


def test_author_email_is_not_scanned(repo):
    commit(repo)  # author is t@example.com anyway; use a real-looking one
    (repo / "b.txt").write_text("x\n")
    git(repo, "add", "-A")
    sh(["git", "-c", "user.name=t", "-c", "user.email=real.person@" + "corp-mail.io",
        "commit", "-q", "-m", "m"], repo)
    assert check(repo).returncode == 0


@pytest.mark.parametrize("rel", [".env", ".env.local", "data/raw/a.csv", "data/processed/x.parquet",
                                 "docs/paper.pdf", "docs/PAPER.PDF"])
def test_forbidden_tracked_files(repo, rel):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("x\n")
    git(repo, "add", "-f", "-A")
    git(repo, "commit", "-q", "-m", "c")
    r = check(repo)
    assert r.returncode == 1 and "forbidden" in r.stderr and rel in r.stderr


def test_data_readme_and_env_lookalikes_allowed(repo):
    (repo / "data").mkdir()
    (repo / "data" / "README.md").write_text("download instructions\n")
    (repo / "envrc.md").write_text("x\n")
    commit(repo)
    assert check(repo).returncode == 0


def test_untracked_and_uncommitted_files_are_not_scanned(repo):
    commit(repo)
    (repo / "later.py").write_text(f"P='{unix_home()}'\n")
    assert check(repo).returncode == 0


def test_prepush_allow_exceptions(repo):
    (repo / "docs").mkdir()
    (repo / "docs" / "sample.md").write_text(f"example: {unix_home('someone', 'x')}\n")
    (repo / ".env.example").write_text("A=1\n")
    commit(repo)
    assert check(repo).returncode == 1
    (repo / ".prepush-allow").write_text("# intentional\n^docs/sample\\.md:\n^\\.env\\.example$\n\n")
    commit(repo)
    r = check(repo)
    assert r.returncode == 0, r.stderr


def test_install_hook_blocks_real_push(repo, tmp_path):
    remote = tmp_path / "remote.git"
    sh(["git", "init", "-q", "--bare", str(remote)], tmp_path)
    git(repo, "remote", "add", "origin", str(remote))
    r = sh(["bash", str(INSTALL), str(repo)], tmp_path)
    assert r.returncode == 0, r.stderr
    hook = repo / ".git" / "hooks" / "pre-push"
    assert hook.is_file() and hook.stat().st_mode & 0o111

    commit(repo, "clean")
    ok = sh(["git", "push", "-q", "origin", "HEAD:main"], repo)
    assert ok.returncode == 0, ok.stderr

    (repo / "leak.py").write_text("T = 'AKIA" + "ABCDEFGHIJKLMNOP'\n")
    commit(repo, "leak")
    bad = sh(["git", "push", "-q", "origin", "HEAD:main"], repo)
    assert bad.returncode != 0 and "blocked" in bad.stderr
    forced = sh(["git", "push", "-q", "--no-verify", "origin", "HEAD:main"], repo)
    assert forced.returncode == 0


def test_install_never_overwrites_existing_hook(repo):
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho mine\n")
    r = sh(["bash", str(INSTALL), str(repo)], repo)
    assert r.returncode == 1 and "not overwriting" in r.stderr
    assert hook.read_text() == "#!/bin/sh\necho mine\n"


def test_install_is_idempotent_and_validates_target(repo, tmp_path):
    assert sh(["bash", str(INSTALL), str(repo)], repo).returncode == 0
    again = sh(["bash", str(INSTALL), str(repo)], repo)
    assert again.returncode == 0 and "already installed" in again.stdout
    notrepo = tmp_path / "plain"
    notrepo.mkdir()
    assert sh(["bash", str(INSTALL), str(notrepo)], tmp_path).returncode == 1


# ---------------------------------------------------------------- pushed commit range

ZERO = "0" * 40
AWS = "AKIA" + "ABCDEFGHIJKLMNOP"


def rev(repo, ref="HEAD"):
    return git(repo, "rev-parse", ref).strip()


def push_line(local, remote=ZERO, lref="refs/heads/main", rref="refs/heads/main"):
    return f"{lref} {local} {rref} {remote}\n"


def check_push(repo, *lines):
    """Run the checker the way git does: ref lines on stdin."""
    return sh(["bash", str(CHECK), "origin", "url"], repo, input="".join(lines))


@pytest.fixture()
def pushed(repo):
    """repo with one clean base commit; returns (repo, base_sha)."""
    commit(repo, "base")
    return repo, rev(repo)


def add_then_remove(repo, rel, text):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    git(repo, "add", "-f", "-A")
    git(repo, "commit", "-q", "-m", f"add {rel}")
    added = rev(repo)
    git(repo, "rm", "-q", "-f", rel)
    git(repo, "commit", "-q", "-m", f"remove {rel}")
    return added


def test_secret_added_then_removed_in_range_fails_while_head_is_clean(pushed):
    repo, base = pushed
    added = add_then_remove(repo, "src/cfg.py", f"KEY = '{AWS}'\n")
    assert check(repo).returncode == 0                       # HEAD alone looks clean
    r = check_push(repo, push_line(rev(repo), base))
    assert r.returncode == 1, r.stdout + r.stderr
    assert "secret" in r.stderr and "src/cfg.py:1" in r.stderr
    assert added[:12] in r.stderr                            # points at the offending commit
    assert AWS not in r.stdout + r.stderr


def test_clean_range_passes(pushed):
    repo, base = pushed
    (repo / "src" / "b.py").write_text("print('more')\n")
    commit(repo, "clean 1")
    (repo / "README.md").write_text("# demo\nedited\n")
    commit(repo, "clean 2")
    r = check_push(repo, push_line(rev(repo), base))
    assert r.returncode == 0, r.stderr
    assert "2 pushed commit" in r.stdout


def test_already_pushed_history_is_not_rescanned(repo):
    (repo / "old.py").write_text(f"KEY = '{AWS}'\n")
    commit(repo, "leak, assumed already on the remote")
    old = rev(repo)
    git(repo, "rm", "-q", "old.py")
    commit(repo, "remove")
    (repo / "new.py").write_text("x = 1\n")
    commit(repo, "clean")
    r = check_push(repo, push_line(rev(repo), old))   # range is old..HEAD
    assert r.returncode == 0, r.stderr


def test_new_branch_without_any_remote_scans_whole_history(repo):
    add_then_remove(repo, "a.py", f"KEY = '{AWS}'\n")
    r = check_push(repo, push_line(rev(repo), ZERO))
    assert r.returncode == 1 and "a.py:1" in r.stderr


def test_new_branch_only_scans_commits_no_remote_has(repo, tmp_path):
    remote = tmp_path / "remote.git"
    sh(["git", "init", "-q", "--bare", str(remote)], tmp_path)
    git(repo, "remote", "add", "origin", str(remote))
    add_then_remove(repo, "old.py", f"KEY = '{AWS}'\n")     # history the remote already has
    git(repo, "push", "-q", "origin", "HEAD:refs/heads/main")
    git(repo, "fetch", "-q", "origin")
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "feat.py").write_text("x = 1\n")
    commit(repo, "feature work")
    ok = check_push(repo, push_line(rev(repo), ZERO, "refs/heads/feature", "refs/heads/feature"))
    assert ok.returncode == 0, ok.stderr
    assert "1 pushed commit" in ok.stdout
    add_then_remove(repo, "feat2.py", f"KEY = '{AWS}'\n")
    bad = check_push(repo, push_line(rev(repo), ZERO, "refs/heads/feature", "refs/heads/feature"))
    assert bad.returncode == 1 and "feat2.py:1" in bad.stderr


@pytest.mark.parametrize("rel", [".env", "config/.env.local", "data/raw/a.csv", "data/processed/x.parquet",
                                 "docs/paper.pdf"])
def test_forbidden_file_added_then_deleted_in_range_fails(pushed, rel):
    repo, base = pushed
    add_then_remove(repo, rel, "x\n")
    assert check(repo).returncode == 0
    r = check_push(repo, push_line(rev(repo), base))
    assert r.returncode == 1 and "forbidden" in r.stderr and rel in r.stderr


def test_forbidden_lookalikes_in_range_pass(pushed):
    repo, base = pushed
    add_then_remove(repo, "data/README.md", "how to download\n")
    add_then_remove(repo, "envrc.md", "x\n")
    assert check_push(repo, push_line(rev(repo), base)).returncode == 0


def test_local_path_and_email_added_then_removed_fail(pushed):
    repo, base = pushed
    add_then_remove(repo, "n1.md", f"see {unix_home('someone', 'x')}\n")
    r = check_push(repo, push_line(rev(repo), base))
    assert r.returncode == 1 and "local-path" in r.stderr and "n1.md:1" in r.stderr
    repo2_base = rev(repo)
    add_then_remove(repo, "n2.md", "mail jane.doe@" + "corp-mail.io\n")
    r = check_push(repo, push_line(rev(repo), repo2_base))
    assert r.returncode == 1 and "email" in r.stderr and "n2.md:1" in r.stderr


def test_allowed_emails_and_author_email_pass_in_range(pushed):
    repo, base = pushed
    (repo / "ok.md").write_text("12345+user@users.noreply.github.com\nsomebody@example.org\n")
    git(repo, "add", "-A")
    sh(["git", "-c", "user.name=t", "-c", "user.email=real.person@" + "corp-mail.io",
        "commit", "-q", "-m", "m"], repo)
    r = check_push(repo, push_line(rev(repo), base))
    assert r.returncode == 0, r.stderr


def test_prepush_allow_applies_to_range_findings(pushed):
    repo, base = pushed
    (repo / "docs").mkdir()
    (repo / "docs" / "sample.md").write_text(f"example: {unix_home('someone', 'x')}\n")
    commit(repo, "sample")
    add_then_remove(repo, ".env.example", "A=1\n")
    r = check_push(repo, push_line(rev(repo), base))
    assert r.returncode == 1 and "docs/sample.md:1" in r.stderr and ".env.example" in r.stderr
    (repo / ".prepush-allow").write_text("# intentional\n^docs/sample\\.md:\n^\\.env\\.example$\n")
    commit(repo, "allow")
    r = check_push(repo, push_line(rev(repo), base))
    assert r.returncode == 0, r.stderr


def test_prepush_allow_file_content_is_not_scanned_in_range(pushed):
    repo, base = pushed
    (repo / ".prepush-allow").write_text("^docs/.*" + unix_home("x", "") + "\n")
    commit(repo, "allow file mentions a path regex")
    assert check_push(repo, push_line(rev(repo), base)).returncode == 0


def test_evil_merge_added_lines_are_scanned(pushed):
    repo, base = pushed
    default = git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    git(repo, "checkout", "-q", "-b", "side")
    (repo / "side.py").write_text("s = 1\n")
    commit(repo, "side")
    git(repo, "checkout", "-q", default)
    (repo / "main.py").write_text("m = 1\n")
    commit(repo, "main")
    git(repo, "merge", "--no-commit", "--no-ff", "-q", "side")
    (repo / "merged.py").write_text(f"KEY = '{AWS}'\n")      # introduced by the merge itself
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "merge")
    r = check_push(repo, push_line(rev(repo), base))
    assert r.returncode == 1 and "merged.py:1" in r.stderr


def test_deleted_refs_and_garbage_lines_are_skipped(pushed):
    repo, base = pushed
    add_then_remove(repo, "a.py", f"KEY = '{AWS}'\n")
    deletion = f"(delete) {ZERO} refs/heads/old {base}\n"
    r = check_push(repo, deletion, "not a ref line\n", "\n")
    assert r.returncode == 0, r.stderr
    assert "clean" in r.stdout and "pushed commit" not in r.stdout


def test_unknown_remote_sha_falls_back_to_new_branch_scan(pushed):
    repo, base = pushed
    add_then_remove(repo, "a.py", f"KEY = '{AWS}'\n")
    r = check_push(repo, push_line(rev(repo), "1" * 40))     # remote tip we do not have (force push)
    assert r.returncode == 1 and "a.py:1" in r.stderr


def test_unlistable_ref_blocks_the_push(pushed):
    repo, _ = pushed
    r = check_push(repo, push_line("2" * 40, ZERO))
    assert r.returncode == 1 and "range-error" in r.stderr


def test_manual_run_with_empty_stdin_scans_head_only(pushed):
    repo, _ = pushed
    add_then_remove(repo, "a.py", f"KEY = '{AWS}'\n")
    r = sh(["bash", str(CHECK), str(repo)], repo, input="")
    assert r.returncode == 0 and "clean" in r.stdout and "pushed commit" not in r.stdout
    (repo / "b.py").write_text(f"KEY = '{AWS}'\n")
    commit(repo, "leak at head")
    assert sh(["bash", str(CHECK), str(repo)], repo, input="").returncode == 1


def test_installed_hook_blocks_secret_added_then_removed(repo, tmp_path):
    remote = tmp_path / "remote.git"
    sh(["git", "init", "-q", "--bare", str(remote)], tmp_path)
    git(repo, "remote", "add", "origin", str(remote))
    assert sh(["bash", str(INSTALL), str(repo)], tmp_path).returncode == 0
    commit(repo, "clean")
    assert sh(["git", "push", "-q", "origin", "HEAD:refs/heads/main"], repo).returncode == 0

    add_then_remove(repo, "leak.py", f"T = '{AWS}'\n")
    assert check(repo).returncode == 0                       # HEAD clean: only the range reveals it
    bad = sh(["git", "push", "-q", "origin", "HEAD:refs/heads/main"], repo)
    assert bad.returncode != 0 and "blocked" in bad.stderr and "leak.py:1" in bad.stderr
    bad_new = sh(["git", "push", "-q", "origin", "HEAD:refs/heads/feature"], repo)
    assert bad_new.returncode != 0 and "leak.py:1" in bad_new.stderr
    # deleting a remote branch pushes no commits and must not be blocked by an old leak
    assert sh(["git", "push", "-q", "--no-verify", "origin", "HEAD:refs/heads/tmp"], repo).returncode == 0
    gone = sh(["git", "push", "-q", "origin", ":refs/heads/tmp"], repo)
    assert gone.returncode == 0, gone.stderr


def test_install_upgrades_an_older_copy_but_not_foreign_hooks(repo):
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/usr/bin/env bash\n# pre-push-check.sh - refuse to push a repo that leaks local or secret data.\necho old\n")
    r = sh(["bash", str(INSTALL), str(repo)], repo)
    assert r.returncode == 0 and "upgraded" in r.stdout, r.stderr
    assert hook.read_text() == CHECK.read_text()
