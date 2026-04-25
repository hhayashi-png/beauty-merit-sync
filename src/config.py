"""環境変数・YAML設定の読み込み"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from .logger import get_logger

logger = get_logger("config")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DEBUG_DIR = PROJECT_ROOT / "debug"
STORE_MAPPING_PATH = CONFIG_DIR / "store_mapping.yml"

# ローカル実行時は .env を自動ロード(CI では無視される)
load_dotenv(PROJECT_ROOT / ".env", override=False)


# 期待する CSV ヘッダー(27カラム)
EXPECTED_HEADER: list[str] = [
    "予約者名", "予約者名(カナ)", "住所", "TEL", "メールアドレス", "生年月日",
    "予約経路", "予約番号", "サイトカスタマーID", "予約日", "予約時間", "来店日時",
    "施術開始時間", "施術終了時間", "料金", "クーポン情報", "メニュー情報",
    "メニューオプション情報", "お客様番号", "スタッフ", "利用ポイント", "来店処理",
    "付与ポイント", "転記", "スタッフ指名", "性別", "予約属性",
]
EXPECTED_COL_COUNT = len(EXPECTED_HEADER)  # 27


def _env(key: str, default: str | None = None, required: bool = False) -> str | None:
    v = os.environ.get(key, default)
    if required and not v:
        raise RuntimeError(f"環境変数 {key} が未設定です")
    return v


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class AppConfig:
    bmerit_login_id: str
    bmerit_password: str
    spreadsheet_id: str
    sheet_name: str
    google_credentials: dict[str, Any]
    headless: bool
    debug_dump: bool
    slack_webhook_url: str | None = None


def load_google_credentials() -> dict[str, Any]:
    """
    サービスアカウント鍵の読み込み。以下のどれでも受け付ける:
      1. GOOGLE_CREDENTIALS_JSON に JSON 文字列(GitHub Actions での想定)
      2. GOOGLE_CREDENTIALS_JSON にローカルファイルのパス(ローカル開発での想定)
      3. GOOGLE_CREDENTIALS_PATH にファイルパス(明示的な指定)
    """
    raw = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if raw:
        stripped = raw.strip()
        # JSON 文字列なら '{' で始まるはず。そうでなければパスとして扱う。
        if stripped.startswith("{"):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"GOOGLE_CREDENTIALS_JSON のJSONパース失敗: {e}") from e
        # パスとして解釈
        p = Path(stripped).expanduser()
        if p.exists():
            logger.info(f"GOOGLE_CREDENTIALS_JSON をファイルパスとして読み込み: {p}")
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        raise RuntimeError(
            f"GOOGLE_CREDENTIALS_JSON の値が JSON 文字列でもなく、ファイルとしても見つかりません: {stripped!r}"
        )

    path = os.environ.get("GOOGLE_CREDENTIALS_PATH")
    if path:
        p = Path(path).expanduser()
        if p.exists():
            logger.info(f"GOOGLE_CREDENTIALS_PATH から読み込み: {p}")
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        raise RuntimeError(f"GOOGLE_CREDENTIALS_PATH が存在しません: {p}")

    raise RuntimeError(
        "Google認証情報がありません。"
        "GOOGLE_CREDENTIALS_JSON(JSON文字列 or ファイルパス)または "
        "GOOGLE_CREDENTIALS_PATH(ファイルパス)を設定してください。"
    )


def load_app_config() -> AppConfig:
    return AppConfig(
        bmerit_login_id=_env("BMERIT_LOGIN_ID", required=True),  # type: ignore[arg-type]
        bmerit_password=_env("BMERIT_PASSWORD", required=True),  # type: ignore[arg-type]
        spreadsheet_id=_env(
            "SPREADSHEET_ID",
            default="1aykK8ll0upbVoJnJJrXBp_cAXBovyI6hc4XzoI8GZ10",
        ),  # type: ignore[arg-type]
        sheet_name=_env("SHEET_NAME", default="予約情報csv"),  # type: ignore[arg-type]
        google_credentials=load_google_credentials(),
        headless=_env_bool("HEADLESS", True),
        debug_dump=_env_bool("DEBUG_DUMP", False),
        slack_webhook_url=_env("SLACK_WEBHOOK_URL", default=None) or None,
    )


# ── 店舗マッピング ────────────────────────────────

@dataclass
class StoreRule:
    name: str
    all_keywords: list[str]


@dataclass
class StoreMapping:
    rules: list[StoreRule]
    fallback_use_filename: bool

    def resolve(self, filename: str) -> str | None:
        """
        ファイル名(拡張子は自動で除去)から店舗名を返す。
        マッチしない場合:
          - use_filename_as_name=True → ファイル名(拡張子なし)を返す
          - False → None
        """
        stem = Path(filename).stem
        for rule in self.rules:
            if all(kw in stem for kw in rule.all_keywords):
                return rule.name

        if self.fallback_use_filename:
            logger.warning(
                f"店舗マッピング未該当: '{filename}' — "
                f"フォールバックとしてファイル名をそのまま店舗名に使用します。"
                f"config/store_mapping.yml に対応店舗を追加することを推奨します。"
            )
            return stem

        logger.error(f"店舗マッピング未該当: '{filename}' (fallback=false のため失敗)")
        return None

    def store_order_index(self, name: str) -> int:
        """YAMLでの定義順を返す(未定義は末尾扱い)"""
        for i, rule in enumerate(self.rules):
            if rule.name == name:
                return i
        return len(self.rules)


def load_store_mapping(path: Path | str | None = None) -> StoreMapping:
    p = Path(path) if path else STORE_MAPPING_PATH
    if not p.exists():
        raise FileNotFoundError(f"店舗マッピングが見つかりません: {p}")

    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    rules = []
    for entry in data.get("stores", []):
        name = entry.get("name")
        match = entry.get("match", {}) or {}
        all_kw = match.get("all", []) or []
        if not name or not all_kw:
            logger.warning(f"store_mapping.yml: 不完全なエントリをスキップ: {entry}")
            continue
        rules.append(StoreRule(name=name, all_keywords=list(all_kw)))

    fallback = (data.get("fallback") or {}).get("use_filename_as_name", True)
    return StoreMapping(rules=rules, fallback_use_filename=bool(fallback))
