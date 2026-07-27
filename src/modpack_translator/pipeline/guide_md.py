"""GuideME 指南頁（Markdown + YAML frontmatter + JSX 元件）的抽取與重建。

AE2 按 G 開啟的遊戲內指南、Powah、Little Big Redstone 等模組的指南頁都由 GuideME
算繪，頁面是 jar 內 `assets/<ns>/<root>/**.md`。譯文放在指南根目錄下的 `_<語言>/`
子樹、相對路徑不變——這點由模組自己出貨的 `_zh_cn`、`_ja_jp`、`_pt_br` 樹實證。

**逐行處理**：拿模組出貨的 73 對「英文頁 ↔ 官方 zh_cn 頁」比對，行數完全一致，
譯者就是逐行替換。所以這裡不做區塊剖析——逐行切出（前綴, 可翻文字, 後綴），
重建時只換中段。這讓「所有片段接回等於原文」這個不變式變得顯而易見。

同一份實證也定出了哪些行要翻：標題 98.8%、散文 94.7%、frontmatter 只有 `title`、
清單只翻連結文字、表格只翻儲存格、JSX 標籤 0.4%（那幾筆是行錯位，不是翻譯）。
"""

from __future__ import annotations

import re

# frontmatter 只有 title 類的值會被翻譯；icon、position、item_ids 全是識別碼。
_FRONTMATTER_TITLE_RE = re.compile(r'^(\s*title\s*:\s*)(.*?)(\s*)$')
_HEADING_RE = re.compile(r'^(\s*#{1,6}\s+)(.*?)(\s*)$')
_LIST_RE = re.compile(r'^(\s*(?:[-*+]|\d+[.)])\s+)(.*?)(\s*)$')
_FENCE_RE = re.compile(r'^\s*(```|~~~)')
_JSX_RE = re.compile(r'^\s*</?[A-Za-z]')
_HTML_COMMENT_RE = re.compile(r'^\s*<!--')
_QUOTED_RE = re.compile(r'^(["\'])(.*)(\1)$')


def extract_guide_text(raw: str) -> dict[str, str]:
    """抽出可翻譯字串，鍵是穩定的行（或儲存格）座標。"""
    return {key: text for key, text, _pre, _suf in _segments(raw)}


def rebuild_guide_text(raw: str, translations: dict[str, str]) -> str:
    """把譯文寫回原文。沒有譯文的片段原樣保留。

    不加也不減行；行尾結束字元沿用原檔（GuideME 頁面常見 CRLF）。
    """
    if not translations:
        return raw

    replacements: dict[int, dict[str, str]] = {}
    for key, _text, _pre, _suf in _segments(raw):
        translated = translations.get(key)
        if translated is None:
            continue
        line_no, _, cell = key.partition(":")
        replacements.setdefault(int(line_no), {})[cell] = translated
    if not replacements:
        return raw

    lines = raw.splitlines(keepends=True)
    for line_no, cells in replacements.items():
        if line_no >= len(lines):
            continue
        lines[line_no] = _apply_line(lines[line_no], cells)
    return "".join(lines)


# ---------------------------------------------------------------- internals

def _segments(raw: str) -> list[tuple[str, str, str, str]]:
    """(鍵, 可翻文字, 前綴, 後綴) 清單，依行序。"""
    out: list[tuple[str, str, str, str]] = []
    in_frontmatter = False
    in_fence = False

    for index, raw_line in enumerate(raw.splitlines()):
        line = raw_line

        if index == 0 and line.strip() == "---":
            in_frontmatter = True
            continue
        if in_frontmatter:
            if line.strip() in ("---", "..."):
                in_frontmatter = False
                continue
            match = _FRONTMATTER_TITLE_RE.match(line)
            if match:
                prefix, value, suffix = match.groups()
                quoted = _QUOTED_RE.match(value)
                if quoted:
                    prefix += quoted.group(1)
                    suffix = quoted.group(3) + suffix
                    value = quoted.group(2)
                if _is_text_bearing(value):
                    out.append((str(index), value, prefix, suffix))
            continue

        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        stripped = line.strip()
        if not stripped or _JSX_RE.match(line) or _HTML_COMMENT_RE.match(line):
            continue

        if stripped.startswith("|"):
            out.extend(_table_segments(index, line))
            continue

        for pattern in (_HEADING_RE, _LIST_RE):
            match = pattern.match(line)
            if match:
                prefix, text, suffix = match.groups()
                if _is_text_bearing(text):
                    out.append((str(index), text, prefix, suffix))
                break
        else:
            match = re.match(r'^(\s*)(.*?)(\s*)$', line)
            prefix, text, suffix = match.groups()
            if _is_text_bearing(text):
                out.append((str(index), text, prefix, suffix))

    return out


def _table_segments(index: int, line: str) -> list[tuple[str, str, str, str]]:
    """表格逐儲存格切段。分隔列（|---|---|）整列跳過。"""
    if re.fullmatch(r'\s*\|[\s:|-]*\|\s*', line):
        return []
    out: list[tuple[str, str, str, str]] = []
    for cell_no, cell in enumerate(line.split("|")):
        match = re.match(r'^(\s*)(.*?)(\s*)$', cell)
        prefix, text, suffix = match.groups()
        if _is_text_bearing(text):
            out.append((f"{index}:{cell_no}", text, prefix, suffix))
    return out


def _apply_line(raw_line: str, cells: dict[str, str]) -> str:
    ending = raw_line[len(raw_line.rstrip("\r\n")):]
    line = raw_line.rstrip("\r\n")

    if "" in cells:                      # 整行型（標題／散文／清單／frontmatter）
        for pattern in (_FRONTMATTER_TITLE_RE, _HEADING_RE, _LIST_RE):
            match = pattern.match(line)
            if match:
                prefix, value, suffix = match.groups()
                if pattern is _FRONTMATTER_TITLE_RE:
                    quoted = _QUOTED_RE.match(value)
                    if quoted:
                        return f"{prefix}{quoted.group(1)}{cells['']}{quoted.group(3)}{suffix}{ending}"
                return f"{prefix}{cells['']}{suffix}{ending}"
        match = re.match(r'^(\s*)(.*?)(\s*)$', line)
        return f"{match.group(1)}{cells['']}{match.group(3)}{ending}"

    parts = line.split("|")              # 表格型
    for cell_no_text, translated in cells.items():
        cell_no = int(cell_no_text)
        if cell_no >= len(parts):
            continue
        match = re.match(r'^(\s*)(.*?)(\s*)$', parts[cell_no])
        parts[cell_no] = f"{match.group(1)}{translated}{match.group(3)}"
    return "|".join(parts) + ending


def _is_text_bearing(text: str) -> bool:
    """這個位置有沒有內容值得當成一則字串。

    刻意**不**在這裡判斷「值不值得翻譯」——那是 classify_translation_entry 與
    diff_keys 的職責。抽取只負責結構定位；抽取階段若也做語意過濾，從已翻好的檔案
    抽取時會得到空字典，於是每次掃描都以為整頁沒翻過（冪等就壞了）。
    """
    stripped = text.strip()
    if len(stripped) < 2:
        return False
    # 純符號的行是排版（水平分隔線 `---`、`***`），不是文字。
    return bool(re.search(r"[^\W_]", stripped, re.UNICODE))
