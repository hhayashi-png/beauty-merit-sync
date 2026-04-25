"""
スプレッドシート書き込みの統合テスト(本番認証情報を使用)。
認証・共有設定・タブ存在・書き込み権限を一気通貫で検証する。

⚠️ pytest からは除外している(認証情報を要するため)。
   実行は明示的にこのモジュールを直接叩く:

       source venv/bin/activate
       python -m tests.test_sheet_write_integration

⚠️ 実行するとスプレッドシートの A2 以降がダミーデータで上書きされます。
   本番予約データはありませんが、確認後は次回の本番実行で正しく上書きされます。
"""

from __future__ import annotations

import sys
import traceback

from src.config import load_app_config
from src.logger import get_logger
from src.sheet_writer import write_to_sheet

logger = get_logger("integration_test")


# ── ダミーデータ(全店舗データ行のみ。CSVヘッダー行は含めない) ────────
# スプシの 1 行目には固定ヘッダーが既に入っているため、CSVヘッダーは転記しない。

DUMMY_ROWS = [
    # データ行 1: LIME渋谷
    ["LIME渋谷", "テスト 太郎", "テスト タロウ", "東京都渋谷区", "09000000000",
     "test@example.com", "1990-01-01", "テスト経路", "TEST001", "C00000000",
     "2026-04-25", "10:00:00", "2026-04-25 10:00", "10:00:00", "11:00:00",
     "5000", "", "テストメニュー", "", "", "テストスタッフ", "0", "", "100",
     "未転記", "なし", "女", ""],
    # データ行 2: LIME渋谷
    ["LIME渋谷", "テスト 次郎", "テスト ジロウ", "東京都港区", "09011111111",
     "test2@example.com", "1995-05-05", "テスト経路", "TEST002", "C00000001",
     "2026-04-25", "11:00:00", "2026-04-26 14:00", "14:00:00", "15:00:00",
     "8000", "", "テストメニュー2", "", "", "テストスタッフ2", "0", "", "200",
     "未転記", "なし", "男", ""],
    # データ行 3: Belle大宮
    ["Belle大宮", "テスト 三郎", "テスト サブロウ", "埼玉県さいたま市", "09022222222",
     "test3@example.com", "1988-08-08", "Hot Pepper", "TEST003", "C00000002",
     "2026-04-26", "09:00:00", "2026-04-27 16:00", "16:00:00", "17:30:00",
     "12000", "クーポンA", "テストメニュー3", "オプション1", "", "テストスタッフ3",
     "300", "", "150", "未転記", "なし", "女", ""],
]


# ── エラー診断ヘルパー ────────────────────────────────────────

def _diagnose(exc: BaseException) -> str:
    """例外メッセージや型から原因と対処を推定して返す。
    例外チェイン(__cause__)も再帰的に見る。"""

    # 関連メッセージをすべて連結
    msgs = []
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        msgs.append(type(cur).__name__)
        msgs.append(str(cur))
        cur = cur.__cause__ or cur.__context__
    blob = "\n".join(msgs).lower()

    table = [
        # gspread の 403 / PermissionError
        (("permission", "403", "permissionerror", "caller does not have permission"),
         "原因: サービスアカウントが対象スプレッドシートに編集者として共有されていない\n"
         "対処:\n"
         "  1. credentials.json の client_email を確認(上記ログに表示済み)\n"
         "  2. スプレッドシートを開いて右上「共有」\n"
         "  3. その client_email を編集者として追加(通知送信のチェックは外してOK)\n"
         "  4. 再度このスクリプトを実行"),
        (("404", "not found"),
         "原因: SPREADSHEET_ID が間違っている、またはスプシが存在しない\n"
         "対処: .env の SPREADSHEET_ID を確認"),
        (("worksheetnotfound",),
         "原因: タブ名(SHEET_NAME)が一致しない\n"
         "対処: スプシでタブ名 '予約情報csv' の完全一致を確認(全角/半角・空白・カナ違い)"),
        (("invalid jwt signature", "invalid_grant"),
         "原因: JSON鍵が破損 or 別プロジェクトの鍵 or 期限切れ\n"
         "対処: GCPコンソールから新しい鍵を発行して credentials.json を差し替え"),
        (("filenotfounderror", "no such file"),
         "原因: credentials.json のパスが間違っている\n"
         "対処: .env の GOOGLE_CREDENTIALS_JSON を確認"),
        (("apidisabled", "has not been used", "is disabled"),
         "原因: Google Sheets API または Drive API が有効になっていない\n"
         "対処: GCPコンソールで両APIを有効化"),
    ]
    for keys, hint in table:
        if any(k in blob for k in keys):
            return hint
    return "原因: 不明 — 上記スタックトレースから判断してください"


# ── メイン ──────────────────────────────────────────────────

def main() -> int:
    print("=" * 60)
    print(" スプレッドシート書き込み 統合テスト")
    print("=" * 60)

    # 1. 設定読み込み
    print("\n[1/3] 設定を読み込み中...")
    try:
        cfg = load_app_config()
    except Exception as e:
        print(f"  ✗ 設定読み込み失敗: {e}")
        print(_diagnose(e))
        return 1

    print(f"  ✓ Spreadsheet ID : {cfg.spreadsheet_id}")
    print(f"  ✓ Sheet Name     : {cfg.sheet_name}")
    print(f"  ✓ Credentials    : project_id={cfg.google_credentials.get('project_id')}")
    print(f"                     client_email={cfg.google_credentials.get('client_email')}")

    # 2. ダミーデータ
    print(f"\n[2/3] ダミーデータを準備中...")
    print(f"  ✓ {len(DUMMY_ROWS)}行 × {len(DUMMY_ROWS[0])}列(店舗名 + 27カラム)")
    stores = sorted({r[0] for r in DUMMY_ROWS})
    print(f"  ✓ 含まれる店舗: {stores}")

    # 3. 書き込み実行
    print("\n[3/3] スプレッドシートに書き込み中...")
    try:
        write_to_sheet(
            credentials_json=cfg.google_credentials,
            spreadsheet_id=cfg.spreadsheet_id,
            sheet_name=cfg.sheet_name,
            data_rows=DUMMY_ROWS,
        )
        print("  ✓ 書き込み成功")
    except Exception as e:
        print(f"\n  ✗ 書き込み失敗: {type(e).__name__}: {e}")
        print("\n--- 推定原因と対処 ---")
        print(_diagnose(e))
        print("\n--- スタックトレース ---")
        traceback.print_exc()
        return 2

    print("\n" + "=" * 60)
    print(" ✅ テスト完了")
    print("=" * 60)
    print("\n次の URL を開いて、A2以降にダミーデータが入っていることを確認してください:")
    print(f"  https://docs.google.com/spreadsheets/d/{cfg.spreadsheet_id}/edit")
    print("\n  - A1〜AB1 のヘッダー(手動入力分)はそのまま残っているはず")
    print("  - A2 行: LIME渋谷 + テスト 太郎(いきなりデータ行)")
    print("  - A3 行: LIME渋谷 + テスト 次郎")
    print("  - A4 行: Belle大宮 + テスト 三郎")
    print("  - A5 以降: 空")
    print("\n確認後、このダミーデータは次回の本番実行で正しい予約データに上書きされます。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
