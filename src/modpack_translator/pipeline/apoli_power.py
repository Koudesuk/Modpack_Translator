"""Origins／Apoli 能力定義裡的字面顯示文字。

`data/<ns>/powers/**.json` 與 `data/<ns>/origins/**.json` 允許 `name`、`description`
直接寫字面英文，不走 lang 鍵。只掃 lang 的話，能力面板整片是英文，而且使用者
在任何失敗清單裡都看不到它們——因為它們從頭到尾沒被當成翻譯目標。

**這個格式最危險的地方**：條件節點也有 `name`。`damage_condition` 底下的
`name: "fall"` 是傷害類型 ID，翻成中文之後能力不會報錯，只會安靜失效。所以抽取
規則是兩道，缺一都會出事：

1. **結構子樹整棵剪掉**——路徑一旦經過 `*_condition`／`*_action`／`*_modifier`／
   `predicate`／`filter`／`source` 這類鍵，底下的東西一律不是給玩家看的。
2. **值必須長得像顯示文字**——顯示名稱是標題或句子（有大寫或有空白），ID 是一串
   小寫。`"fall"`、`"lava"` 這種即使漏過第一道也擋得下來。
"""

from __future__ import annotations

import re
from typing import Any

from modpack_translator.pipeline.preprocessor import (
    _has_translatable_text,
    _parse_patchouli_path_key,
    _patchouli_path_key,
)

# 大小寫敏感：Apoli 一律小寫，NBT 的 `display.Name` 不是顯示名稱的來源。
TEXT_KEYS = ("name", "description")

# `data/<ns>/<這裡>/…`。Origins 的能力與起源定義都長同一個樣子。
POWER_DIRS = frozenset({"powers", "origins"})

_STRUCTURAL_KEY_SUFFIXES = (
    "_condition",
    "_action",
    "_modifier",
    "_predicate",
    "_filter",
    "_source",
    "_type",
)
_STRUCTURAL_KEYS = frozenset({
    "action",
    "actions",
    "block",
    "condition",
    "conditions",
    "entity",
    "filter",
    "filters",
    "fluid",
    "item",
    "items",
    "modifier",
    "modifiers",
    "nbt",
    "predicate",
    "predicates",
    "source",
    "sources",
    "stack",
    "tag",
})

_UPPER_RE = re.compile(r"[A-Z]")
_SPACE_RE = re.compile(r"\s")
_CJK_RE = re.compile(r"[㐀-鿿]")


def is_power_member(parts: list[str]) -> bool:
    """jar／資料包內的路徑是不是能力定義檔。"""
    return (
        len(parts) >= 4
        and parts[0] == "data"
        and parts[2] in POWER_DIRS
        and parts[-1].endswith(".json")
        and not parts[-1].startswith(".")
    )


def looks_like_power_document(data: Any) -> bool:
    """確認這份 JSON 真的是 Apoli 能力或 Origins 起源。

    `powers` 這個資料夾名稱不是 Origins 專用（Palladium 之類的模組也用），光看路徑
    會抓到形狀完全不同的檔案。能力必有帶命名空間的 `type`，起源必有 `powers` 陣列，
    兩個都不是就別碰。
    """
    if not isinstance(data, dict):
        return False
    type_value = data.get("type")
    if isinstance(type_value, str) and ":" in type_value:
        return True
    return isinstance(data.get("powers"), list)


def read_power_text(data: Any) -> dict[str, str]:
    """抽出玩家看得到的字串，鍵是 JSON 路徑（與 Patchouli 同一套編碼）。"""
    return {
        _patchouli_path_key(path): value
        for path, value in _iter_power_text(data)
    }


def write_power_text(data: Any, path_key: str, value: str) -> None:
    path = _parse_patchouli_path_key(path_key)
    cursor = data
    for part in path[:-1]:
        cursor = cursor[part]
    cursor[path[-1]] = value


def _iter_power_text(data: Any, path: tuple[str | int, ...] = ()):
    if isinstance(data, dict):
        for key, value in data.items():
            if _is_structural_key(key):
                continue                      # 條件／動作子樹整棵剪掉
            child_path = path + (key,)
            if isinstance(value, str):
                if key in TEXT_KEYS and _is_display_text(value):
                    yield child_path, value
            elif isinstance(value, (dict, list)):
                yield from _iter_power_text(value, child_path)
    elif isinstance(data, list):
        for idx, value in enumerate(data):
            if isinstance(value, (dict, list)):
                yield from _iter_power_text(value, path + (idx,))


def _is_structural_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in _STRUCTURAL_KEYS or lowered.endswith(_STRUCTURAL_KEY_SUFFIXES)


def _is_display_text(value: str) -> bool:
    text = value.strip()
    if len(text) < 2:
        return False
    if _CJK_RE.search(text):
        # 譯文寫回同一個欄位，所以「已含中文」就是「這條做完了」。少了這道，
        # 保留英文專有名詞的譯文（「Applied Energistics 2 控制器」）每次重跑都會
        # 被當成沒翻，白送模型一輪再原封不動退回來。
        return False
    if not _has_translatable_text(text):
        return False                          # 資源路徑、lang 鍵、URL、色碼…
    # 顯示文字是標題或句子；ID 是一串小寫。這一道專門擋漏網的 "fall"、"lava"。
    return bool(_UPPER_RE.search(text) or _SPACE_RE.search(text))
