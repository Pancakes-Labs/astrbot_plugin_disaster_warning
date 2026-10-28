"""
扩展多语言查找：locale/<lang>.json -> locale/zh-CN.json -> key 本身。
"""

from __future__ import annotations

import json
from pathlib import Path

HOST_LANG = "zh-CN"


class Translator:
    """按扩展目录下的 locale/*.json 翻译文案。"""

    def __init__(self, locale_dir: Path, lang: str | None = None):
        self._dir = locale_dir
        self._lang = lang or HOST_LANG
        self._cache: dict[str, dict[str, str]] = {}

    def _table(self, lang: str) -> dict[str, str]:
        if lang not in self._cache:
            try:
                data = json.loads((self._dir / f"{lang}.json").read_text("utf-8"))
                self._cache[lang] = data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                self._cache[lang] = {}  # 缺失或损坏的语言包视为空
        return self._cache[lang]

    def t(self, key: str, **fmt) -> str:
        text = self._table(self._lang).get(key)
        if text is None:
            text = self._table(HOST_LANG).get(key, key)
        text = str(text)
        try:
            return text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            return text
