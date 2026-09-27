"""翻譯包：一台電腦翻好，其他人直接匯入，不必每台都重跑模型。

匯出的是譯文對照表（翻譯快取＋手動補譯，都以來源字串雜湊為鍵），不是翻好的檔案。

檔案只是這張表寫進各種格式之後的樣子：jar 裡的 zh_tw.json、Patchouli 書頁、任務書
的 .snbt……直接搬檔案，得先知道十幾種格式各寫到哪裡、兩邊的 jar 檔名對不對得上、
任務檔是不是同一版——漏一種就缺一塊，版本差一點就把對方的任務結構整個蓋掉。

搬對照表就沒有這些問題。匯入端照自己的模組包重新掃描，每條字串寫到哪裡由原本那套
掃描器與寫入器決定，跟正常翻譯走同一條路，只是不叫模型。對方已經是中文的內容不會
被動到；模組包版本略有差異，也只是少命中幾條。
"""

from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple

from modpack_translator.pipeline.runner import (
    load_cache,
    load_manual_translations,
    save_cache,
    save_manual_translations,
)
from modpack_translator.version import APP_NAME, __version__

PACK_FORMAT = "modpack-translator/translation-pack"
PACK_VERSION = 1
PACK_MEMBER = "translation_pack.json"
README_MEMBER = "README.txt"


class PackError(ValueError):
    """翻譯包不能用。訊息會直接顯示給使用者，所以寫成白話。"""


class TranslationPack(NamedTuple):
    cache: dict[str, str]       # 來源雜湊 -> 譯文（模型／用語庫翻的）
    manual: dict[str, str]      # 來源雜湊 -> 譯文（使用者手動補的，套用時不再驗證）
    language: str
    modpack: str
    exported_at: str
    app_version: str

    @property
    def entries(self) -> int:
        return len(self.cache.keys() | self.manual.keys())


def export_pack(
    dest: Path,
    cache_path: Path,
    manual_path: Path | None,
    language: str,
    modpack: str = "",
) -> int:
    """把本機的譯文寫成翻譯包，回傳收錄的條數。"""
    cache = load_cache(cache_path)
    manual = load_manual_translations(manual_path)
    if not cache and not manual:
        raise PackError("這台電腦還沒有任何譯文可以匯出，請先翻譯一次模組包。")

    exported_at = datetime.now().isoformat(timespec="seconds")
    payload = {
        "format": PACK_FORMAT,
        "version": PACK_VERSION,
        "app_version": __version__,
        "language": language,
        "modpack": modpack,
        "exported_at": exported_at,
        "cache": cache,
        "manual": manual,
    }

    # 先寫暫存檔再換名：寫到一半失敗（磁碟滿之類）時，使用者手上不會多出一個
    # 看起來正常、其實是壞掉的檔案被轉傳出去。
    dest = Path(dest)
    tmp = dest.with_name(f"{dest.name}.tmp")
    try:
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(PACK_MEMBER, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
            zf.writestr(README_MEMBER, _readme(modpack, exported_at))
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()
    return len(cache.keys() | manual.keys())


def read_pack(path: Path, language: str) -> TranslationPack:
    """讀取並驗證翻譯包。任何不對勁都丟 PackError。"""
    try:
        with zipfile.ZipFile(path) as zf:
            raw = zf.read(PACK_MEMBER)
    except zipfile.BadZipFile:
        raise PackError("這不是翻譯包：檔案不是 zip 格式。") from None
    except KeyError:
        raise PackError("這個 zip 裡沒有翻譯包資料，可能選錯檔案了。") from None

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PackError("翻譯包內容損壞，無法讀取。") from None
    if not isinstance(data, dict) or data.get("format") != PACK_FORMAT:
        raise PackError("這不是本程式匯出的翻譯包。")
    version = data.get("version")
    if not isinstance(version, int) or version > PACK_VERSION:
        raise PackError("這個翻譯包來自較新版本的翻譯器，請先更新程式再匯入。")
    pack_language = str(data.get("language") or "")
    if pack_language.lower() != language.lower():
        raise PackError(
            f"翻譯包的語言是「{pack_language or '未知'}」，與目前設定的「{language}」不同，不能套用。"
        )

    pack = TranslationPack(
        cache=_str_map(data.get("cache")),
        manual=_str_map(data.get("manual")),
        language=pack_language,
        modpack=str(data.get("modpack") or ""),
        exported_at=str(data.get("exported_at") or ""),
        app_version=str(data.get("app_version") or ""),
    )
    if not pack.entries:
        raise PackError("翻譯包裡沒有任何譯文。")
    return pack


def merge_pack(
    pack: TranslationPack,
    cache_path: Path,
    manual_path: Path | None,
) -> tuple[int, int]:
    """把翻譯包併進本機的快取與手動補譯表，回傳 (新增快取條數, 新增手動條數)。

    只補空缺，本機已有的一條都不蓋：匯入是補上本機缺的譯文，不是改寫本機已經
    定案的譯文——尤其手動補譯是使用者刻意的決定。
    """
    cache = load_cache(cache_path)
    new_cache = {k: v for k, v in pack.cache.items() if k not in cache}
    if new_cache:
        cache.update(new_cache)
        save_cache(cache_path, cache)

    manual = load_manual_translations(manual_path)
    new_manual = {k: v for k, v in pack.manual.items() if k not in manual}
    save_manual_translations(manual_path, new_manual)
    return len(new_cache), len(new_manual)


def _str_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if isinstance(k, str) and isinstance(v, str)}


def _readme(modpack: str, exported_at: str) -> str:
    """zip 裡附的說明。收到檔案的人多半會先點開看，別讓他們以為要自己解壓縮去貼。"""
    lines = [
        f"這是「{APP_NAME}」匯出的翻譯包。",
        "",
        "使用方式：",
        "  1. 開啟翻譯器，選好自己的模組包資料夾。",
        "  2. 按「匯入翻譯…」，選這個 zip 檔（不需要解壓縮）。",
        "  3. 程式會自動掃描並把譯文寫進模組包；不會啟動翻譯模型，也不需要顯示卡。",
        "",
        "・模組包版本與匯出者相同效果最好；版本不同只會少翻幾條，不會弄壞檔案。",
        "・模組包裡已經是中文的內容不會被覆蓋，原始檔案也會先備份。",
        "",
        f"來源模組包：{modpack or '（未註明）'}",
        f"匯出時間：{exported_at}",
        f"翻譯器版本：v{__version__}",
    ]
    # BOM＋CRLF：Windows 記事本不管新舊版本都能正確顯示中文。
    return "﻿" + "\r\n".join(lines) + "\r\n"
