"""
Google スプレッドシートへの書き込み。
1行目(手動設定のヘッダー)は保護し、A2 以降のみを上書きする。
"""

from __future__ import annotations

import time
from typing import Any, List

import gspread
from google.oauth2.service_account import Credentials

from .config import EXPECTED_COL_COUNT
from .logger import get_logger

logger = get_logger("sheet_writer")

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

# 27カラム(CSV) + 1カラム(店舗名) = 28列 → AB列まで
LAST_COL_LETTER = "AB"

MAX_RETRIES = 3


def _authorize(credentials_json: dict[str, Any]) -> gspread.Client:
    creds = Credentials.from_service_account_info(credentials_json, scopes=SCOPES)
    return gspread.authorize(creds)


def write_to_sheet(
    credentials_json: dict[str, Any],
    spreadsheet_id: str,
    sheet_name: str,
    data_rows: List[List[str]],
) -> None:
    """
    data_rows を対象シートの A2 以降に書き込む。
    1 行目(ヘッダー)は絶対に触らない。
    """
    if not data_rows:
        raise RuntimeError("書き込むデータ行が空です")

    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            client = _authorize(credentials_json)
            sh = client.open_by_key(spreadsheet_id)
            try:
                ws = sh.worksheet(sheet_name)
            except gspread.WorksheetNotFound:
                raise RuntimeError(f"タブ '{sheet_name}' が見つかりません") from None

            logger.info(
                f"シート取得: '{sheet_name}' (既存 rows={ws.row_count}, cols={ws.col_count})"
            )

            # A2以降クリア(1行目ヘッダーは絶対に触らない)
            clear_range = f"A2:{LAST_COL_LETTER}"
            logger.info(f"クリア範囲: {clear_range}")
            ws.batch_clear([clear_range])

            # 書き込み
            num_rows = len(data_rows)
            num_cols = max(len(r) for r in data_rows)
            logger.info(f"書き込み: {num_rows}行 × {num_cols}列")

            # 期待列数チェック(警告のみ)
            if num_cols != EXPECTED_COL_COUNT + 1:
                logger.warning(
                    f"列数が期待({EXPECTED_COL_COUNT + 1}) と異なります(実際={num_cols})"
                )

            ws.update(
                range_name="A2",
                values=data_rows,
                value_input_option="USER_ENTERED",
            )
            logger.info("書き込み完了")
            return

        except Exception as e:
            last_err = e
            logger.warning(f"シート書き込み試行 {attempt} 失敗: {e}")
            if attempt < MAX_RETRIES:
                wait = 2 ** (attempt - 1)  # 1, 2, 4 秒
                logger.info(f"{wait}秒待機してリトライ")
                time.sleep(wait)

    raise RuntimeError(f"シート書き込みに{MAX_RETRIES}回失敗: {last_err}") from last_err
