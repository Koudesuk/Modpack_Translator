"""Citadel 圖鑑書頁（`.txt`）的抽取與重建。

Alex's Mobs／Alex's Caves 等模組的圖鑑內文不是 lang 檔，而是
`assets/<ns>/books/<locale>/**/*.txt`，逐檔 fallback 到 en_us。

**中文必須自己折行。** 算繪器靠半形空格斷詞，中文沒有空格，整段會直接衝出書頁。
處理方式比照模組自己出貨的官方 zh_cn 譯本：把譯文折成每行最多 32 個半形格
（實測 963 行落在 30、最大 32），段落首行縮排 4 格、續行不縮排，段間空一行。

同一份 zh_cn 譯本也證實：`{顯示文字|目標路徑}` 連結只翻前半、路徑原樣保留；
`• 30.0 ♥` 這類數值項目與開頭用來做垂直定位的空行都不動。
"""

from __future__ import annotations

import re

# 官方 zh_cn 實測：最大 32 個半形格。取 30 留餘裕（該譯本 963 行落在 30）。
WRAP_WIDTH = 30
_PARAGRAPH_INDENT = "    "

_LINK_RE = re.compile(r"\{([^{}|]*)\|([^{}]*)\}")
_BULLET_RE = re.compile(r"^(\s*•\s*)(.*)$")
_FORMAT_CODE_RE = re.compile(r"§.")


def extract_citadel_text(raw: str) -> dict[str, str]:
    """抽出可翻字串。鍵是「行號」或「行號:連結序號」。"""
    out: dict[str, str] = {}
    for index, line in enumerate(raw.splitlines()):
        for key, text in _line_segments(index, line):
            out[key] = text
    return out


def rebuild_citadel_text(raw: str, translations: dict[str, str]) -> str:
    """套用譯文並重新折行。行數會改變——這個格式本來就不是逐行對齊的。"""
    if not translations:
        return raw

    ending = "\r\n" if "\r\n" in raw else "\n"
    out: list[str] = []

    for index, line in enumerate(raw.splitlines()):
        if not line.strip():
            out.append("")
            continue

        bullet = _BULLET_RE.match(line)
        if bullet:
            prefix, body = bullet.groups()
            out.append(prefix + _apply_links(index, body, translations))
            continue

        translated = translations.get(str(index))
        if translated is None:
            out.append(_apply_links(index, line, translations))
            continue

        translated = _apply_links(index, translated, translations)
        out.extend(_wrap(translated.strip()))
        out.append("")                    # 段間空行，比照官方 zh_cn 排版

    while out and out[-1] == "":
        out.pop()
    return ending.join(out) + ending


# ---------------------------------------------------------------- internals

def _line_segments(index: int, line: str) -> list[tuple[str, str]]:
    if not line.strip():
        return []

    links = _LINK_RE.findall(line)
    segments = [
        (f"{index}:{n}", text) for n, (text, _target) in enumerate(links) if text.strip()
    ]

    bullet = _BULLET_RE.match(line)
    body = bullet.group(2) if bullet else line
    remainder = _LINK_RE.sub("", body).strip()
    if remainder and not bullet:
        segments.append((str(index), line.strip()))
    elif remainder and bullet:
        segments.append((f"{index}:t", remainder))
    return segments


def _apply_links(index: int, text: str, translations: dict[str, str]) -> str:
    """替換連結顯示文字與純文字項目，路徑一律保留。"""
    counter = [0]

    def replace(match: re.Match) -> str:
        key = f"{index}:{counter[0]}"
        counter[0] += 1
        return "{%s|%s}" % (translations.get(key, match.group(1)), match.group(2))

    result = _LINK_RE.sub(replace, text)
    plain = translations.get(f"{index}:t")
    if plain is not None and not _LINK_RE.search(text):
        return plain
    return result


def _wrap(text: str) -> list[str]:
    """折成每行最多 WRAP_WIDTH 個半形格；首行縮排 4 格。

    §x 格式碼不佔顯示寬度，計算時排除，但輸出保留。
    """
    lines: list[str] = []
    indent = _PARAGRAPH_INDENT
    current = ""
    for token in _tokens(text):
        candidate = current + token
        if current and _display_width(indent + candidate) > WRAP_WIDTH:
            lines.append(indent + current)
            indent = ""
            current = token.lstrip(" ")
        else:
            current = candidate
    if current or not lines:
        lines.append(indent + current)
    return lines


def _tokens(text: str) -> list[str]:
    """切成不可再分的單位：CJK 逐字，ASCII 詞（含前導空白）整塊。"""
    return re.findall(r"\s*(?:§.)*[⺀-鿿＀-￯]|\s*(?:§.|[^\s⺀-鿿＀-￯])+", text)


def _display_width(text: str) -> int:
    plain = _FORMAT_CODE_RE.sub("", text)
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in plain)
