"""エントリポイント。当月+翌月の予約データを取得し、スプシに反映 + Slack通知"""

from __future__ import annotations

import sys
import tempfile
import time
import traceback as _tb
from collections import Counter
from pathlib import Path
from typing import Any

from .config import load_app_config, load_store_mapping
from .csv_processor import build_rows_from_multiple_zips
from .logger import get_logger
from .scraper import download_reservations_for_two_months
from .sheet_writer import write_to_sheet
from .slack_notifier import notify_failure, notify_success

logger = get_logger("main")


# ── 進捗ステージ管理 ───────────────────────────

# main 内の各ステップ通過時に True を立てる。失敗時はこの dict から失敗箇所を推定する。
# 順番が意味を持つ(早いものが上)
_STAGES_ORDER = [
    ("config_loaded", "設定読み込み"),
    ("download_success", "ビューティーメリットからのZIPダウンロード"),
    ("csv_processed", "ZIP展開・CSV処理"),
    ("sheet_write_success", "スプレッドシート書き込み"),
]


def _detect_failure_stage(stats: dict[str, Any]) -> str:
    """stats のフラグを上から見て、最初に未達のステージを返す"""
    for key, label in _STAGES_ORDER:
        if not stats.get(key):
            return label
    return "不明"


def _collect_stats_from_rows(rows: list[list[str]], spreadsheet_id: str) -> dict[str, Any]:
    """書き込み済み行から月別件数・店舗数を集計。
    M列(index 12) = 来店日時 を月別キーに使用。"""
    months: Counter[str] = Counter()
    stores: set[str] = set()
    for r in rows:
        if r and r[0]:
            stores.add(r[0])
        if len(r) > 12:
            visit = r[12]
            if visit and len(visit) >= 7:
                months[visit[:7]] += 1
    return {
        "total_rows": len(rows),
        "store_count": len(stores),
        "monthly_breakdown": dict(months),
        "spreadsheet_id": spreadsheet_id,
    }


# ── タイマー ────────────────────────────────

class _StepTimer:
    def __init__(self, label: str):
        self.label = label

    def __enter__(self):
        self.start = time.time()
        logger.info(f"========== {self.label} 開始 ==========")
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed = time.time() - self.start
        status = "完了" if exc_type is None else "失敗"
        logger.info(f"========== {self.label} {status} ({elapsed:.2f}秒) ==========")
        return False


# ── メイン ──────────────────────────────────

def main() -> int:
    total_start = time.time()
    logger.info("処理開始")

    stats: dict[str, Any] = {}
    cfg = None
    webhook_url: str | None = None

    try:
        cfg = load_app_config()
        mapping = load_store_mapping()
        webhook_url = cfg.slack_webhook_url
        stats["config_loaded"] = True
        stats["spreadsheet_id"] = cfg.spreadsheet_id
        logger.info(
            f"設定ロード完了: SPREADSHEET_ID={cfg.spreadsheet_id}, "
            f"SHEET_NAME={cfg.sheet_name}, HEADLESS={cfg.headless}, "
            f"stores登録数={len(mapping.rules)}, "
            f"Slack通知={'有効' if webhook_url else '未設定'}"
        )

        with tempfile.TemporaryDirectory(prefix="bmerit_") as tmp:
            tmp_path = Path(tmp)

            with _StepTimer("Step 1: 当月+翌月のZIPダウンロード"):
                zip_paths = download_reservations_for_two_months(
                    login_id=cfg.bmerit_login_id,
                    password=cfg.bmerit_password,
                    save_dir=tmp_path / "zip",
                    headless=cfg.headless,
                    debug_dump=cfg.debug_dump,
                )
                logger.info(f"取得 ZIP 数: {len(zip_paths)}")
                stats["download_success"] = True

            with _StepTimer("Step 2: ZIP展開 + CSV集計(cp932 ファイル名対応)"):
                data_rows = build_rows_from_multiple_zips(
                    zip_paths=zip_paths,
                    mapping=mapping,
                    work_dir=tmp_path / "extracted",
                )
                stats["csv_processed"] = True

            with _StepTimer("Step 3: スプレッドシート書き込み"):
                write_to_sheet(
                    credentials_json=cfg.google_credentials,
                    spreadsheet_id=cfg.spreadsheet_id,
                    sheet_name=cfg.sheet_name,
                    data_rows=data_rows,
                )
                stats["sheet_write_success"] = True

        elapsed = time.time() - total_start
        logger.info(f"✅ 全ステップ完了 (合計 {elapsed:.2f}秒)")

        # ── Slack 成功通知 ──
        notify_stats = _collect_stats_from_rows(data_rows, cfg.spreadsheet_id)
        notify_stats["elapsed_sec"] = elapsed
        try:
            notify_success(notify_stats, webhook_url=webhook_url)
        except Exception as e:
            # 念のため二重に防御(関数側でも例外は出さない設計だが)
            logger.error(f"Slack成功通知でエラー(本処理は成功扱い): {e}")
        return 0

    except Exception as e:
        elapsed = time.time() - total_start
        logger.error(f"❌ エラーにより中断 (経過 {elapsed:.2f}秒): {e}")
        tb_text = _tb.format_exc()
        logger.error(tb_text)

        stage = _detect_failure_stage(stats)

        # ── Slack 失敗通知 ──
        try:
            notify_failure(
                error_message=str(e),
                stage=stage,
                traceback=tb_text,
                webhook_url=webhook_url,
            )
        except Exception as ne:
            logger.error(f"Slack失敗通知の送信に失敗: {ne}")

        return 1


if __name__ == "__main__":
    sys.exit(main())
