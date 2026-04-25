"""エントリポイント。CSV取得 → パース → スプレッドシート反映"""

from __future__ import annotations

import sys
import tempfile
import time
import traceback
from pathlib import Path

from .config import load_app_config, load_store_mapping
from .csv_processor import build_rows_for_sheet, extract_zip
from .logger import get_logger
from .scraper import download_reservations_zip
from .sheet_writer import write_to_sheet

logger = get_logger("main")


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


def main() -> int:
    total_start = time.time()
    logger.info("処理開始")

    try:
        cfg = load_app_config()
        mapping = load_store_mapping()
        logger.info(
            f"設定ロード完了: SPREADSHEET_ID={cfg.spreadsheet_id}, "
            f"SHEET_NAME={cfg.sheet_name}, HEADLESS={cfg.headless}, "
            f"stores登録数={len(mapping.rules)}"
        )

        with tempfile.TemporaryDirectory(prefix="bmerit_") as tmp:
            tmp_path = Path(tmp)

            with _StepTimer("Step 1: ZIPダウンロード"):
                zip_path = download_reservations_zip(
                    login_id=cfg.bmerit_login_id,
                    password=cfg.bmerit_password,
                    save_dir=tmp_path / "zip",
                    headless=cfg.headless,
                    debug_dump=cfg.debug_dump,
                )

            with _StepTimer("Step 2: ZIP展開"):
                csv_files = extract_zip(zip_path, tmp_path / "extracted")
                logger.info(f"CSVファイル数: {len(csv_files)}")

            with _StepTimer("Step 3: CSV集計"):
                data_rows = build_rows_for_sheet(csv_files, mapping)

            with _StepTimer("Step 4: スプレッドシート書き込み"):
                write_to_sheet(
                    credentials_json=cfg.google_credentials,
                    spreadsheet_id=cfg.spreadsheet_id,
                    sheet_name=cfg.sheet_name,
                    data_rows=data_rows,
                )

        elapsed = time.time() - total_start
        logger.info(f"✅ 全ステップ完了 (合計 {elapsed:.2f}秒)")
        return 0

    except Exception as e:
        elapsed = time.time() - total_start
        logger.error(f"❌ エラーにより中断 (経過 {elapsed:.2f}秒): {e}")
        logger.error(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
