"""slack_notifier の単体テスト(実Webhookには送らずモック化)"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src import slack_notifier
from src.slack_notifier import (
    _redact,
    build_failure_payload,
    build_success_payload,
    notify_failure,
    notify_success,
)


# ── ペイロード構造 ────────────────────────────────

def test_success_payload_has_block_kit_structure():
    payload = build_success_payload({
        "total_rows": 2597,
        "store_count": 11,
        "monthly_breakdown": {"2026-04": 2019, "2026-05": 578},
        "spreadsheet_id": "test_sid",
        "elapsed_sec": 14.7,
    })
    assert "blocks" in payload
    assert "text" in payload  # フォールバック
    blocks = payload["blocks"]
    # ヘッダーに ✅ が含まれる
    assert blocks[0]["type"] == "header"
    assert "✅" in blocks[0]["text"]["text"]
    # fields セクションに件数・店舗数が入っている
    field_texts = [f["text"] for f in blocks[1]["fields"]]
    assert any("2,597件" in t for t in field_texts)
    assert any("11店舗" in t for t in field_texts)
    # 月別内訳セクションがある
    text_blocks = [b for b in blocks if b.get("type") == "section" and "text" in b]
    breakdown_text = "\n".join(b["text"]["text"] for b in text_blocks if "月別内訳" in b["text"].get("text", ""))
    assert "2026-04: 2,019件" in breakdown_text
    assert "2026-05: 578件" in breakdown_text
    # スプシボタンが含まれる
    actions = [b for b in blocks if b["type"] == "actions"][0]
    btn = actions["elements"][0]
    assert "test_sid" in btn["url"]
    assert btn["style"] == "primary"


def test_success_payload_handles_missing_optional_fields():
    """stats が空でも例外にならない"""
    payload = build_success_payload({})
    assert payload["blocks"][0]["text"]["text"].startswith("✅")


def test_failure_payload_has_block_kit_structure():
    payload = build_failure_payload(
        error_message="TimeoutError: Login form not found",
        stage="ビューティーメリットログイン",
        traceback_text="File ...\n  raise TimeoutError(...)",
    )
    blocks = payload["blocks"]
    assert blocks[0]["type"] == "header"
    assert "❌" in blocks[0]["text"]["text"]

    field_texts = [f["text"] for f in blocks[1]["fields"]]
    assert any("ビューティーメリットログイン" in t for t in field_texts)

    # エラー本文が code block で含まれる
    err_section = next(
        b for b in blocks
        if b.get("type") == "section" and "エラー内容" in b.get("text", {}).get("text", "")
    )
    assert "TimeoutError" in err_section["text"]["text"]

    # GitHub Actions ボタン
    actions = [b for b in blocks if b["type"] == "actions"][0]
    assert "github.com" in actions["elements"][0]["url"]
    assert actions["elements"][0]["style"] == "danger"


# ── マスキング ────────────────────────────────

def test_redact_email():
    assert "<redacted-email>" in _redact("Login with limegroup@a.a failed")


def test_redact_github_token():
    assert "<redacted-token>" in _redact("token=ghp_abcdefghij1234567890XYZ")


def test_redact_slack_webhook_url():
    out = _redact("https://hooks.slack.com/services/T123/B456/xyzabc789secret")
    assert "secret" not in out
    assert "<redacted>" in out


def test_redact_password_in_dict_repr():
    out = _redact("password='supersecret123'")
    assert "supersecret123" not in out
    assert "<redacted>" in out


def test_redact_private_key_block():
    text = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA1234567890\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out = _redact(text)
    assert "MIIEpAIBAAKCAQEA" not in out
    assert "<redacted-private-key>" in out


def test_failure_payload_redacts_credentials():
    payload = build_failure_payload(
        error_message="Auth failed for limegroup@a.a / password='lime8262'",
        stage="ログイン",
    )
    serialized = json.dumps(payload)
    assert "limegroup@a.a" not in serialized
    assert "lime8262" not in serialized


# ── 送信(モック)────────────────────────────

@pytest.fixture
def mock_post_ok():
    with patch.object(slack_notifier, "requests") as m:
        resp = MagicMock(status_code=200, text="ok")
        m.post.return_value = resp
        yield m


def test_notify_success_calls_post_once_when_ok(mock_post_ok):
    ok = notify_success(
        {"total_rows": 100, "store_count": 5, "monthly_breakdown": {"2026-04": 100}},
        webhook_url="https://hooks.slack.com/services/x/y/z",
    )
    assert ok is True
    assert mock_post_ok.post.call_count == 1
    sent_payload = mock_post_ok.post.call_args.kwargs["json"]
    assert "blocks" in sent_payload


def test_notify_failure_calls_post_once_when_ok(mock_post_ok):
    ok = notify_failure(
        error_message="boom",
        stage="ログイン",
        webhook_url="https://hooks.slack.com/services/x/y/z",
    )
    assert ok is True
    assert mock_post_ok.post.call_count == 1


def test_notify_returns_false_if_webhook_unset(monkeypatch, caplog):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    # 例外を出さず False を返すこと(本処理を妨げない)
    assert notify_success({"total_rows": 1}, webhook_url=None) is False
    assert notify_failure("err", "stg", webhook_url=None) is False


def test_notify_retries_on_failure_then_succeeds():
    """1回目は500、2回目は200で成功するケース。post が2回呼ばれること"""
    side_effects = [
        MagicMock(status_code=500, text="server error"),
        MagicMock(status_code=200, text="ok"),
    ]
    with patch.object(slack_notifier, "requests") as m, \
         patch.object(slack_notifier.time, "sleep") as sleep_mock:
        m.post.side_effect = side_effects
        ok = notify_success(
            {"total_rows": 1},
            webhook_url="https://hooks.slack.com/services/x/y/z",
        )
        assert ok is True
        assert m.post.call_count == 2
        # 1回はバックオフ sleep が呼ばれているはず
        assert sleep_mock.call_count >= 1


def test_notify_retries_max_times_then_returns_false():
    """全試行失敗 → 例外を出さず False を返す"""
    fail_resp = MagicMock(status_code=500, text="server error")
    with patch.object(slack_notifier, "requests") as m, \
         patch.object(slack_notifier.time, "sleep"):
        m.post.return_value = fail_resp
        ok = notify_success(
            {"total_rows": 1},
            webhook_url="https://hooks.slack.com/services/x/y/z",
        )
        assert ok is False
        assert m.post.call_count == slack_notifier.MAX_RETRIES


def test_notify_handles_network_exception():
    """requests.post が例外でも本処理を妨げない"""
    with patch.object(slack_notifier, "requests") as m, \
         patch.object(slack_notifier.time, "sleep"):
        m.post.side_effect = Exception("DNS failure")
        ok = notify_success(
            {"total_rows": 1},
            webhook_url="https://hooks.slack.com/services/x/y/z",
        )
        assert ok is False
        assert m.post.call_count == slack_notifier.MAX_RETRIES
