from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, NamedTuple

from modpack_translator.pipeline.patcher import (
    read_jar_json_file,
    read_jar_json_lang,
    read_jar_legacy_lang,
    read_jar_text_or_none,
    read_existing_bq_lang,
    read_existing_snbt,
    write_inplace_bq_lang,
    write_inplace_json,
    write_inplace_snbt,
    write_inline_snbt,
    write_jar_json_file,
    write_jar_json_lang,
    write_jar_legacy_lang,
    write_jar_text,
)
from modpack_translator.pipeline.apoli_power import read_power_text, write_power_text
from modpack_translator.pipeline.citadel_book import extract_citadel_text, rebuild_citadel_text
from modpack_translator.pipeline.guide_md import extract_guide_text, rebuild_guide_text
from modpack_translator.pipeline.postprocessor import normalize_line_shape, process
from modpack_translator.pipeline.preprocessor import (
    classify_translation_entry,
    decode,
    diff_keys,
    encode,
    read_inline_snbt_text,
    is_usable_translation,
    loads_relaxed,
    read_legacy_lang,
    read_bq_lang,
    read_json_lang,
    read_patchouli_page,
    read_patchouli_text,
    read_snbt_lang,
    rejection_reason,
    write_patchouli_text,
)
from modpack_translator.pipeline.scanner import TranslationTarget
from modpack_translator import run_log


class TargetStats(NamedTuple):
    """單一翻譯目標的處理結果。

    以前只回傳前三個數字，於是「來源有 295 條、摘要只交代 172 條」，剩下的憑空消失，
    看日誌的人只會覺得程式漏做事。加上 skipped 與 already_ok 之後，
    `source_total == translated + cached + fallback + skipped + already_ok` 恆成立，
    每一條字串的去向都交代得出來。
    """
    translated: int          # 這次真的送進模型並成功
    cached: int              # 命中快取或手動補譯表
    fallback: int            # 試過但沒通過驗證，保留英文
    failed: dict[str, str]   # fallback 的明細，供失敗清單與手動補譯視窗
    skipped: int             # 判定不需翻譯（資源路徑、純標記、技術縮寫…）
    already_ok: int          # 既有譯文已可用，這次不必動

    @property
    def source_total(self) -> int:
        return (self.translated + self.cached + self.fallback
                + self.skipped + self.already_ok)


def cache_key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:24]


MANUAL_TRANSLATIONS_NAME = "manual_translations.json"


def manual_translations_path(output_root: Path | None) -> Path | None:
    """使用者手動補譯的存放位置（放輸出目錄，自動更新不會清掉）。"""
    return Path(output_root) / MANUAL_TRANSLATIONS_NAME if output_root else None


def load_manual_translations(path: Path | None) -> dict[str, str]:
    """讀手動譯文表：來源字串雜湊 -> 使用者指定的譯文。"""
    if not path or not Path(path).is_file():
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}


def save_manual_translations(path: Path | None, entries: dict[str, str]) -> None:
    """合併寫回手動譯文表。使用者說了算，不做驗證。"""
    if not path or not entries:
        return
    merged = load_manual_translations(path)
    merged.update(entries)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(merged, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )


# 用來偵測「有無可翻譯的真實字母內容」
_HAS_LETTER_RE = re.compile(r"[A-Za-z]")
_PATCHOULI_SEGMENT_SPLIT_RE = re.compile(r"(\$\((?:p|br2?|li\d*)\)|\\?@[A-Z][A-Z0-9_]*@)")
_GENERIC_SEGMENT_SPLIT_RE = re.compile(r"(\r?\n+|\\?@(?:L|PAGE)@)")
_SENTENCE_SEGMENT_SPLIT_RE = re.compile(r"(?<=[.!?])(\s+)")
_STATIC_TRANSLATIONS = {
    "Bosses": "首領",
    "Cat": "貓",
    "Chicken": "雞",
    "Cow": "牛",
    "Pig": "豬",
    "Sheep": "綿羊",
    "Villager": "村民",
}
_STATIC_PATTERNS: tuple[tuple[re.Pattern[str], dict[str, str], str], ...] = (
    (
        re.compile(r"^(%s) Pacifies (Endermen|Phantoms|Piglins) when worn$"),
        {"Endermen": "終界使者", "Phantoms": "夜魅", "Piglins": "豬布林"},
        "{0} 穿戴時會安撫{1}",
    ),
)


def _translate_single(
    translator: Any,
    encoded: str,
    tokens: list[str],
    retry_count: int,
    cancel_check=None,
) -> tuple[str, bool]:
    """嘗試翻譯，失敗時最多重試 retry_count 次。cancel_check() 為 True 時立即中止。

    快速路徑：若移除 {N} 佔位符後沒有任何英文字母，表示字串本身無可翻譯內容
    （如 "[{2}]"），直接還原 tokens 並回傳原文，避免模型推理浪費資源且必然失敗。
    """
    # 移除 {N} 佔位符後若無任何英文字母，表示無可翻譯內容（如 "[{2}]"）
    # 直接 decode 還原 token 並回傳，不需推理且不會失敗
    if not _HAS_LETTER_RE.search(re.sub(r"\{[0-9]+\}", "", encoded)):
        return decode(encoded, tokens), True

    for _ in range(1 + retry_count):
        if cancel_check is not None and cancel_check():
            return encoded, False
        raw = translator.translate(encoded, cancel_check=cancel_check)
        final, ok = process(raw, encoded, tokens)
        if ok:
            return final, True
    return encoded, False


def _translate_validated(
    translator: Any,
    source: str,
    retry_count: int,
    cancel_check=None,
) -> tuple[str, bool]:
    static = _static_translation(source)
    if static is not None and is_usable_translation(source, static):
        return static, True

    glossary = getattr(translator, "glossary", None)
    if glossary is not None:
        # 整串就是用語庫詞條時直接取官方譯名——零推理成本，且原版名稱必定正確。
        exact = glossary.lookup(source)
        if exact is not None:
            candidate = source.replace(source.strip(), exact)
            if is_usable_translation(source, candidate):
                return candidate, True

    encoded, tokens = encode(source)
    final, ok = _translate_single(translator, encoded, tokens, retry_count, cancel_check)
    if not ok:
        # 重試 retry_count 次後，後處理器仍判定模型輸出結構壞掉。
        run_log.reject(source, final if final != encoded else None,
                       f"模型輸出未通過後處理（已重試 {retry_count} 次）")
        return source, False
    reason = rejection_reason(source, final)
    if reason is not None:
        run_log.reject(source, final, reason)
        return source, False
    if glossary is not None:
        final = glossary.enforce(source, final)
    return final, True


def _normalize_cached(cache: dict[str, str], ck: str, source: str, glossary: Any) -> None:
    """就地修好舊快取條目：零推理成本，而且每個雜湊只算一次。

    快取跨版本存活，裡面躺著的是「當時的規則」翻出來的東西——沒有用語庫的譯名、
    模型自己多加的換行。命中就沿用等於把舊缺陷一路帶到新的模組包裡。
    """
    fixed = normalize_line_shape(cache[ck], source)
    if glossary is not None:
        fixed = glossary.enforce(source, fixed)
    if fixed != cache[ck]:
        run_log.detail(f"  ↳ 就地校正快取條目：{ck}")
        cache[ck] = fixed


def _static_translation(source: str) -> str | None:
    text = source.strip()
    if text in _STATIC_TRANSLATIONS:
        return source.replace(text, _STATIC_TRANSLATIONS[text])
    for pattern, mapping, template in _STATIC_PATTERNS:
        match = pattern.fullmatch(text)
        if not match:
            continue
        translated = template.format(match.group(1), mapping[match.group(2)])
        return source.replace(text, translated)
    return None


def _translate_segmented_text(
    translator: Any,
    source: str,
    retry_count: int,
    cancel_check=None,
) -> tuple[str, bool]:
    final, ok = _translate_validated(translator, source, retry_count, cancel_check)
    if ok:
        return final, True

    parts = _GENERIC_SEGMENT_SPLIT_RE.split(source)
    if len(parts) <= 1:
        return _translate_sentence_segmented_text(translator, source, retry_count, cancel_check)

    translated_parts: list[str] = []
    changed = False
    for part in parts:
        if not part:
            continue
        if _GENERIC_SEGMENT_SPLIT_RE.fullmatch(part):
            translated_parts.append(part)
            continue
        if not part.strip():
            translated_parts.append(part)
            continue
        part_final, part_ok = _translate_validated(translator, part, retry_count, cancel_check)
        if not part_ok:
            return source, False
        translated_parts.append(part_final)
        changed = changed or part_final != part

    combined = "".join(translated_parts)
    if changed and is_usable_translation(source, combined):
        return combined, True
    final, ok = _translate_sentence_segmented_text(translator, source, retry_count, cancel_check)
    if ok:
        return final, True
    return source, False


def _translate_sentence_segmented_text(
    translator: Any,
    source: str,
    retry_count: int,
    cancel_check=None,
) -> tuple[str, bool]:
    if len(source) < 120:
        return source, False
    parts = _SENTENCE_SEGMENT_SPLIT_RE.split(source)
    if len(parts) <= 1:
        return source, False

    translated_parts: list[str] = []
    changed = False
    for part in parts:
        if not part:
            continue
        if _SENTENCE_SEGMENT_SPLIT_RE.fullmatch(part) or not part.strip():
            translated_parts.append(part)
            continue
        part_final, part_ok = _translate_validated(translator, part, retry_count, cancel_check)
        if not part_ok:
            return source, False
        translated_parts.append(part_final)
        changed = changed or part_final != part

    combined = "".join(translated_parts)
    if changed and is_usable_translation(source, combined):
        return combined, True
    return source, False


def _translate_patchouli_text(
    translator: Any,
    source: str,
    retry_count: int,
    cancel_check=None,
) -> tuple[str, bool]:
    final, ok = _translate_segmented_text(translator, source, retry_count, cancel_check)
    if ok:
        return final, True

    parts = _PATCHOULI_SEGMENT_SPLIT_RE.split(source)
    if len(parts) <= 1:
        return source, False

    translated_parts: list[str] = []
    changed = False
    for part in parts:
        if not part:
            continue
        if _PATCHOULI_SEGMENT_SPLIT_RE.fullmatch(part):
            translated_parts.append(part)
            continue
        part_final, part_ok = _translate_segmented_text(translator, part, retry_count, cancel_check)
        if not part_ok:
            return source, False
        translated_parts.append(part_final)
        changed = changed or part_final != part

    combined = "".join(translated_parts)
    if changed and is_usable_translation(source, combined):
        return combined, True
    return source, False


def translate_dict(
    en_dict: dict[str, str],
    zh_existing: dict[str, str],
    translator: Any,
    cache: dict[str, str],
    retry_count: int = 0,
    cancel_check=None,
    on_pair_done=None,
    manual: dict[str, str] | None = None,
) -> tuple[dict[str, str], int, int, int, dict[str, str]]:
    """翻譯缺少/未翻譯的鍵值。回傳 (result, translated, cached, fallback, failed)。

    `manual` 是使用者手動補的譯文（來源雜湊 -> 譯文），優先於快取與模型且不經驗證
    ——使用者刻意保留英文專有名詞之類的決定，不該被自動流程推翻。
    """
    to_translate = diff_keys(en_dict, zh_existing)
    # diff_keys 內部就用 classify 濾過一輪了，所以迴圈裡再判一次永遠不會命中。
    # 這裡另外走一趟，一是讓「來源有幾條、各自去了哪裡」在日誌裡加得起來，二是把
    # 每條被跳過的都記下來——「這句為什麼還是英文」是最常見的疑問，沒有紀錄就沒得查。
    n_skipped = 0
    for key, value in en_dict.items():
        verdict = classify_translation_entry(key, value)
        if verdict != "translate":
            n_skipped += 1
            run_log.outcome("skip", key, value, note=f"分類：{verdict}")
    n_already_ok = len(en_dict) - n_skipped - len(to_translate)
    result: dict[str, str] = {}
    failed: dict[str, str] = {}
    n_translated = n_cached = n_fallback = 0
    glossary = getattr(translator, "glossary", None)

    for key in to_translate:
        if cancel_check is not None and cancel_check():
            break
        src = en_dict[key]
        ck = cache_key(src)
        if manual and ck in manual:
            result[key] = manual[ck]
            n_cached += 1
            run_log.outcome("manual", key, src, manual[ck])
            if on_pair_done is not None:
                on_pair_done(1)
            continue
        if ck in cache and is_usable_translation(src, cache[ck]):
            _normalize_cached(cache, ck, src, glossary)
            result[key] = cache[ck]
            n_cached += 1
            run_log.outcome("cache", key, src, cache[ck])
            if on_pair_done is not None:
                on_pair_done(1)
            continue
        if ck in cache:
            run_log.detail(
                f"  ↳ 快取條目不可用已汰除：{rejection_reason(src, cache[ck])}")
            cache.pop(ck, None)
        final, ok = _translate_segmented_text(translator, src, retry_count, cancel_check)
        if ok:
            result[key] = final
            cache[ck] = final
            n_translated += 1
            run_log.outcome("model", key, src, final)
        else:
            result[key] = src
            failed[key] = src
            n_fallback += 1
            run_log.outcome("fallback", key, src, note="所有嘗試皆未通過驗證，保留英文")
        if on_pair_done is not None:
            on_pair_done(1)

    return result, n_translated, n_cached, n_fallback, failed, n_skipped, n_already_ok


def read_target_strings(target: TranslationTarget) -> dict[str, str]:
    if target.format == "json_lang":
        return read_json_lang(target.source_file, target.path_in_jar)
    elif target.format == "legacy_lang":
        return read_legacy_lang(target.source_file, target.path_in_jar)
    elif target.format == "patchouli_json":
        page = read_patchouli_page(target.source_file, target.path_in_jar)
        return read_patchouli_text(page)
    elif target.format in ("ftbq_snbt", "heracles_snbt"):
        return read_snbt_lang(target.source_file)
    elif target.format in ("ftbq_inline_snbt", "heracles_inline_snbt"):
        return read_inline_snbt_text(target.source_file)
    elif target.format == "bq_lang":
        return read_bq_lang(target.source_file)
    elif target.format == "kubejs_json":
        return read_json_lang(target.source_file, None)
    elif target.format == "apoli_power":
        return read_power_text(read_power_document(target))
    elif target.format in ("guideme_md", "citadel_txt"):
        raw = read_jar_text_or_none(target.source_file, target.path_in_jar)
        if not raw:
            return {}
        extract = _TEXT_PAGE_CODECS[target.format][0]
        return extract(raw)
    return {}


# ----------------------------------------------------- Origins／Apoli 能力定義

def read_power_document(target: TranslationTarget) -> Any:
    """能力定義檔的完整 JSON。讀不到或壞掉時回 `{}`——掃描階段已經記過一行了。"""
    if target.path_in_jar:
        raw = read_jar_text_or_none(target.source_file, target.path_in_jar)
    else:
        try:
            raw = target.source_file.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            return {}
    if not raw:
        return {}
    try:
        return loads_relaxed(raw)
    except json.JSONDecodeError:
        return {}


def write_power_document(target: TranslationTarget, document: Any) -> None:
    """原地寫回：能力名稱就住在定義檔裡，`data/` 沒有語言檔那一層。"""
    if target.path_in_jar:
        write_jar_json_file(target.source_file, target.target_path_in_jar or target.path_in_jar, document)
        return
    path = target.target_file or target.source_file
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _process_power_json(
    target: TranslationTarget,
    translator: Any,
    cache: dict[str, str],
    retry_count: int = 0,
    cancel_check=None,
    on_pair_done=None,
    manual: dict[str, str] | None = None,
) -> TargetStats:
    document = read_power_document(target)
    en_dict = read_power_text(document)
    if not en_dict:
        return TargetStats(0, 0, 0, {}, 0, 0)

    # 既有譯文一律傳空表：譯文就寫在同一個欄位裡，已經是中文的那些會在分類階段
    # 被判為不需翻譯，所以重跑仍然是冪等的。
    result, n_translated, n_cached, n_fallback, failed, n_skipped, n_already_ok = translate_dict(
        en_dict, {}, translator, cache, retry_count, cancel_check, on_pair_done, manual
    )

    if _apply_power_text(document, en_dict, result):
        write_power_document(target, document)
    return TargetStats(n_translated, n_cached, n_fallback, failed, n_skipped, n_already_ok)


def _apply_power_text(document: Any, en_dict: dict[str, str], result: dict[str, str]) -> bool:
    changed = False
    for path_key, value in result.items():
        if value == en_dict.get(path_key):
            continue                       # 回退原文，不必動檔案
        try:
            write_power_text(document, path_key, value)
        except (KeyError, IndexError, TypeError):
            continue                       # 結構對不上就跳這一條，不連累整份檔案
        changed = True
    return changed


_LOCALE_IN_PATH_RE = re.compile(r"(?<![A-Za-z0-9])([a-z]{2})_([a-z]{2,3})(?![A-Za-z0-9])")


def _locale_path_variants(path_in_jar: str | None) -> list[str]:
    """語言碼路徑的大小寫變體。

    寫入一律用小寫（遊戲只認小寫），但少數模組出貨大寫的 zh_TW 譯文——那些內容
    仍然可用，讀取時一併撈進來重用，免得整包重翻一次。
    """
    if not path_in_jar:
        return []
    match = _LOCALE_IN_PATH_RE.search(path_in_jar)
    if not match:
        return [path_in_jar]
    upper = path_in_jar[:match.start(2)] + match.group(2).upper() + path_in_jar[match.end(2):]
    return list(dict.fromkeys([path_in_jar, upper]))


def read_existing_target(target: TranslationTarget, lang_code: str) -> dict[str, str]:
    if target.format == "apoli_power":
        # 譯文與原文共用同一個欄位，沒有「既有譯文檔」這種東西。已經翻好的條目由
        # 分類階段判為不需翻譯，不是靠這裡比對出來的。
        return {}
    if target.output_mode == "jar_inject":
        # 文字型頁面（GuideME／Citadel）走自己的 codec。少了這一段，掃描階段會以為
        # 整本指南都沒翻過，估出來的待翻譯量遠大於實際要做的事——進度條因此永遠到
        # 不了底，摘要的數字也對不上。
        if target.format in _TEXT_PAGE_CODECS:
            extract, _rebuild, reuse_existing = _TEXT_PAGE_CODECS[target.format]
            if not reuse_existing:
                return {}
            raw = read_jar_text_or_none(target.source_file, target.target_path_in_jar)
            return extract(raw) if raw else {}

        merged: dict[str, str] = {}
        for path in _locale_path_variants(target.target_path_in_jar):
            if target.format == "json_lang":
                merged.update(read_jar_json_lang(target.source_file, path))
            elif target.format == "legacy_lang":
                merged.update(read_jar_legacy_lang(target.source_file, path))
            elif target.format == "patchouli_json":
                merged.update(read_patchouli_text(read_jar_json_file(target.source_file, path)))
        return merged

    if target.format in ("ftbq_snbt", "heracles_snbt"):
        path = target.target_file or target.source_file.parent / f"{lang_code}.snbt"
        return read_existing_snbt(path)
    elif target.format in ("ftbq_inline_snbt", "heracles_inline_snbt"):
        return {}
    elif target.format == "bq_lang":
        path = target.target_file or target.source_file.parent / f"{lang_code}.lang"
        return read_existing_bq_lang(path)
    else:
        path = target.target_file or target.source_file.parent / f"{lang_code}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def process_target(
    target: TranslationTarget,
    translator: Any,
    cache: dict[str, str],
    lang_code: str,
    retry_count: int = 0,
    cancel_check=None,
    on_pair_done=None,
    manual: dict[str, str] | None = None,
) -> TargetStats:
    """處理單一翻譯目標。回傳 (translated, cached, fallback, failed)。"""
    if target.format == "patchouli_json":
        return _process_patchouli(target, translator, cache, retry_count, cancel_check, on_pair_done, manual)

    if target.format in ("guideme_md", "citadel_txt"):
        return _process_jar_text_page(target, translator, cache, retry_count, cancel_check, on_pair_done, manual)

    if target.format == "apoli_power":
        return _process_power_json(target, translator, cache, retry_count, cancel_check, on_pair_done, manual)

    if target.format == "json_lang":
        en_dict = read_json_lang(target.source_file, target.path_in_jar)
    elif target.format == "legacy_lang":
        en_dict = read_legacy_lang(target.source_file, target.path_in_jar)
    elif target.format in ("ftbq_snbt", "heracles_snbt"):
        en_dict = read_snbt_lang(target.source_file)
    elif target.format in ("ftbq_inline_snbt", "heracles_inline_snbt"):
        en_dict = read_inline_snbt_text(target.source_file)
    elif target.format == "bq_lang":
        en_dict = read_bq_lang(target.source_file)
    elif target.format == "kubejs_json":
        en_dict = read_json_lang(target.source_file, None)
    else:
        return TargetStats(0, 0, 0, {}, 0, 0)

    if not en_dict:
        return TargetStats(0, 0, 0, {}, 0, 0)

    zh_existing = read_existing_target(target, lang_code)
    result, n_translated, n_cached, n_fallback, failed, n_skipped, n_already_ok = translate_dict(
        en_dict, zh_existing, translator, cache, retry_count, cancel_check, on_pair_done, manual
    )

    if result:
        write_target_result(target, result, lang_code)

    return TargetStats(n_translated, n_cached, n_fallback, failed, n_skipped, n_already_ok)


def write_target_result(
    target: TranslationTarget,
    result: dict[str, str],
    lang_code: str,
) -> None:
    """把一份 鍵→譯文 寫進目標的輸出位置。

    傳入部分結果是安全的：底層寫入函式一律先讀既有內容再合併，只有提供的鍵會被
    覆蓋。手動補譯就是靠這個性質，不必重跑整個檔案。
    """
    if target.output_mode == "jar_inject":
        if not target.target_path_in_jar:
            raise ValueError(f"Missing jar target path for {target.source_file}")
        if target.format == "json_lang":
            write_jar_json_lang(target.source_file, target.target_path_in_jar, result)
        elif target.format == "legacy_lang":
            write_jar_legacy_lang(target.source_file, target.target_path_in_jar, result)
        else:
            raise ValueError(f"Unsupported jar injection format: {target.format}")
    elif target.format in ("ftbq_snbt", "heracles_snbt"):
        write_inplace_snbt(target.source_file, lang_code, result, target.target_file)
    elif target.format in ("ftbq_inline_snbt", "heracles_inline_snbt"):
        write_inline_snbt(target.source_file, result)
    elif target.format == "bq_lang":
        write_inplace_bq_lang(target.source_file, lang_code, result, target.target_file)
    else:
        write_inplace_json(target.source_file, lang_code, result, target.target_file)


def apply_manual_translations(
    target: TranslationTarget,
    translations: dict[str, str],
    lang_code: str,
) -> int:
    """把使用者手動補的譯文寫進模組包，回傳實際寫入的條數。

    只動提供的鍵，其餘內容原封不動；使用者不必自己去 jar 裡翻找檔案。
    """
    values = {key: text for key, text in translations.items() if text.strip()}
    if not values:
        return 0

    if target.format == "patchouli_json":
        _apply_manual_patchouli(target, values)
    elif target.format == "apoli_power":
        document = read_power_document(target)
        if _apply_power_text(document, {}, values):
            write_power_document(target, document)
    elif target.format in _TEXT_PAGE_CODECS:
        _apply_manual_text_page(target, values)
    else:
        write_target_result(target, values, lang_code)
    return len(values)


def _apply_manual_patchouli(target: TranslationTarget, values: dict[str, str]) -> None:
    target_path = target.target_path_in_jar or target.path_in_jar
    if not target_path:
        raise ValueError(f"Missing Patchouli target path for {target.source_file}")
    page = read_jar_json_file(target.source_file, target_path)
    if not page:
        page = deepcopy(read_patchouli_page(target.source_file, target.path_in_jar))
    for path_key, text in values.items():
        try:
            write_patchouli_text(page, path_key, text)
        except (KeyError, IndexError, TypeError):
            continue          # 頁面結構已變，跳過這一條而不是整批失敗
    write_jar_json_file(target.source_file, target_path, page)


def _apply_manual_text_page(target: TranslationTarget, values: dict[str, str]) -> None:
    raw = read_jar_text_or_none(target.source_file, target.path_in_jar)
    if not raw or not target.target_path_in_jar:
        return
    extract, rebuild, reuse_existing = _TEXT_PAGE_CODECS[target.format]
    existing_raw = read_jar_text_or_none(target.source_file, target.target_path_in_jar)
    existing = extract(existing_raw) if (existing_raw and reuse_existing) else {}
    write_jar_text(
        target.source_file,
        target.target_path_in_jar,
        rebuild(raw, {**existing, **values}),
    )


# (抽取, 重建, 是否重用目標檔既有片段)
# Citadel 不重用：中文重新折行後行數與原文不同，行號當鍵會位移，改由掃描端
# 做檔級「已含中文就跳過」判定。
_TEXT_PAGE_CODECS = {
    "guideme_md": (extract_guide_text, rebuild_guide_text, True),
    "citadel_txt": (extract_citadel_text, rebuild_citadel_text, False),
}


def _process_jar_text_page(
    target: TranslationTarget,
    translator: Any,
    cache: dict[str, str],
    retry_count: int = 0,
    cancel_check=None,
    on_pair_done=None,
    manual: dict[str, str] | None = None,
) -> TargetStats:
    """jar 內的文字型頁面（GuideME 指南、Citadel 圖鑑書）。

    抽出可翻片段 → 走共用翻譯管線（快取／用語庫／驗證都沿用）→ 重建 → 寫回 jar
    的目標路徑。兩種格式只差在 codec，流程完全相同。
    """
    raw = read_jar_text_or_none(target.source_file, target.path_in_jar)
    if not raw or not target.target_path_in_jar:
        return TargetStats(0, 0, 0, {}, 0, 0)

    extract, rebuild, reuse_existing = _TEXT_PAGE_CODECS[target.format]
    en_dict = extract(raw)
    if not en_dict:
        return TargetStats(0, 0, 0, {}, 0, 0)

    existing_raw = read_jar_text_or_none(target.source_file, target.target_path_in_jar)
    zh_existing = extract(existing_raw) if (existing_raw and reuse_existing) else {}

    result, n_translated, n_cached, n_fallback, failed, n_skipped, n_already_ok = translate_dict(
        en_dict, zh_existing, translator, cache, retry_count, cancel_check, on_pair_done, manual
    )

    merged = {**zh_existing, **result}
    if merged:
        write_jar_text(
            target.source_file,
            target.target_path_in_jar,
            rebuild(raw, merged),
        )
    return TargetStats(n_translated, n_cached, n_fallback, failed, n_skipped, n_already_ok)


def _process_patchouli(
    target: TranslationTarget,
    translator: Any,
    cache: dict[str, str],
    retry_count: int = 0,
    cancel_check=None,
    on_pair_done=None,
    manual: dict[str, str] | None = None,
) -> TargetStats:
    if not target.path_in_jar:
        raise ValueError(f"Missing Patchouli source path for {target.source_file}")

    source_page = read_patchouli_page(target.source_file, target.path_in_jar)
    target_path = target.target_path_in_jar or target.path_in_jar
    if not target_path:
        raise ValueError(f"Missing Patchouli target path for {target.source_file}")
    existing_page = read_jar_json_file(target.source_file, target_path) if target.output_mode == "jar_inject" else {}
    page = deepcopy(source_page)
    source_strings = read_patchouli_text(source_page)
    existing_strings = read_patchouli_text(existing_page) if existing_page else {}
    for path_key, existing_value in existing_strings.items():
        source_value = source_strings.get(path_key)
        if source_value is not None and is_usable_translation(source_value, existing_value):
            write_patchouli_text(page, path_key, existing_value)

    existing_strings = read_patchouli_text(page)
    to_translate = diff_keys(source_strings, existing_strings)

    changed = page != existing_page
    failed: dict[str, str] = {}
    n_translated = n_cached = n_fallback = 0
    n_skipped = 0
    for path_key, value in source_strings.items():
        verdict = classify_translation_entry(path_key, value)
        if verdict != "translate":
            n_skipped += 1
            run_log.outcome("skip", path_key, value, note=f"分類：{verdict}")
    n_already_ok = len(source_strings) - n_skipped - len(to_translate)
    glossary = getattr(translator, "glossary", None)

    for path_key in to_translate:
        if cancel_check is not None and cancel_check():
            break
        src = source_strings[path_key]
        ck = cache_key(src)
        if manual and ck in manual:
            write_patchouli_text(page, path_key, manual[ck])
            changed = True
            n_cached += 1
            run_log.outcome("manual", path_key, src, manual[ck])
            if on_pair_done is not None:
                on_pair_done(1)
            continue
        if ck in cache and is_usable_translation(src, cache[ck]):
            _normalize_cached(cache, ck, src, glossary)
            write_patchouli_text(page, path_key, cache[ck])
            changed = True
            n_cached += 1
            run_log.outcome("cache", path_key, src, cache[ck])
            if on_pair_done is not None:
                on_pair_done(1)
            continue
        if ck in cache:
            run_log.detail(
                f"  ↳ 快取條目不可用已汰除：{rejection_reason(src, cache[ck])}")
            cache.pop(ck, None)
        final, ok = _translate_patchouli_text(translator, src, retry_count, cancel_check)
        if ok:
            write_patchouli_text(page, path_key, final)
            cache[ck] = final
            changed = True
            n_translated += 1
            run_log.outcome("model", path_key, src, final)
        else:
            failed[path_key] = src
            n_fallback += 1
            run_log.outcome("fallback", path_key, src, note="所有嘗試皆未通過驗證，保留英文")
        if on_pair_done is not None:
            on_pair_done(1)

    if changed:
        if target.output_mode != "jar_inject":
            raise ValueError("Patchouli resource pack output is no longer supported")
        write_jar_json_file(target.source_file, target_path, page)

    return TargetStats(n_translated, n_cached, n_fallback, failed, n_skipped, n_already_ok)


def failed_target_name(target: TranslationTarget) -> str:
    location = target.path_in_jar
    if not location and target.target_file:
        location = str(target.target_file)
    if not location:
        location = str(target.source_file)
    return f"{target.mod_id}__{target.format}__{location}"


_FAILED_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _clear_failed_items(output_dir: Path) -> None:
    if not output_dir.is_dir():
        return
    for file_path in output_dir.rglob("*.txt"):
        try:
            file_path.unlink()
        except OSError:
            pass


def _write_failed_items(
    failed_by_target: dict[str, dict[str, str]],
    output_dir: Path,
) -> int:
    """將失敗項目分檔寫入 output_dir。無失敗項目時不建立資料夾，回傳 0。"""
    _clear_failed_items(output_dir)
    total_failed = sum(len(v) for v in failed_by_target.values())
    if total_failed == 0:
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for target_name, items in sorted(failed_by_target.items()):
        if not items:
            continue
        category = _failed_item_category(target_name, items)
        safe_name = _FAILED_FILENAME_RE.sub("_", target_name).strip("._")
        if not safe_name:
            safe_name = "failed_items"
        if len(safe_name) > 180:
            digest = hashlib.sha1(target_name.encode("utf-8")).hexdigest()[:12]
            safe_name = f"{safe_name[:167]}_{digest}"
        category_dir = output_dir / category
        category_dir.mkdir(parents=True, exist_ok=True)
        file_path = category_dir / f"{safe_name}.txt"
        lines = [
            f"失敗項目清單：{target_name}",
            f"分類：{category}",
            f"失敗數量：{len(items)} 個",
            "",
        ]
        for key, src in sorted(items.items()):
            display_src = src[:200] + "…" if len(src) > 200 else src
            lines.append(f"  {key}")
            lines.append(f'    原文："{display_src}"')
            lines.append("")
        file_path.write_text("\n".join(lines), encoding="utf-8")
        written += 1
    return written


def _failed_item_category(target_name: str, items: dict[str, str]) -> str:
    if "__patchouli_json__" in target_name:
        return "markup_or_book_text"
    classifications = {classify_translation_entry(key, src) for key, src in items.items()}
    if classifications <= {"copy", "skip"}:
        return "copy_or_skip_noise"
    values = list(items.values())
    if all(_looks_failed_fragment(value) for value in values):
        return "short_fragments"
    if any(_looks_markup_heavy(value) for value in values):
        return "markup_or_book_text"
    return "natural_text"


def _looks_failed_fragment(value: str) -> bool:
    text = value.strip()
    if len(text) <= 24:
        return True
    return bool(re.search(r"%\d*\$?[sdifcbxo]|%[sdifcbxo]", text)) and len(text) <= 80


def _looks_markup_heavy(value: str) -> bool:
    return value.count("$(") + value.count("[#](") + value.count("://") >= 2
