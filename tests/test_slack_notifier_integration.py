"""
Slack 通知の統合テスト(実 Webhook へ送信)。pytest からは実行されない。

使い方:
    source venv/bin/activate
    python -m tests.test_slack_notifier_integration

事前準備:
    .env に SLACK_WEBHOOK_URL=... が設定されていること
    (load_app_config 経由で .env を自動ロードする)
"""

from __future__ import annotations

import sys

from src.config import load_app_config
from src.logger import get_logger
from src.slack_notifier import notify_failure, notify_success

logger = get_logger("slack_integration")


def main() -> int:
    cfg = load_app_config()
    if not cfg.slack_webhook_url:
        print("❌ SLACK_WEBHOOK_URL が未設定です。.env または環境変数に追加してください")
        return 1

    print("=" * 60)
    print(" Slack 通知 統合テスト")
    print("=" * 60)
    print("(SLACK_WEBHOOK_URL の値は出力しません)")
    print()

    # ── 成功通知のサンプル ──
    print("[1/2] 成功通知を送信中...")
    sample_stats = {
        "total_rows": 2597,
        "store_count": 11,
        "monthly_breakdown": {"2026-04": 2019, "2026-05": 578},
        "spreadsheet_id": cfg.spreadsheet_id,
        "elapsed_sec": 14.7,
    }
    ok = notify_success(sample_stats, webhook_url=cfg.slack_webhook_url)
    print(f"  → {'✓ 送信成功' if ok else '✗ 送信失敗'}")

    # ── 失敗通知のサンプル ──
    print("\n[2/2] 失敗通知を送信中...")
    ok = notify_failure(
        error_message=(
            "TimeoutError: Login form not found within 30 seconds.\n"
            "Selectorに合致する要素が見つかりませんでした。"
        ),
        stage="ビューティーメリットログイン",
        traceback=(
            "Traceback (most recent call last):\n"
            "  File \"src/scraper.py\", line 123, in _login\n"
            "    raise TimeoutError(...)\n"
            "TimeoutError: Login form not found"
        ),
        webhook_url=cfg.slack_webhook_url,
    )
    print(f"  → {'✓ 送信成功' if ok else '✗ 送信失敗'}")

    print("\n" + "=" * 60)
    print(" Slack を確認してください(2件のテスト通知が届いているはず)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
