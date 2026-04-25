# beauty-merit-sync

ビューティーメリット(b-merit.jp)の予約情報(**当月+翌月**の2ヶ月分)を毎朝 **JST 09:00** に自動取得し、
Google スプレッドシートに反映するシステム。GitHub Actions で実行するため、PC が OFF でも動作する。

## 🔄 処理フロー

1. ビューティーメリットにログイン
2. 予約ダウンロード画面へ遷移
3. **当月** を選択 → 「一括ダウンロード」で ZIP 取得
4. ブラウザを再起動して **翌月** を選択 → ZIP 取得
5. 両 ZIP を展開(b-merit のファイル名は `cp932` 格納のため、特殊デコードで日本語復元)
6. 全店舗の CSV をデータ行のみ縦連結(店舗名は YAML マッピングで解決)
7. スプレッドシートの A2 以降をクリアして書き込み(1行目固定ヘッダーは保護)

---

## 📋 技術スタック

| 項目 | 内容 |
|------|------|
| 実行基盤 | GitHub Actions(`ubuntu-latest`) |
| 言語 | Python 3.11 |
| ブラウザ自動化 | Playwright(Chromium) |
| スプレッドシート操作 | gspread + google-auth(サービスアカウント認証) |
| CSV 処理 | 標準 `csv` + `cp932`(Shift_JIS 拡張) |
| 設定 | PyYAML(店舗マッピング) |
| シークレット管理 | GitHub Secrets |

---

## 🏗 プロジェクト構成

```
beauty-merit-sync/
├── .github/workflows/daily_sync.yml   # GitHub Actions(毎朝9時JST)
├── config/
│   └── store_mapping.yml              # 店舗名マッピング(YAMLベース)
├── src/
│   ├── main.py                        # エントリポイント
│   ├── scraper.py                     # Playwright ログイン → ZIP DL
│   ├── csv_processor.py               # ZIP展開 + CSV連結 + 店舗名付与
│   ├── sheet_writer.py                # Google Sheets 書き込み
│   ├── config.py                      # 環境変数・YAML読み込み
│   └── logger.py                      # ロガー設定
├── tests/test_csv_processor.py        # 店舗名抽出・連結の単体テスト
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## 🛠 セットアップ手順

### 1. Google Cloud Platform でサービスアカウントを作成

1. <https://console.cloud.google.com/> にアクセス
2. 上部のプロジェクトセレクタ → **新しいプロジェクト** → 名前 `beauty-merit-sync` で作成
3. 左メニュー **APIとサービス → ライブラリ**
   - **「Google Sheets API」** を検索 → **有効にする**
   - **「Google Drive API」** も検索 → **有効にする**(gspread の認証で必要)
4. 左メニュー **IAMと管理 → サービスアカウント** → **サービスアカウントを作成**
   - 名前: `beauty-merit-sync`(任意)
   - 役割の付与はスキップで OK
5. 作成したサービスアカウントをクリック → **「鍵」タブ** → **鍵を追加 → 新しい鍵を作成** → **JSON** を選択 → ダウンロード
6. 開いて `client_email` の値(`xxx@xxx.iam.gserviceaccount.com`)をコピー

> ⚠️ この JSON 鍵はローカルに保存せず、使用後削除することを推奨します。

### 2. スプレッドシートの共有設定

1. 対象スプレッドシートを開く
   <https://docs.google.com/spreadsheets/d/1aykK8ll0upbVoJnJJrXBp_cAXBovyI6hc4XzoI8GZ10/edit>
2. タブ `予約情報csv` が存在することを確認(無ければ新規作成)
3. **A1 以降の 1 行目に任意のヘッダー**を手動で入力しておく(ここは**自動同期で絶対に上書きされません**)
4. 右上 **「共有」** → 手順1でコピーした `client_email` を貼り付け → **「編集者」** → **「送信」**

### 3. GitHub リポジトリ作成 & Secrets 登録

```bash
cd beauty-merit-sync
git init
git add .
git commit -m "initial: beauty-merit-sync"
git branch -M main
git remote add origin git@github.com:<user>/<repo>.git
git push -u origin main
```

リポジトリ **Settings → Secrets and variables → Actions → New repository secret** で以下を登録:

| Name                       | Value                                                               |
| -------------------------- | ------------------------------------------------------------------- |
| `BMERIT_LOGIN_ID`          | ビューティーメリットのログイン ID                                  |
| `BMERIT_PASSWORD`          | ビューティーメリットのパスワード                                   |
| `GOOGLE_CREDENTIALS_JSON`  | 手順1でダウンロードした JSON の**全内容**(`{` から `}` まで丸ごと) |

> `GOOGLE_CREDENTIALS_JSON` は JSON 文字列そのまま貼り付け。圧縮・エスケープ不要。
> `SPREADSHEET_ID` と `SHEET_NAME` はワークフロー内に固定値を埋め込み済みのため Secrets 登録不要(変更したい場合は `.github/workflows/daily_sync.yml` を編集)。

### 4. 動作確認(手動実行)

1. リポジトリの **Actions** タブ → **「Daily Beauty Merit Sync」** を選択
2. **「Run workflow」** → `main` ブランチで実行
3. ログを確認
   - ✅ 成功 → シート `予約情報csv` の 2 行目以降に全店舗の予約データが入っているはず
   - ❌ 失敗 → **Artifacts → debug-<run_id>** をダウンロードし、スクリーンショット/HTML を見て `src/scraper.py` の `Selectors` クラスを調整
4. 成功を確認できたら、以降は毎日 JST 09:00 に自動実行

---

## 💻 ローカル実行(開発・デバッグ用)

```bash
cd beauty-merit-sync
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium

# .env を作成(.env.example が雛形)
cp .env.example .env
# エディタで .env を編集し、認証情報を埋める
#   HEADLESS=false  にするとブラウザが見える(セレクタ調整時に便利)
#   DEBUG_DUMP=true にすると成功時もスクショ/HTMLを debug/ に保存

# 実行
python -m src.main
```

### テスト実行

```bash
pip install pytest
pytest tests/ -v
```

テストは店舗名マッピング(11サンプル全部)と CSV 連結ロジックの振る舞いをカバーします。

---

## 🏪 店舗追加・変更の手順

`config/store_mapping.yml` を編集するだけ。ルールは **「ファイル名に `all` のキーワードがすべて含まれれば該当店舗」**。

### 例: 「Belle立川店」を追加

```yaml
stores:
  # ... 既存エントリ ...
  - name: "Belle立川"
    match:
      all: ["Belle", "立川"]
```

ファイル名が `reserve__..._Belle_立川店_2026-04-01.csv` 形式なら自動判定される。

### 注意点

- 評価は**上から順**、最初にマッチしたものが採用されるため、より具体的な(キーワードが多い)ルールを上に書く
- シート書き込み時の店舗並び順も YAML の **`stores` 定義順**
- 未定義のファイル名は、デフォルトでは警告ログを出した上でファイル名をそのまま店舗名に使う(`fallback.use_filename_as_name: true`)

---

## 📊 シートへの書き込み仕様

| 行          | A列            | B列以降                  |
| ----------- | -------------- | ------------------------ |
| 1           | (手動ヘッダー) | (手動ヘッダー)           |
| 2           | LIME渋谷       | LIME渋谷の実データ       |
| 3, 4, ...   | LIME渋谷       | LIME渋谷の実データ       |
| ...         | Belle大宮      | Belle大宮の実データ      |
| N           | Belle日本橋    | Belle日本橋の実データ    |

- **1行目(A1:AB1)は絶対に上書きされない**(`batch_clear(["A2:AB"])` のみ)
- A2 から全件上書き(毎回クリアしてから書き込み)
- **CSVのヘッダー行は全店舗でスキップ**(スプシ側に固定ヘッダーが既に入っているため)
- 列数: 27(CSV) + 1(店舗名) = **28列(A〜AB)**
- CSV エンコーディングは **cp932 固定**(UTF-8 ではない)
- **当月 + 翌月** の 2 ヶ月分を取得 → 1 つのタブに縦連結
  (月の境界はシート上で明示しない。`予約日` カラムから判別可能)
- **ZIP 内のファイル名は `cp932` を `cp437` 偽装で格納**(b-merit 仕様)
  → `csv_processor._decode_zip_filename` で `cp437` → `cp932` 再デコードして正しい日本語ファイル名を復元

---

## 🔧 セレクタ要調整箇所(実サイト確認が必要)

`src/scraper.py` 冒頭の `Selectors` クラスにすべて集約されています。

| 定数                              | 用途                                              |
| --------------------------------- | ------------------------------------------------- |
| `LOGIN_ID_INPUTS`                 | ID 入力欄のセレクタ候補                          |
| `LOGIN_PASSWORD_INPUT`            | パスワード入力欄                                  |
| `LOGIN_SUBMITS`                   | ログインボタン                                   |
| `RESERVATION_DOWNLOAD_URLS`       | 予約DL画面URLの直打ち候補                         |
| `RESERVATION_MENU_TEXTS`          | メニューからのリンクテキスト(URL直打ち失敗時) |
| `MONTH_CURRENT_CLICK` / 各 MONTH_* | 年月選択UI(select / input / radio / ボタン)  |
| `BULK_DOWNLOAD`                   | 一括ダウンロードボタン                            |

### セレクタ調整の進め方

1. ローカルで `.env` の `HEADLESS=false` にセット
2. `python -m src.main` 実行 → ブラウザが立ち上がって実画面を目視
3. 失敗箇所で DevTools を開いて正確なセレクタを特定
4. `src/scraper.py` の `Selectors` を書き換え
5. 失敗時は `debug/` にスクリーンショットと HTML が自動保存される(`DEBUG_DUMP=true` で成功時も)

---

## 🛡 エラー耐性

- **ログイン / ZIPダウンロード / シート書き込み**: それぞれ最大 **3回リトライ**(指数バックオフ 1s → 2s → 4s)
- **1 CSV ファイルの読み込み失敗**: スキップして他の店舗は処理続行
- **空ファイル**: スキップ
- **ヘッダー列数が想定と異なる**: 警告ログを出して処理続行
- **失敗時のデバッグ**: スクリーンショット + HTML を `debug/` に保存、GitHub Actions ではアーティファクトとして自動アップロード

---

## 🐛 トラブルシューティング

### ログインに失敗する

1. `BMERIT_LOGIN_ID` / `BMERIT_PASSWORD` Secret の値を再確認
2. ビューティーメリット側で IP 制限・二段階認証が有効化されていないか確認
3. `debug/` の `login_failed.png` を確認

### セレクタが見つからない(`input不在`など)

1. `debug/*.html` を開いて実 DOM 構造を確認
2. `src/scraper.py` の `Selectors` クラスの該当項目を実 DOM に合わせて書き換え

### 店舗名が「ファイル名そのまま」で出力される

1. ログに `店舗マッピング未該当` の警告が出ているか確認
2. `config/store_mapping.yml` にそのファイル名パターンに合う店舗を追加

### スプレッドシート書き込みで `WorksheetNotFound`

- タブ名が `予約情報csv` と**完全一致**しているか確認(半角/全角・スペース有無に注意)

### `gspread.exceptions.APIError: PERMISSION_DENIED`

- サービスアカウントの `client_email` が対象スプレッドシートに**編集者権限**で共有されているか確認

### cron が実行されない

- GitHub Actions の cron は負荷により数分遅延することがある(正常)
- リポジトリが 60日間 push なしで非活性化された場合は停止する → 定期的に push するか、手動トリガーで蘇生

---

## 🔐 セキュリティ上の注意

- 認証情報は**絶対にコードにハードコードしない**。必ず環境変数/Secrets 経由
- `.env` は `.gitignore` 済み(コミット防止)
- サービスアカウント鍵 JSON はローカルに保存せず、Secrets 登録後は削除を推奨
- ビューティーメリットのパスワードは定期的に変更し、その都度 Secret を更新
