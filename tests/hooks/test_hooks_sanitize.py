"""Summary masking: paths, e-mail addresses and secret-like strings.

Secret-looking fixtures are assembled at runtime so this file itself passes
scripts/pre-push-check.sh.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from hooks_helpers import HOOKS, RECORD, SRC, unix_home

sys.path.insert(0, str(HOOKS))
import wbcommon as wb  # noqa: E402

TOK = "aB3dE5fG7hJ9kL1mN3pQ5rS7tU9vW1xY3zA5"  # 36 chars, letters + digits
GH = "gh" + "p_" + TOK
GH_PAT = "github" + "_pat_" + "11ABCDEFG0" + "abcdefghij_KLMNOPQRST1234"
AWS = "AK" + "IA" + "IOSFODNN7EXAMPLE"
SK = "sk" + "-" + "proj" + "-" + "abcDEF123456789012345678"
PEM_HEAD = "-----BEGIN " + "RSA PRIVATE" + " KEY-----"
PEM_TAIL = "-----END " + "RSA PRIVATE" + " KEY-----"
PW = "pass" + "word"


def s(text, limit=200):
    return wb.sanitize_text(text, limit)


# --------------------------------------------------------------------------- masked

@pytest.mark.parametrize("secret", [GH, GH_PAT, "gho_" + TOK, "ghu_" + TOK, "ghs_" + TOK, "ghr_" + TOK])
def test_github_tokens_masked(secret):
    out = s(f"push with {secret} failed")
    assert out == "push with <secret> failed"


def test_aws_access_key_masked():
    assert s(f"key {AWS}, region x") == "key <secret>, region x"


def test_sk_api_key_masked():
    assert s(f"OPENAI_API_KEY is {SK}.") == "OPENAI_API_KEY is <secret>."
    assert s("sk" + "-" + "A" * 20) == "<secret>"


def test_bearer_token_masked():
    assert s("Authorization: Bearer abc123.def456_ghi789") == "Authorization: Bearer <secret>"
    assert s("header bearer eyJhbGciOiJIUzI1NiJ9.e30.Zm9v end") == "header bearer <secret> end"


def test_private_key_block_masked_including_body():
    body = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC"
    out = s(f"found {PEM_HEAD}\n{body}\n{body}\n{PEM_TAIL} in repo")
    assert out == "found <secret> in repo"
    # header without END marker: everything after it is dropped (conservative)
    out = s(f"found {PEM_HEAD} {body} tail")
    assert out == "found <secret>"
    assert "PRIVATE" not in out and "MIIE" not in out


@pytest.mark.parametrize("text,expected", [
    (f"{PW}=hunter2xyz", f"{PW}=<secret>"),
    ("token=abc.def", "token=<secret>"),
    ("secret: s3cr3t!", "secret: <secret>"),
    ("api_key=ZZZ", "api_key=<secret>"),
    ("API-KEY = ZZZ next", "API-KEY = <secret> next"),
    ('{"' + PW + '": "two words"}', '{"' + PW + '": <secret>}'),
    ("DB_PASSWD='x y' ok", "DB_PASSWD=<secret> ok"),
    ("GITHUB_TOKEN=ghx1 and client_secret=abc", "GITHUB_TOKEN=<secret> and client_secret=<secret>"),
])
def test_assignments_mask_value_keep_key(text, expected):
    assert s(text) == expected


def test_email_masked():
    assert s("mail alice.b+tag@example.com or bob@sub.corp.co.kr.") == "mail <email> or <email>."


def test_generic_long_run_masked():
    assert s(f"blob {TOK}") == "blob <secret>"
    assert s("x " + "a1" * 16 + " y") == "x <secret> y"  # exactly 32 chars
    assert s("hex " + "0123456789abcdef" * 4) == "hex <secret>"


def test_path_masking_unchanged():
    assert s(f"wrote {unix_home()} and C:\\Users\\bob\\x.csv") == "wrote <path> and <path>"


def test_mask_all_in_one_message():
    out = s(f"{unix_home()} {GH} a@example.com {PW}=zzzz {TOK}")
    assert out == "<path> <secret> <email> " + PW + "=<secret> <secret>"


# --------------------------------------------------------------------------- look-alikes

@pytest.mark.parametrize("text", [
    "rebased onto 9b2f4d6 and 0123456789ab",                  # 7 and 12 char revs
    "Wrote 3 files in docs/research/a.md and https://example.com/x/y?z=1",
    "데이터 전처리를 마쳤고 결과는 사용자 승인을 기다립니다. 비밀번호 정책은 별도 문서에 있습니다.",
    "Finished the cleaning step; token budget and the password policy are documented.",
    "version 1.2.3 -> 10.0.0-rc.1 (python 3.12.4)",
    "Bearer authentication is not used here",
    "input_tokens: 312 output_tokens: 9254",                   # usage-style prose, no key suffix match
    "secrets are stored elsewhere; tokens expire",
    "see docs/research/data_dictionary_v2_final_2024_cleaned.md for details",
    "snake_case_identifier_for_the_feature_extraction_step_2024",
    "short sk-abc and AKIAshort and ghp_short",
    "@dataclass and user@host and #hashtag",
    "<path> and <secret> and <email> already masked",
])
def test_lookalikes_not_masked(text):
    assert s(text) == " ".join(text.split())


def test_normal_absolute_path_is_path_not_secret():
    long_name = "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"  # 32 chars of hex-ish, inside a path
    assert s(unix_home(tail=long_name + "/x.csv")) == "<path>"


# --------------------------------------------------------------------------- cut / idempotence

def test_secret_straddling_the_cut_leaks_no_prefix():
    for secret in (GH, SK, TOK, AWS):
        for pos in range(188, 200):  # secret starts before position 200 and ends after
            text = "x" * pos + " " + secret + " tail"
            out = s(text)
            assert out is not None and len(out) <= 200
            frag = secret[:8]
            assert frag not in out, (pos, out[-40:])
            # what follows the x-run is the placeholder (possibly cut by the limit), never the secret
            assert "<secret> tail".startswith(out[pos + 1:]), (pos, out[-40:])


def test_email_and_assignment_straddling_the_cut():
    out = s("y" * 195 + " alice@example.com")
    assert "alice" not in out and "example" not in out
    out = s("y" * 190 + " " + PW + "=hunter2hunter2")
    assert "hunter" not in out


def test_idempotent():
    texts = [
        f"{unix_home()} {GH} a@example.com {PW}=zzzz {TOK} Bearer abc123xyz9 {PEM_HEAD} k {PEM_TAIL}",
        '{"' + PW + '": "two words", "token": \'x\'}',
        "plain Korean 문장 and docs/a.md " + "z" * 300,
    ]
    for t in texts:
        once = s(t)
        assert s(once) == once


def test_none_and_empty():
    assert s(None) is None and s("") is None and s("  \n\t ") is None


def test_fail_soft_never_returns_raw(monkeypatch):
    def boom(_):
        raise RuntimeError("regex exploded")
    monkeypatch.setattr(wb, "_EMAIL_RE", type("R", (), {"sub": staticmethod(lambda *a, **k: boom(0))}))
    assert wb.sanitize_text(f"mail a@example.com {GH}") is None


def test_huge_input_is_fast_and_capped():
    import time
    big = ("word " * 50) + TOK + " " + ("abc123 " * 300_000) + "a" * 200_000
    t = time.time()
    out = s(big)
    assert time.time() - t < 5
    assert out is not None and len(out) <= 200


# --------------------------------------------------------------------------- end to end

def test_recorder_masks_stop_and_error_summaries(project):
    msg = f"done: {unix_home()} {GH} alice@example.com {PW}=hunter2hunter2"
    project.run(RECORD, {"hook_event_name": "SubagentStop", "agent_id": "a", "agent_type": "t",
                         "last_assistant_message": msg})
    project.run(RECORD, {"hook_event_name": "PostToolUseFailure", "tool_name": "Agent",
                         "tool_input": {"subagent_type": "t"}, "error": f"401 Bearer {TOK} for {SK}"})
    stop, err = project.events()
    assert stop["summary"] == "done: <path> <secret> <email> " + PW + "=<secret>"
    assert err["summary"] == "401 Bearer <secret> for <secret>"
    raw = json.dumps([stop, err])
    for leak in (GH, TOK, SK, "alice@", "hunter2"):
        assert leak not in raw


def test_recorder_cut_after_masking(project):
    msg = "w" * 190 + " " + GH + " end"
    project.run(RECORD, {"hook_event_name": "SubagentStop", "agent_id": "a", "agent_type": "t",
                         "last_assistant_message": msg})
    summary = project.events()[0]["summary"]
    assert len(summary) <= 200 and "gh" + "p_" not in summary and TOK[:8] not in summary


def test_emit_summary_masked(project):
    r = subprocess.run(
        [sys.executable, "-m", "agent_workbench.emit", "change_requested",
         "--summary", f"ask bob@example.com about {GH} in {unix_home()}"],
        capture_output=True, text=True, env=project.env(PYTHONPATH=str(SRC)),
        cwd=str(project.root), timeout=30)
    assert r.returncode == 0, r.stderr
    (ev,) = project.events()
    assert ev["summary"] == "ask <email> about <secret> in <path>"
