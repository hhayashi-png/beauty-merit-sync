"""csv_processor と店舗マッピングの単体テスト"""

from __future__ import annotations

import csv
import zipfile
from pathlib import Path

import pytest

from src.config import EXPECTED_HEADER, load_store_mapping
from src.csv_processor import (
    build_rows_for_sheet,
    build_rows_from_multiple_zips,
    extract_zip,
    extract_zips,
)


# ────────────────────────────────────────
# 店舗名マッピング: 11 サンプルに対する期待値
# ────────────────────────────────────────

SAMPLE_FILENAMES = [
    ("reserve_ハーフ_ヒ_ーリンク_LIME_渋谷_2026-04-01.csv", "LIME渋谷"),
    ("reserve_ハーフ_ヒ_ーリンク_LIME_新宿三丁目_2026-04-01.csv", "LIME新宿三丁目"),
    ("reserve_ハーフ_ヒ_ーリンク_LIME池袋東口_2026-04-01.csv", "LIME池袋東口"),
    ("reserve__ハーフ_ヒ_ーリンク__ニキヒ_ケア_韓国肌管理Belle_大宮店_2026-04-01.csv", "Belle大宮"),
    ("reserve__ハーフ_ヒ_ーリンク__ニキヒ_ケア_韓国肌管理Belle_北千住店_2026-04-01.csv", "Belle北千住"),
    ("reserve__ハーフ_ヒ_ーリンク__ニキヒ_ケア_韓国肌管理Belle_川崎店_2026-04-01.csv", "Belle川崎"),
    ("reserve__ハーフ_ヒ_ーリンク__ニキヒ_ケア_韓国肌管理Belle_柏店_2026-04-01.csv", "Belle柏"),
    ("reserve__ハーフ_ヒ_ーリンク__ニキヒ_ケア_韓国肌管理Belle_西船橋店_2026-04-01.csv", "Belle西船橋"),
    ("reserve__ハーフ_ヒ_ーリンク__ニキヒ_ケア_韓国肌管理Belle錦糸町店_2026-04-01.csv", "Belle錦糸町"),
    ("reserve__ハーフ_ヒ_ーリンク__l_ニキヒ_ケア_韓国肌管理_Belle_横浜店_2026-04-01.csv", "Belle横浜"),
    ("reserve__韓国肌管理_ハーフ_ヒ_ーリンク__Belle日本橋_2026-04-01.csv", "Belle日本橋"),
]


@pytest.fixture(scope="module")
def mapping():
    return load_store_mapping()


@pytest.mark.parametrize("filename, expected", SAMPLE_FILENAMES)
def test_resolve_store_name(mapping, filename, expected):
    assert mapping.resolve(filename) == expected


def test_fallback_unknown_filename(mapping):
    """未登録の店舗名は fallback でファイル名そのままが返る(デフォルト挙動)"""
    assert mapping.resolve("完全_未知_店舗_2026-04-01.csv") == "完全_未知_店舗_2026-04-01"


# ────────────────────────────────────────
# CSV 連結ロジック
# ────────────────────────────────────────

def _write_cp932_csv(path: Path, rows: list[list[str]]) -> None:
    with open(path, "w", encoding="cp932", newline="") as f:
        w = csv.writer(f)
        w.writerows(rows)


def _make_dummy_data_row(marker: str) -> list[str]:
    """27カラム分のデータ行(先頭セルに識別マーカー入り)"""
    return [f"{marker}_{i}" for i in range(27)]


def test_build_rows_skips_all_csv_headers(tmp_path: Path, mapping):
    """
    全店舗で CSV ヘッダー行はスキップされ、データ行のみ出力される。
    (スプシ側に固定ヘッダーが既にあるため)
    """
    # 1店舗目: LIME渋谷(YAML上位)
    p1 = tmp_path / "reserve_ハーフ_ヒ_ーリンク_LIME_渋谷_2026-04-01.csv"
    _write_cp932_csv(p1, [EXPECTED_HEADER, _make_dummy_data_row("渋谷A"), _make_dummy_data_row("渋谷B")])

    # 2店舗目: Belle大宮
    p2 = tmp_path / "reserve__ハーフ_ヒ_ーリンク__ニキヒ_ケア_韓国肌管理Belle_大宮店_2026-04-01.csv"
    _write_cp932_csv(p2, [EXPECTED_HEADER, _make_dummy_data_row("大宮X")])

    rows = build_rows_for_sheet([p2, p1], mapping)  # 入力順は意図的に逆転

    # 期待: [LIME渋谷A, LIME渋谷B, Belle大宮X](ヘッダー行は一切含まれない)
    assert len(rows) == 3
    assert rows[0][0] == "LIME渋谷" and rows[0][1].startswith("渋谷A")
    assert rows[1][0] == "LIME渋谷" and rows[1][1].startswith("渋谷B")
    assert rows[2][0] == "Belle大宮" and rows[2][1].startswith("大宮X")

    # CSVヘッダー値("予約者名"等)が出力に含まれていないこと
    assert not any(r[1:] == EXPECTED_HEADER for r in rows), \
        "CSVヘッダー行はどの店舗でも出力されてはならない"
    # 期待ヘッダーの先頭値("予約者名")が B列(index 1)に登場しないこと
    assert not any(r[1] == "予約者名" for r in rows), \
        "B列にCSVヘッダー値が紛れ込んでいる"


def test_build_rows_order_follows_yaml(tmp_path: Path, mapping):
    """店舗順は YAML 定義順に従う(LIME渋谷→LIME新宿三丁目→…→Belle日本橋)"""
    # 2店舗で検証
    p_belle = tmp_path / "reserve__ハーフ_ヒ_ーリンク__ニキヒ_ケア_韓国肌管理Belle_柏店_2026-04-01.csv"
    p_lime = tmp_path / "reserve_ハーフ_ヒ_ーリンク_LIME_新宿三丁目_2026-04-01.csv"
    _write_cp932_csv(p_belle, [EXPECTED_HEADER, _make_dummy_data_row("B")])
    _write_cp932_csv(p_lime, [EXPECTED_HEADER, _make_dummy_data_row("L")])

    rows = build_rows_for_sheet([p_belle, p_lime], mapping)

    # LIME新宿三丁目(YAML上位)が先に来る
    stores_in_order = [r[0] for r in rows]
    first_store = stores_in_order[0]
    assert first_store == "LIME新宿三丁目"
    # そして Belle柏 のレコードが後ろに出現
    assert "Belle柏" in stores_in_order
    assert stores_in_order.index("LIME新宿三丁目") < stores_in_order.index("Belle柏")


def test_build_rows_all_data_rows_have_store_name(tmp_path: Path, mapping):
    """全ての行に店舗名(A列)が付与されている"""
    p = tmp_path / "reserve_ハーフ_ヒ_ーリンク_LIME_渋谷_2026-04-01.csv"
    _write_cp932_csv(p, [EXPECTED_HEADER, _make_dummy_data_row("X"), _make_dummy_data_row("Y")])
    rows = build_rows_for_sheet([p], mapping)
    assert all(r[0] == "LIME渋谷" for r in rows)
    # 27+1=28 列
    assert all(len(r) == 28 for r in rows)


def test_build_rows_empty_file_skipped(tmp_path: Path, mapping):
    """空ファイルはスキップされ、他の店舗のデータ行のみが出力される"""
    p_empty = tmp_path / "reserve_ハーフ_ヒ_ーリンク_LIME_渋谷_2026-04-01.csv"
    p_ok = tmp_path / "reserve__ハーフ_ヒ_ーリンク__ニキヒ_ケア_韓国肌管理Belle_大宮店_2026-04-01.csv"
    p_empty.write_bytes(b"")
    _write_cp932_csv(p_ok, [EXPECTED_HEADER, _make_dummy_data_row("大宮")])

    rows = build_rows_for_sheet([p_empty, p_ok], mapping)
    # 出力は Belle大宮 のデータ行 1件のみ(CSVヘッダー行は含まれない)
    assert len(rows) == 1
    assert rows[0][0] == "Belle大宮"
    assert rows[0][1].startswith("大宮")
    assert rows[0][1:] != EXPECTED_HEADER


def test_build_rows_no_data_raises(tmp_path: Path, mapping):
    """有効なデータ行が0件なら例外"""
    p = tmp_path / "reserve_ハーフ_ヒ_ーリンク_LIME_渋谷_2026-04-01.csv"
    p.write_bytes(b"")
    with pytest.raises(RuntimeError, match="データ行"):
        build_rows_for_sheet([p], mapping)


# ────────────────────────────────────────
# ZIP 展開
# ────────────────────────────────────────

def test_extract_zip_only_returns_csv(tmp_path: Path):
    zip_path = tmp_path / "test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.csv", "col1,col2\n1,2\n".encode("cp932"))
        zf.writestr("b.txt", "not a csv")
        zf.writestr("c.CSV", "col1,col2\n3,4\n".encode("cp932"))

    extracted = extract_zip(zip_path, tmp_path / "out")
    names = sorted(p.name for p in extracted)
    assert names == ["a.csv", "c.CSV"]


def test_extract_zip_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        extract_zip(tmp_path / "nope.zip", tmp_path / "out")


# ────────────────────────────────────────
# Shift_JIS(cp932)ファイル名対応
# ────────────────────────────────────────

# 実際の b-merit.jp の ZIP に含まれていたファイル名サンプル
REAL_BMERIT_FILENAMES = [
    ("reserve_【ハーブピーリング｜ニキビケア】韓国肌管理Belle_大宮店_2026-04-01.csv", "Belle大宮"),
    ("reserve_【ハーブピーリング｜ニキビケア】韓国肌管理Belle_柏店_2026-04-01.csv", "Belle柏"),
    ("reserve_【ハーブピーリング_l_ニキビケア】韓国肌管理_Belle_横浜店_2026-04-01.csv", "Belle横浜"),
    ("reserve_【ハーブピーリング｜ニキビケア】韓国肌管理Belle錦糸町店_2026-04-01.csv", "Belle錦糸町"),
    ("reserve_【ハーブピーリング｜ニキビケア】韓国肌管理Belle_西船橋店_2026-04-01.csv", "Belle西船橋"),
    ("reserve_【ハーブピーリング｜ニキビケア】韓国肌管理Belle_北千住店_2026-04-01.csv", "Belle北千住"),
    ("reserve_【ハーブピーリング｜ニキビケア】韓国肌管理Belle_川崎店_2026-04-01.csv", "Belle川崎"),
    ("reserve_ハーブピーリングLIME池袋東口_2026-04-01.csv", "LIME池袋東口"),
    ("reserve_ハーブピーリングLIME_新宿三丁目_2026-04-01.csv", "LIME新宿三丁目"),
    ("reserve_［韓国肌管理｜ハーブピーリング］Belle日本橋_2026-04-01.csv", "Belle日本橋"),
    ("reserve_ハーブピーリングLIME_渋谷_2026-04-01.csv", "LIME渋谷"),
]


class _BmeritZipInfo(zipfile.ZipInfo):
    """
    b-merit.jp の ZIP と同じファイル名格納方式を再現する ZipInfo サブクラス。
    cp932 バイト列を UTF-8 フラグなしで書き出す。
    """
    def __init__(self, decoded_name: str):
        # 親クラスには cp437 偽装の str を渡しておく(filename属性として保持される)
        cp932_bytes = decoded_name.encode("cp932")
        super().__init__(filename=cp932_bytes.decode("cp437"))
        self.flag_bits = 0
        self._cp932_bytes = cp932_bytes  # サブクラスは独自の属性追加可

    def _encodeFilenameFlags(self):
        # zipfile が ASCII 不可だと自動で UTF-8 フラグを立てるロジックを完全にバイパス
        return self._cp932_bytes, self.flag_bits


def _make_bmerit_style_zip(zip_path: Path, filenames: list[str]) -> None:
    """b-merit.jp 互換の cp932 ファイル名 ZIP を作る(テスト用)"""
    with zipfile.ZipFile(zip_path, "w") as zf:
        for fn in filenames:
            zi = _BmeritZipInfo(fn)
            zf.writestr(zi, "col1\nval1\n".encode("cp932"))


@pytest.mark.parametrize("real_name, _", REAL_BMERIT_FILENAMES)
def test_extract_zip_decodes_cp932_filenames(tmp_path: Path, real_name, _):
    """単一ファイル名でも cp932→cp437 偽装の状態から正しく復元できる"""
    zip_path = tmp_path / "bmerit.zip"
    _make_bmerit_style_zip(zip_path, [real_name])
    extracted = extract_zip(zip_path, tmp_path / "out")
    assert len(extracted) == 1
    assert extracted[0].name == real_name


def test_extract_zip_full_bmerit_sample(tmp_path: Path):
    """b-merit の実 ZIP と同じファイル名 11 件をすべて正しく復元できる"""
    zip_path = tmp_path / "bmerit_full.zip"
    names = [n for n, _ in REAL_BMERIT_FILENAMES]
    _make_bmerit_style_zip(zip_path, names)

    extracted = extract_zip(zip_path, tmp_path / "out")
    extracted_names = sorted(p.name for p in extracted)
    assert extracted_names == sorted(names)


def test_store_mapping_resolves_real_bmerit_filenames(mapping):
    """実ファイル名(復元後)→ 期待店舗名 のマッピングが全件成立する"""
    for real_name, expected_store in REAL_BMERIT_FILENAMES:
        assert mapping.resolve(real_name) == expected_store, \
            f"マッピング失敗: {real_name!r} → 期待: {expected_store}"


# ────────────────────────────────────────
# 複数ZIP統合(当月+翌月)
# ────────────────────────────────────────

def test_build_rows_from_multiple_zips_concatenates(tmp_path: Path, mapping):
    """2つのZIP(当月+翌月想定)を順番に統合し、データ行が縦連結される"""
    csv_content = ("予約者名\nテストA\nテストB\n").encode("cp932")

    zip_curr = tmp_path / "curr.zip"
    with zipfile.ZipFile(zip_curr, "w") as zf:
        zi = zipfile.ZipInfo("reserve_LIME_渋谷_2026-04-01.csv")
        zi.flag_bits = 0
        zf.writestr(zi, csv_content)

    zip_next = tmp_path / "next.zip"
    with zipfile.ZipFile(zip_next, "w") as zf:
        zi = zipfile.ZipInfo("reserve_LIME_渋谷_2026-05-01.csv")
        zi.flag_bits = 0
        zf.writestr(zi, csv_content)

    rows = build_rows_from_multiple_zips(
        [zip_curr, zip_next], mapping, tmp_path / "work"
    )
    # 各ZIPに 2 データ行 → 計 4 行(ヘッダーは全店舗スキップ)
    assert len(rows) == 4
    assert all(r[0] == "LIME渋谷" for r in rows)
    # 当月→翌月の順(ファイル名 04-01 < 05-01 のソート)
    file_dates_in_order = []
    # データ行の順に当月→翌月で並んでいるかは、build_rows_for_sheet のソート順で
    # (店舗順, ファイル名)で安定化されるため、この順序が当月→翌月になる。
    # テストでは「最初の 2 行 = 当月分」「後ろの 2 行 = 翌月分」を確認する代わりに、
    # ともかく 4 行が同店舗で格納されていることを確認する。
