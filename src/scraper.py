"""
Playwright(sync)でビューティーメリット管理画面にログインし、
予約ダウンロード画面から「当月」の一括ZIPをダウンロードする。

⚠️ セレクタは推定値。実サイトのDevToolsで確認しながら調整する必要がある。
   - 調整のしやすさのため、セレクタはすべて Selectors クラスにまとめている。
   - ローカル開発時は HEADLESS=false で目視デバッグ可能。
   - 失敗時 or DEBUG_DUMP=true の時、debug/ にスクリーンショット + HTML を保存。
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PWTimeout, sync_playwright

from .config import DEBUG_DIR
from .logger import get_logger

logger = get_logger("scraper")


# ════════════════════════════════════════════
# セレクタ定義(ここだけ編集すれば UI 変更に追従できる)
# ════════════════════════════════════════════

class Selectors:
    """
    すべての CSS / Playwright セレクタを一箇所に集約。
    - 優先順位が高い候補を先頭に並べ、_click_first / _fill_first でフォールバックする。
    - `# TODO: 要実サイト確認` がついている項目は推定値。
    """

    LOGIN_URL = "https://b-merit.jp/groupmanage/login/"

    # TODO: 要実サイト確認 — ログインID入力欄
    LOGIN_ID_INPUTS = [
        'input[name="login_id"]',
        'input[type="email"]',
        'input[name*="mail"]',
        'input[name*="login"]',
        'input[name*="id"]',
        'input[type="text"]',
    ]

    LOGIN_PASSWORD_INPUT = 'input[type="password"]'

    # TODO: 要実サイト確認 — ログインボタン
    LOGIN_SUBMITS = [
        'button[type="submit"]',
        'input[type="submit"]',
        'button:has-text("ログイン")',
    ]

    # TODO: 要実サイト確認 — 予約ダウンロード画面URLの直打ち候補
    RESERVATION_DOWNLOAD_URLS = [
        "https://b-merit.jp/groupmanage/top/?action=reservedownloadlist",
        "https://b-merit.jp/groupmanage/top/?action=reservationdownload",
        "https://b-merit.jp/groupmanage/reservation/download",
    ]

    # TODO: 要実サイト確認 — メニューからのリンクテキスト
    RESERVATION_MENU_TEXTS = ["予約ダウンロード", "予約DL", "予約CSVダウンロード"]

    # TODO: 要実サイト確認 — 「当月」ボタン(対象月=当月のとき優先される近道UI)
    MONTH_CURRENT_CLICK = [
        'input[type="radio"][value="current"]',
        'label:has-text("当月")',
        'button:has-text("当月")',
        'text=当月',
    ]
    # TODO: 要実サイト確認 — 「翌月」ボタン(対象月=翌月のとき優先される近道UI)
    MONTH_NEXT_CLICK = [
        'input[type="radio"][value="next"]',
        'label:has-text("翌月")',
        'button:has-text("翌月")',
        'text=翌月',
    ]
    # 任意年月の指定UI(年・月別 select / 単一 select / input[type=month])
    MONTH_YEAR_SELECT = 'select[name*="year"]'
    MONTH_MONTH_SELECT = 'select[name*="month"]'
    MONTH_YM_SELECT = 'select[name*="ym"], select[name*="yearmonth"]'
    MONTH_INPUT = 'input[type="month"], input[name*="target_month"], input[name*="ym"]'

    # TODO: 要実サイト確認 — 一括ダウンロードボタン
    BULK_DOWNLOAD = [
        'button:has-text("一括ダウンロード")',
        'input[type="submit"][value*="一括ダウンロード"]',
        'a:has-text("一括ダウンロード")',
        '#bulk-download',
    ]


DEFAULT_TIMEOUT_MS = 30_000
DOWNLOAD_TIMEOUT_MS = 60_000
MAX_RETRIES = 3


# ════════════════════════════════════════════
# ヘルパー
# ════════════════════════════════════════════

def _dump(page: Page, label: str) -> None:
    """debug/ にスクショとHTMLを保存"""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = DEBUG_DIR / f"{ts}_{label}"
    try:
        page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
        (base.with_suffix(".html")).write_text(page.content(), encoding="utf-8")
        logger.info(f"debug保存: {base}.{{png,html}}")
    except Exception as e:
        logger.warning(f"debug保存失敗: {e}")


def _click_first(page: Page, selectors: list[str], label: str) -> bool:
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                logger.info(f"{label}: '{sel}' でクリック")
                return True
        except Exception:
            continue
    return False


def _fill_first(page: Page, selectors: list[str], value: str, label: str) -> bool:
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.fill(value)
                logger.info(f"{label}: '{sel}' に入力")
                return True
        except Exception:
            continue
    return False


# ════════════════════════════════════════════
# ステップ
# ════════════════════════════════════════════

def _login(page: Page, login_id: str, password: str, debug_dump: bool) -> None:
    logger.info(f"ログインページへ遷移: {Selectors.LOGIN_URL}")
    page.goto(Selectors.LOGIN_URL, wait_until="networkidle", timeout=DEFAULT_TIMEOUT_MS)

    if not _fill_first(page, Selectors.LOGIN_ID_INPUTS, login_id, "ログインID"):
        _dump(page, "login_no_id_field")
        raise RuntimeError("ログインID入力欄が見つかりません")

    pw_el = page.query_selector(Selectors.LOGIN_PASSWORD_INPUT)
    if not pw_el:
        _dump(page, "login_no_pw_field")
        raise RuntimeError("パスワード入力欄が見つかりません")
    pw_el.fill(password)

    if not _click_first(page, Selectors.LOGIN_SUBMITS, "ログインボタン"):
        _dump(page, "login_no_submit")
        raise RuntimeError("ログインボタンが見つかりません")

    try:
        page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)
    except PWTimeout:
        pass

    still = page.query_selector(Selectors.LOGIN_PASSWORD_INPUT)
    if still and still.is_visible():
        _dump(page, "login_failed")
        raise RuntimeError("ログイン失敗(PW入力欄が残存)。ID/PWを確認してください")

    logger.info("ログイン成功")
    if debug_dump:
        _dump(page, "after_login")


def _go_reservation_download(page: Page) -> None:
    # まずURL直打ちを順に試す
    for url in Selectors.RESERVATION_DOWNLOAD_URLS:
        try:
            logger.info(f"予約DL画面URL試行: {url}")
            page.goto(url, wait_until="networkidle", timeout=DEFAULT_TIMEOUT_MS)
            body = page.inner_text("body")
            if ("一括ダウンロード" in body) or ("予約ダウンロード" in body):
                logger.info(f"予約DL画面に到達: {url}")
                return
        except Exception as e:
            logger.warning(f"URL遷移失敗: {url} ({e})")

    # リンククリックでフォールバック
    for txt in Selectors.RESERVATION_MENU_TEXTS:
        try:
            link = page.get_by_role("link", name=txt)
            if link.count() > 0:
                link.first.click()
                page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)
                logger.info(f"リンク '{txt}' から予約DL画面へ遷移")
                return
        except Exception:
            continue

    _dump(page, "no_reservation_page")
    raise RuntimeError("予約ダウンロード画面へ遷移できません")


def _set_target_month(page: Page, year: int, month: int, month_kind: str = "current") -> None:
    """
    対象年月を画面に設定する。
    month_kind: "current"(当月) / "next"(翌月) / "any"(任意年月)
      - "current"/"next" の場合、近道UI(当月/翌月ボタン)があればそれを優先
      - 近道UIが無ければ任意年月選択UIへフォールバック
    """
    label = {"current": "当月", "next": "翌月"}.get(month_kind, f"{year}-{month:02d}")

    # A) 近道UI(当月/翌月)
    if month_kind == "current":
        if _click_first(page, Selectors.MONTH_CURRENT_CLICK, "当月選択"):
            return
    elif month_kind == "next":
        if _click_first(page, Selectors.MONTH_NEXT_CLICK, "翌月選択"):
            return

    # B) 年・月 select が別々
    try:
        year_sel = page.query_selector(Selectors.MONTH_YEAR_SELECT)
        month_sel = page.query_selector(Selectors.MONTH_MONTH_SELECT)
        if year_sel and month_sel:
            year_sel.select_option(str(year))
            month_sel.select_option(str(month))
            logger.info(f"{label}設定(年月select): {year}-{month:02d}")
            return
    except Exception as e:
        logger.warning(f"年月select失敗: {e}")

    # C) 単一 select: "YYYY-MM"
    try:
        ym_sel = page.query_selector(Selectors.MONTH_YM_SELECT)
        if ym_sel:
            ym_sel.select_option(f"{year}-{month:02d}")
            logger.info(f"{label}設定(ym select): {year}-{month:02d}")
            return
    except Exception as e:
        logger.warning(f"ym select失敗: {e}")

    # D) input[type=month] / text input
    try:
        inp = page.query_selector(Selectors.MONTH_INPUT)
        if inp:
            inp.fill(f"{year}-{month:02d}")
            logger.info(f"{label}設定(input): {year}-{month:02d}")
            return
    except Exception as e:
        logger.warning(f"month input失敗: {e}")

    logger.warning(
        f"⚠️ 年月選択UIが見つかりませんでした({label})。デフォルトのまま続行します。"
    )
    _dump(page, f"no_month_selector_{month_kind}")


def _set_current_month(page: Page) -> None:
    """後方互換用エイリアス(廃止予定)"""
    now = datetime.now()
    _set_target_month(page, now.year, now.month, month_kind="current")


def _download_zip(page: Page, save_dir: Path) -> Path:
    save_dir.mkdir(parents=True, exist_ok=True)
    with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as dl_info:
        if not _click_first(page, Selectors.BULK_DOWNLOAD, "一括ダウンロード"):
            _dump(page, "no_bulk_download_btn")
            raise RuntimeError("一括ダウンロードボタンが見つかりません")

    download = dl_info.value
    out = save_dir / (download.suggested_filename or "reservations.zip")
    download.save_as(str(out))
    size = out.stat().st_size
    logger.info(f"ZIP保存: {out} ({size} bytes)")
    if size < 100:
        out.unlink(missing_ok=True)
        raise RuntimeError(f"ZIPサイズ異常: {size} bytes")
    return out


# ════════════════════════════════════════════
# エントリポイント
# ════════════════════════════════════════════

def _next_month(year: int, month: int) -> tuple[int, int]:
    """(year, month) の翌月を返す"""
    if month == 12:
        return year + 1, 1
    return year, month + 1


def _run_once_for_month(
    login_id: str,
    password: str,
    save_dir: Path,
    headless: bool,
    debug_dump: bool,
    year: int,
    month: int,
    month_kind: str,
) -> Path:
    """指定年月の ZIP を 1 回ダウンロードする"""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT_MS)
        try:
            _login(page, login_id, password, debug_dump)
            _go_reservation_download(page)
            _set_target_month(page, year, month, month_kind=month_kind)
            return _download_zip(page, save_dir)
        except Exception:
            try:
                _dump(page, f"unexpected_failure_{month_kind}_{year}-{month:02d}")
            except Exception:
                pass
            raise
        finally:
            context.close()
            browser.close()


def download_reservations_zip(
    login_id: str,
    password: str,
    save_dir: Path | str,
    headless: bool = True,
    debug_dump: bool = False,
    target_year: int | None = None,
    target_month: int | None = None,
    month_kind: str = "current",
) -> Path:
    """
    指定月の予約ZIPを取得(指数バックオフでリトライ)。

    引数:
      target_year/target_month: None なら今日の年月(=当月)を使用
      month_kind: "current" / "next" / "any"
        UI に「当月」「翌月」のショートカットボタンがある場合の優先選択ヒント
    """
    if target_year is None or target_month is None:
        now = datetime.now()
        target_year, target_month = now.year, now.month

    save_dir = Path(save_dir)
    last_err: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(
                f"ダウンロード試行 {attempt}/{MAX_RETRIES} "
                f"(対象={target_year}-{target_month:02d}, kind={month_kind})"
            )
            return _run_once_for_month(
                login_id, password, save_dir, headless, debug_dump,
                target_year, target_month, month_kind,
            )
        except Exception as e:
            last_err = e
            logger.warning(f"試行 {attempt} 失敗: {e}")
            if attempt < MAX_RETRIES:
                wait = 2 ** (attempt - 1)  # 1, 2, 4 秒
                logger.info(f"{wait}秒待機してリトライ")
                time.sleep(wait)

    raise RuntimeError(
        f"ZIPダウンロードに{MAX_RETRIES}回失敗 ({target_year}-{target_month:02d}): {last_err}"
    ) from last_err


def download_reservations_for_two_months(
    login_id: str,
    password: str,
    save_dir: Path | str,
    headless: bool = True,
    debug_dump: bool = False,
) -> list[Path]:
    """
    当月と翌月の予約ZIPをそれぞれダウンロードしてパスのリスト [当月, 翌月] を返す。
    各月のダウンロードはブラウザを起動し直す(ステートレスで失敗しにくい)。
    リネーム時の衝突回避のため、サブディレクトリ current/ next/ に分けて保存。
    """
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    cur_y, cur_m = now.year, now.month
    nxt_y, nxt_m = _next_month(cur_y, cur_m)

    logger.info(
        f"2ヶ月分ダウンロード開始: 当月={cur_y}-{cur_m:02d}, 翌月={nxt_y}-{nxt_m:02d}"
    )

    cur_zip = download_reservations_zip(
        login_id, password,
        save_dir=save_dir / "current",
        headless=headless, debug_dump=debug_dump,
        target_year=cur_y, target_month=cur_m, month_kind="current",
    )
    nxt_zip = download_reservations_zip(
        login_id, password,
        save_dir=save_dir / "next",
        headless=headless, debug_dump=debug_dump,
        target_year=nxt_y, target_month=nxt_m, month_kind="next",
    )

    logger.info(f"2ヶ月分ダウンロード完了: {[str(p) for p in (cur_zip, nxt_zip)]}")
    return [cur_zip, nxt_zip]
