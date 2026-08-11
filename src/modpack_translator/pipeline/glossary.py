"""用語庫：讓譯名對齊 Minecraft 官方與社群慣例。

一份對照表同時餵給三個環節，行為才會一致：

1. **整串短路**（`lookup`）——原文整串就是某個詞條時直接取官方譯名，不必推理。
   這是本地模型最大的一筆省時，也保證 1,900 多個原版名稱百分之百正確。
2. **prompt 注入**（`prompt_block`）——把原文裡出現的詞條附在 system prompt 最後，
   讓模型翻長句時沿用官方譯名。放最後是為了讓靜態前綴維持不變，prompt 快取才有效。
3. **事後強制替換**（`enforce`）——模型仍留英文原詞時，用對照表補上。

三層合併，優先序 自訂 > 模組名 > 官方；自訂條目的譯名留空字串代表刪除該詞條。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path

from modpack_translator.pipeline.preprocessor import _preserves_required_tokens

# 詞條左右必須是「非英數」才算整詞命中，避免 Stone 命中 Stonecutter。
_WORD_EDGE = r"(?<![A-Za-z0-9]){}(?![A-Za-z0-9])"


class Glossary:
    def __init__(self, terms: Mapping[str, str] | None = None) -> None:
        self._terms: dict[str, str] = {
            source.strip(): translated.strip()
            for source, translated in (terms or {}).items()
            if source and source.strip() and translated and translated.strip()
        }
        # 長詞先替換，否則 "Iron Sword" 會先被 "Iron" 咬掉一半。
        self._by_length: list[str] = sorted(self._terms, key=len, reverse=True)
        self._exact: dict[str, str] = {
            _normalize(source): translated for source, translated in self._terms.items()
        }
        self._patterns: dict[str, re.Pattern[str]] = {}
        self._loose_patterns: dict[str, re.Pattern[str]] = {}
        # 以「詞條首字（小寫）」建索引。若不建，每翻一條字串都要對 1,900 多個詞條
        # 各跑一次 regex；乘上整包七萬多條就是純粹的浪費。查詢時只比對首字出現在
        # 文字裡的那幾個詞條。
        self._by_head: dict[str, list[str]] = {}
        for term in self._by_length:
            head = _head_word(term)
            if head:
                self._by_head.setdefault(head, []).append(term)

    def __len__(self) -> int:
        return len(self._terms)

    def __bool__(self) -> bool:
        return bool(self._terms)

    @property
    def terms(self) -> dict[str, str]:
        return dict(self._terms)

    # ------------------------------------------------------------ 整串短路

    def lookup(self, source: str) -> str | None:
        """原文整串等於某詞條時回傳官方譯名，否則 None。"""
        return self._exact.get(_normalize(source))

    # ------------------------------------------------------------ prompt 注入

    def terms_in(self, source: str, limit: int = 24) -> list[tuple[str, str]]:
        """原文中出現的詞條，長詞優先，最多 limit 條。"""
        found: list[tuple[str, str]] = []
        for term in self._candidates(source):
            if len(found) >= limit:
                break
            if self._loose_pattern(term).search(source):
                found.append((term, self._terms[term]))
        return found

    def _candidates(self, text: str) -> list[str]:
        """只回傳「首字有出現在 text 裡」的詞條，長詞優先。"""
        heads = {word.lower() for word in re.findall(r"[A-Za-z][A-Za-z0-9'-]*", text)}
        if not heads:
            return []
        candidates = [
            term
            for head in heads & self._by_head.keys()
            for term in self._by_head[head]
        ]
        candidates.sort(key=len, reverse=True)
        return candidates

    def prompt_block(self, source: str, limit: int = 24) -> str:
        """組出注入 system prompt 的用語區塊；沒有命中時回空字串。"""
        found = self.terms_in(source, limit)
        if not found:
            return ""
        lines = "\n".join(f"{term} = {translated}" for term, translated in found)
        return (
            "\n\n[Glossary] Use these established Traditional Chinese terms exactly:\n"
            f"{lines}"
        )

    # ------------------------------------------------------------ 事後替換

    def enforce(self, source: str, target: str) -> str:
        """把譯文裡殘留的英文詞條換成對照表譯名。

        守則：
        * 區分大小寫——避免動到程式識別字。
        * 單字詞條只在「譯文整串就是那個詞」時替換；多字詞條才做句中替換。
          單字英文詞在句中往往是專有名詞的一部分，硬換會壞掉。
        * 譯名已出現在譯文中就跳過，保護「中文名（English）」這種夾註寫法。
        * 替換後若結構 token 沒保住，整個放棄、回傳原譯文。
        """
        if not self._terms or not target:
            return target
        if not re.search(r"[A-Za-z]", target):
            return target        # 譯文已無英文可換，這是最常見的情形

        result = target
        for term in self._candidates(target):
            translated = self._terms[term]
            if translated in result:
                continue
            pattern = self._pattern(term)
            if not pattern.search(result):
                continue
            if " " not in term and _normalize(result) != term:
                continue
            result = pattern.sub(lambda _m, t=translated: t, result)

        if result == target:
            return target
        result = _tighten_cjk_spacing(result)
        if not _preserves_required_tokens(source, result):
            return target
        return result

    # ------------------------------------------------------------ internals

    def _pattern(self, term: str) -> re.Pattern[str]:
        cached = self._patterns.get(term)
        if cached is None:
            cached = re.compile(_WORD_EDGE.format(re.escape(term)))
            self._patterns[term] = cached
        return cached

    def _loose_pattern(self, term: str) -> re.Pattern[str]:
        """比對用（prompt 注入）的不分大小寫版本。

        原文寫 "saturation value"、詞條寫 "Saturation Value" 時，大小寫敏感的比對
        等於整條線索沒送出去，模型只能自己猜——實測就是這樣猜出「飢餓值」的。
        替換（`enforce`）維持大小寫敏感：那裡動的是譯文，換錯會壞掉程式識別字。
        """
        cached = self._loose_patterns.get(term)
        if cached is None:
            cached = re.compile(_WORD_EDGE.format(re.escape(term)), re.IGNORECASE)
            self._loose_patterns[term] = cached
        return cached


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _head_word(term: str) -> str:
    match = re.search(r"[A-Za-z][A-Za-z0-9'-]*", term)
    return match.group(0).lower() if match else ""


def _tighten_cjk_spacing(text: str) -> str:
    """吃掉替換後夾在中文之間的單一半形空格（「使用 鐵劍 攻擊」→「使用鐵劍攻擊」）。

    只處理單一空格：連續空格多半是刻意的表格對齊，不能動。
    """
    return re.sub(r"(?<=[㐀-鿿])[ ](?=[㐀-鿿])", "", text)


# ---------------------------------------------------------------- 載入

def load_terms(path: Path | None) -> dict[str, str]:
    """讀單一對照表檔；缺檔或壞檔一律視為空表，不讓使用者卡在錯誤訊息。"""
    if not path or not Path(path).is_file():
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def merge_terms(layers: Iterable[Mapping[str, str]]) -> dict[str, str]:
    """由低到高合併多層對照表；高層譯名為空字串代表刪除該詞條。

    覆蓋比對不分大小寫，但保留高層寫的正式大小寫（使用者寫 "twilight forest"
    仍會取代官方表的 "Twilight Forest" 條目，並以官方大小寫為準寫回）。
    """
    merged: dict[str, str] = {}
    index: dict[str, str] = {}          # 小寫詞 -> 目前採用的原始寫法
    for layer in layers:
        for source, translated in layer.items():
            if not isinstance(source, str) or not isinstance(translated, str):
                continue
            key = source.strip()
            if not key:
                continue
            lowered = key.lower()
            previous = index.get(lowered)
            if previous is not None:
                merged.pop(previous, None)
            if not translated.strip():   # 空譯名 = 刪除
                index.pop(lowered, None)
                continue
            canonical = previous if previous is not None else key
            merged[canonical] = translated.strip()
            index[lowered] = canonical
    return merged


def load_glossary(
    official: Path | None = None,
    modnames: Path | None = None,
    custom: Path | None = None,
) -> Glossary:
    """三層合併載入：官方 < 模組名 < 自訂。"""
    return Glossary(merge_terms([
        load_terms(official),
        load_terms(modnames),
        load_terms(custom),
    ]))


# src/modpack_translator/pipeline/glossary.py → 上 3 層是專案根目錄
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
GLOSSARY_DIR = _PROJECT_ROOT / "assets" / "glossary"
CUSTOM_GLOSSARY_NAME = "custom_glossary.json"


def custom_glossary_path(output_root: Path | None) -> Path | None:
    """使用者自訂用語表的位置。

    放在輸出目錄底下而非 assets/，這樣自動更新覆蓋程式碼時不會被清掉。
    """
    return Path(output_root) / CUSTOM_GLOSSARY_NAME if output_root else None


def default_glossary(output_root: Path | None = None) -> Glossary:
    """專案預設用語庫：官方原版用語 < 模組名譯名 < 使用者自訂。"""
    return load_glossary(
        official=GLOSSARY_DIR / "minecraft_zh_tw.json",
        modnames=GLOSSARY_DIR / "modnames_zh_tw.json",
        custom=custom_glossary_path(output_root),
    )
