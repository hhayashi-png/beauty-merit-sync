"""アプリ全体で共通のロガー設定"""

from __future__ import annotations

import logging
import sys


_LOGGER_NAME = "bmerit_sync"
_configured = False


def get_logger(name: str | None = None) -> logging.Logger:
    """ルートロガー(bmerit_sync)を初期化して返す。モジュールごとに子ロガーを取得する用途。"""
    global _configured
    root = logging.getLogger(_LOGGER_NAME)

    if not _configured:
        root.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        root.addHandler(handler)
        root.propagate = False
        _configured = True

    if name:
        return logging.getLogger(f"{_LOGGER_NAME}.{name}")
    return root
