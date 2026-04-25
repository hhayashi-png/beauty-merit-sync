"""
ZIP を展開し、各店舗 CSV を読んで Google スプレッドシート書き込み用の
2次元配列を生成する。

出力ルール(仕様):
  - A列: 店舗名
  - B列以降: CSV のデータ行(27カラム)
  - 全店舗で CSV のヘッダー行はスキップ(スプシ側に既に固定ヘッダーが入っているため)
  - 店舗の並び順は store_mapping.yml の stores 定義順
"""

from __future__ import annotations

import csv
import zipfile
from pathlib import Path
from typing import List, Tuple

from .config import EXPECTED_COL_COUNT, EXPECTED_HEADER, StoreMapping
from .logger import get_logger

logger = get_logger("csv_processor")

CSV_ENCODING = "cp932"  # ビューティーメリット CSV 固定仕様


# ── ZIP 展開 ────────────────────────────────────

def extract_zip(zip_path: Path | str, dest_dir: Path | str) -> List[Path]:
    """ZIP を dest_dir に展開し、展開された CSV ファイルパスのリストを返す"""
    zip_path = Path(zip_path)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if not zip_path.exists():
        raise FileNotFoundError(f"ZIPファイルが見つかりません: {zip_path}")

    extracted: List[Path] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            if not name.lower().endswith(".csv"):
                continue
            extracted.append(dest_dir / name)

    logger.info(f"ZIP展開完了: {len(extracted)}件のCSV ({zip_path} → {dest_dir})")
    return extracted


# ── CSV 読み込み ────────────────────────────────

def _read_csv(path: Path) -> List[List[str]]:
    """cp932 で CSV を読み込み、全行を2次元配列で返す"""
    with open(path, "r", encoding=CSV_ENCODING, newline="", errors="replace") as f:
        reader = csv.reader(f)
        return [row for row in reader]


def _validate_header(filename: str, header: List[str]) -> None:
    """ヘッダーが期待構造と異なれば警告(処理は継続)"""
    if len(header) != EXPECTED_COL_COUNT:
        logger.warning(
            f"{filename}: ヘッダー列数が期待({EXPECTED_COL_COUNT})と異なる "
            f"({len(header)}列) — 処理は続行します"
        )
    # 項目名の食い違いも軽く検出
    mismatches = [
        (i, exp, got)
        for i, (exp, got) in enumerate(zip(EXPECTED_HEADER, header))
        if exp != got
    ]
    if mismatches:
        logger.warning(
            f"{filename}: ヘッダー項目名が一部異なります (最初の不一致: "
            f"col={mismatches[0][0]} expected='{mismatches[0][1]}' got='{mismatches[0][2]}')"
        )


# ── メイン処理 ─────────────────────────────────

def build_rows_for_sheet(
    csv_files: List[Path],
    mapping: StoreMapping,
) -> List[List[str]]:
    """
    CSVファイル群をスプレッドシート書き込み用の2次元配列に変換する。

    - 全店舗で CSV のヘッダー行(rows[0])はスキップ
    - データ行(rows[1:])のみ A列に店舗名を付与して出力
    - 店舗順は store_mapping.yml の定義順
    """
    # 店舗名の解決 → (store_name, path) のリスト
    resolved: List[Tuple[str, Path]] = []
    for p in csv_files:
        name = mapping.resolve(p.name)
        if name is None:
            raise RuntimeError(
                f"店舗名が解決できません: {p.name} "
                f"(store_mapping.yml の fallback.use_filename_as_name=false)"
            )
        resolved.append((name, p))

    # YAML 定義順にソート(未定義は末尾、同店舗名内はファイル名で安定化)
    resolved.sort(key=lambda x: (mapping.store_order_index(x[0]), x[1].name))

    result: List[List[str]] = []
    effective_idx = 0  # 実際に処理した店舗の通し番号(空ファイルスキップ後)
    for store_name, csv_path in resolved:
        try:
            rows = _read_csv(csv_path)
        except Exception as e:
            logger.error(f"CSV読み込み失敗(スキップ): {csv_path.name}: {e}")
            continue

        if not rows:
            logger.warning(f"空ファイルをスキップ: {csv_path.name}")
            continue

        header = rows[0]
        data_rows = rows[1:]  # ヘッダー行は出力しない(スプシ側に固定ヘッダーがあるため)

        _validate_header(csv_path.name, header)

        effective_idx += 1
        logger.info(f"[{effective_idx}店舗目] {store_name}: {len(data_rows)}データ行")

        for row in data_rows:
            result.append([store_name] + row)

    if not result:
        raise RuntimeError("書き込むデータ行が 0 件でした")

    logger.info(f"集計完了: {len(result)}行を生成(店舗数: {len(resolved)})")
    return result
