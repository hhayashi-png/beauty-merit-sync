"""
Slack Incoming Webhook への通知。

設計方針:
  - 通知失敗は本処理を妨げない(警告/エラーログのみ)
  - SLACK_WEBHOOK_URL 未設定なら何もせず黙ってスキップ
  - URL 自体は絶対にログに出さない(ペイロードログでは送信先をマスク)
  - traceback / エラーメッセージから認証情報らしき値をマスクしてから送信
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import requests

from .logger import get_logger

logger = get_logger("slack_notifier")

MAX_RETRIES = 3
TIMEOUT_SEC = 10
JST = timezone(timedelta(hours=9))

SPREADSHEET_URL_TEMPLATE = "https://docs.google.com/spreadsheets/d/{sid}/edit"
ACTIONS_URL = "https://github.com/hhayashi-png/beauty-merit-sync/actions"

# エラーメッセージ・スタックトレースから機密情報をマスクするためのパターン
_SENSITIVE_PATTERNS = [
    # メールアドレス形式の ID(BMERIT_LOGIN_ID 想定)
    # 注: 通常のメールは TLD 2文字以上だが、b-merit のログインIDは短いTLD
    # (例: limegroup@a.a)もあり得るので 1 文字以上を許容する。
    (re.compile(r"\b[\w._%+-]+@[\w.-]+\.[A-Za-z]+\b"), "<redacted-email>"),
    # GitHub PAT
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "<redacted-token>"),
    # Slack webhook URL
    (re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/]+"), "https://hooks.slack.com/<redacted>"),
    # GCP private key 内容
    (re.compile(r"-----BEGIN[^-]*PRIVATE KEY-----[\s\S]*?-----END[^-]*PRIVATE KEY-----"),
     "<redacted-private-key>"),
    # 一般的なパスワード風の値(env=PASSWORD)
    (re.compile(r"(password['\"]?\s*[:=]\s*['\"]?)([^'\"\s,}]+)", re.IGNORECASE),
     r"\1<redacted>"),
]


def _redact(text: str) -> str:
    """機密情報らしきパターンをマスク"""
    if not text:
        return text
    out = text
    for pat, repl in _SENSITIVE_PATTERNS:
        out = pat.sub(repl, out)
    return out


def _now_jst_str() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST")


# ── ペイロード生成 ─────────────────────────────────

def build_success_payload(stats: dict[str, Any]) -> dict[str, Any]:
    """
    stats に期待するキー(全て optional だが揃っているほど richer):
      - total_rows: int                合計データ行数
      - store_count: int               店舗数
      - monthly_breakdown: dict[str,int]  例 {"2026-04": 2019, "2026-05": 578}
      - spreadsheet_id: str            スプシID(URLボタンに使用)
      - elapsed_sec: float             所要秒数
    """
    total = stats.get("total_rows")
    store_count = stats.get("store_count")
    monthly = stats.get("monthly_breakdown") or {}
    spreadsheet_id = stats.get("spreadsheet_id") or "1aykK8ll0upbVoJnJJrXBp_cAXBovyI6hc4XzoI8GZ10"
    elapsed = stats.get("elapsed_sec")

    sorted_months = sorted(monthly.keys())
    period_text = " + ".join(sorted_months) if sorted_months else "—"

    fields = []
    fields.append({"type": "mrkdwn", "text": f"*対象期間:*\n{period_text}"})
    if total is not None:
        fields.append({"type": "mrkdwn", "text": f"*取得件数:*\n{total:,}件"})
    if store_count is not None:
        fields.append({"type": "mrkdwn", "text": f"*店舗数:*\n{store_count}店舗"})
    fields.append({"type": "mrkdwn", "text": f"*実行時刻:*\n{_now_jst_str()}"})
    if elapsed is not None:
        fields.append({"type": "mrkdwn", "text": f"*所要時間:*\n{elapsed:.1f}秒"})

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "✅ 予約データ更新完了", "emoji": True},
        },
        {"type": "section", "fields": fields},
    ]

    if monthly:
        breakdown_lines = "\n".join(
            f"• {m}: {monthly[m]:,}件" for m in sorted_months
        )
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*月別内訳:*\n{breakdown_lines}"},
        })

    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "📊 スプレッドシートを開く", "emoji": True},
                "url": SPREADSHEET_URL_TEMPLATE.format(sid=spreadsheet_id),
                "style": "primary",
            }
        ],
    })

    return {
        "text": "✅ 予約データ更新完了",  # フォールバック表示用
        "blocks": blocks,
    }


def build_failure_payload(
    error_message: str,
    stage: str,
    traceback_text: str | None = None,
) -> dict[str, Any]:
    err = _redact(error_message)
    fields = [
        {"type": "mrkdwn", "text": f"*失敗ステージ:*\n{stage}"},
        {"type": "mrkdwn", "text": f"*実行時刻:*\n{_now_jst_str()}"},
    ]

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "❌ 予約データ更新失敗", "emoji": True},
        },
        {"type": "section", "fields": fields},
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*エラー内容:*\n```{err[:1500]}```"},
        },
    ]

    if traceback_text:
        tb = _redact(traceback_text)
        # スタックトレースは最後の方が重要なので末尾を残す
        if len(tb) > 1500:
            tb = "...(略)...\n" + tb[-1500:]
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*スタックトレース(末尾):*\n```{tb}```"},
        })

    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "🔍 GitHub Actionsで詳細確認", "emoji": True},
                "url": ACTIONS_URL,
                "style": "danger",
            }
        ],
    })

    return {
        "text": f"❌ 予約データ更新失敗 ({stage})",
        "blocks": blocks,
    }


# ── 送信 ────────────────────────────────────────

def _post(payload: dict[str, Any], webhook_url: str | None = None) -> bool:
    """Webhook に POST。成功で True、失敗(リトライ後)で False。例外は上に上げない。"""
    url = webhook_url if webhook_url is not None else os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        logger.warning("SLACK_WEBHOOK_URL が未設定のため、Slack通知をスキップします")
        return False

    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, timeout=TIMEOUT_SEC)
            if 200 <= resp.status_code < 300:
                logger.info(f"Slack通知送信成功 (試行 {attempt}/{MAX_RETRIES})")
                return True
            last_err = RuntimeError(
                f"HTTP {resp.status_code}: {resp.text[:200]}"
            )
            logger.warning(f"Slack通知 試行 {attempt} 失敗: {last_err}")
        except Exception as e:
            last_err = e
            logger.warning(f"Slack通知 試行 {attempt} 例外: {e}")

        if attempt < MAX_RETRIES:
            wait = 2 ** (attempt - 1)  # 1, 2, 4 秒
            time.sleep(wait)

    logger.error(f"Slack通知 {MAX_RETRIES}回失敗。本処理は継続: {last_err}")
    return False


def notify_success(stats: dict[str, Any], webhook_url: str | None = None) -> bool:
    """成功通知。失敗しても本処理を妨げない(常に bool を返し、例外は出さない)"""
    try:
        payload = build_success_payload(stats)
        return _post(payload, webhook_url=webhook_url)
    except Exception as e:
        logger.error(f"Slack成功通知の組み立てに失敗(本処理は継続): {e}")
        return False


def notify_failure(
    error_message: str,
    stage: str,
    traceback: str | None = None,
    webhook_url: str | None = None,
) -> bool:
    """失敗通知。失敗してもこの関数では再 raise しない(本処理側で raise されている前提)"""
    try:
        payload = build_failure_payload(error_message, stage, traceback)
        return _post(payload, webhook_url=webhook_url)
    except Exception as e:
        logger.error(f"Slack失敗通知の組み立てに失敗: {e}")
        return False
