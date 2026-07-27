"""單一檔案的執行紀錄：開啟程式時清空，只保留這一次執行的完整經過。

**為什麼需要它**：最有診斷價值的那幾行——哪個模組被略過、為什麼略過、備份了幾個檔、
用語庫載入幾條——都是背景執行緒 emit 出來的，畫面上看不到，程式一關就沒了。使用者
回報問題時只能說「它壞了」。有這份檔案，回報就變成可以直接讀的證據。

**為什麼只留一份、每次清空**：這是給使用者附在 issue 裡的東西，不是稽核軌跡。留一堆
帶時間戳的舊檔，只會讓人挑錯一份寄過來；而且輸出資料夾是使用者天天看的地方，不該被
日誌堆滿。要留舊的，自己另存一份就好。

所有函式都不會拋例外——寫日誌失敗絕不能連累翻譯本身。
"""

from __future__ import annotations

import platform
import sys
import threading
import traceback
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import IO

LOG_NAME = "run.log"

_lock = threading.Lock()
_handle: IO[str] | None = None
_path: Path | None = None


def path() -> Path | None:
    """目前的紀錄檔位置；尚未開始則為 None。"""
    return _path


def start(output_root: Path | str | None, extra: dict[str, object] | None = None) -> Path | None:
    """清空並重新開始紀錄。重複呼叫會關掉舊的、重開一份。"""
    global _handle, _path
    if not output_root:
        return None
    try:
        target = Path(output_root)
        target.mkdir(parents=True, exist_ok=True)
        target = target / LOG_NAME
        global _detail_count
        with _lock:
            _close_locked()
            _detail_count = 0
            # "w" 直接截斷舊內容——這就是「只留這一次」的實作。
            _handle = target.open("w", encoding="utf-8", newline="\n")
            _path = target
            _write_locked(_header(extra or {}))
        return target
    except Exception:
        with _lock:
            _handle = None
            _path = None
        return None


def write(message: str) -> None:
    """附加一行（自動加時間戳）。多行訊息每行都會對齊縮排。"""
    if _handle is None or not message:
        return
    stamp = datetime.now().strftime("%H:%M:%S")
    lines = str(message).rstrip().splitlines() or [""]
    body = f"[{stamp}] {lines[0]}\n"
    body += "".join(f"{'':11}{line}\n" for line in lines[1:])
    with _lock:
        _write_locked(body)


def section(title: str) -> None:
    """區段標題，讓人一眼掃到執行到哪個階段。"""
    if _handle is None:
        return
    with _lock:
        _write_locked(f"\n{'─' * 70}\n  {title}\n{'─' * 70}\n")


def detail(message: str) -> None:
    """逐條層級的明細。

    刻意不設上限。這個檔案的用途就是使用者寄回來給作者查問題——截斷過的日誌，
    偏偏很可能就把出問題的那一條截掉了，那整份檔案也就白留了。
    """
    write(message)


_detail_count = 0


def detail_count() -> int:
    return _detail_count


# 逐條紀錄的標記。用符號開頭是為了 grep：搜 "✗" 就只剩失敗的。
_MARKS = {
    "model": "✓ 模型翻譯",
    "cache": "✓ 快取命中",
    "manual": "✓ 手動補譯",
    "existing": "✓ 沿用既有譯文",
    "skip": "· 判定不需翻譯",
    "fallback": "✗ 回退原文",
}


def outcome(kind: str, key: str, source: str, result: str | None = None,
            note: str = "") -> None:
    """一條字串的最終結果。

    連成功的也記——使用者回報「這個名字翻得很怪」時，得查得出那條譯文是模型現翻的、
    快取撈的、還是他自己手動補的。這三種情形的修法完全不同，而翻完的 jar 裡看不出
    差別。
    """
    global _detail_count
    if _handle is None:
        return
    _detail_count += 1
    body = f"{_MARKS.get(kind, kind)}  {key}"
    if note:
        body += f"（{note}）"
    body += f"\n    原文：{_clip(source)}"
    if result is not None and result != source:
        body += f"\n    譯文：{_clip(result)}"
    write(body)


def reject(source: str, candidate: str | None, reason: str, where: str = "") -> None:
    """一次翻譯嘗試沒通過驗證。原文、模型實際輸出、被哪條規則擋下——缺一就查不下去。

    這是「嘗試」層級：整串失敗後還會退回分段重試，所以同一條字串可能出現數次。
    最終結果看該鍵的 ✓／✗ 那一行。
    """
    location = f"  @ {where}" if where else ""
    body = f"  ↳ 嘗試失敗：{reason}{location}\n      原文：{_clip(source)}"
    if candidate is not None and candidate != source:
        body += f"\n      模型：{_clip(candidate)}"
    detail(body)


def _clip(text: str, limit: int = 400) -> str:
    """單行化。真換行改成 ⏎ 才不會讓一條紀錄散成十幾行、蓋掉時間戳的對齊。

    上限訂得寬，是因為翻譯出問題的往往就是長字串——截掉反而看不出哪裡壞了。
    """
    flat = str(text).replace("\n", "⏎").replace("\r", "")
    return flat if len(flat) <= limit else flat[:limit] + f"…〔全長 {len(flat)} 字〕"


def table(rows: list[tuple[str, object]], indent: int = 4) -> None:
    """對齊的數值表；中文欄名用顯示寬度對齊。"""
    if _handle is None or not rows:
        return
    width = max(_display_width(str(k)) for k, _ in rows)
    pad = " " * indent
    body = "".join(
        f"{pad}{k}{' ' * (width - _display_width(str(k)))} : {v}\n" for k, v in rows
    )
    with _lock:
        _write_locked(body)


def exception(context: str, exc: BaseException) -> None:
    """例外連同 traceback 一起寫進去——沒有 traceback 的錯誤回報等於沒回報。"""
    if _handle is None:
        return
    trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    write(f"[例外] {context}：{exc!r}\n{trace.rstrip()}")


def close() -> None:
    global _handle
    with _lock:
        if _handle is not None:
            _write_locked(
                f"\n結束時間：{datetime.now():%Y-%m-%d %H:%M:%S}"
                f"　（逐條紀錄 {_detail_count:,} 筆）\n"
            )
        _close_locked()
    if _path is not None and _path.exists():
        size = _path.stat().st_size
        if size > 8 * 1024 * 1024:
            # 回報問題時要用寄的，超過幾 MB 先講一聲比較不會卡在信箱附件上限。
            with _lock:
                pass
            try:
                with _path.open("a", encoding="utf-8", newline="\n") as fh:
                    fh.write(f"（本檔 {size / 1024 / 1024:.1f} MB，回報問題時建議先壓縮成 zip）\n")
            except Exception:
                pass


def install_excepthook() -> None:
    """把未捕捉的例外也留在紀錄裡，然後照常交還原本的處理流程。"""
    previous = sys.excepthook

    def _hook(exc_type, exc_value, exc_tb):
        try:
            if _handle is not None and exc_value is not None:
                exception("未捕捉的例外", exc_value)
        except Exception:
            pass
        previous(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook


# ---------------------------------------------------------------- internals

def _close_locked() -> None:
    global _handle
    if _handle is not None:
        try:
            _handle.close()
        except Exception:
            pass
        _handle = None


def _write_locked(text: str) -> None:
    """一律立即 flush：程式被強制關掉時，已寫的內容不能不見。"""
    if _handle is None:
        return
    try:
        _handle.write(text)
        _handle.flush()
    except Exception:
        _close_locked()


def _header(extra: dict[str, object]) -> str:
    from modpack_translator.version import APP_NAME, APP_VERSION

    rows = {
        "程式": f"{APP_NAME}{APP_VERSION}",
        "開始時間": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
        "系統": f"{platform.platform()}",
        "Python": f"{platform.python_version()} ({sys.executable})",
        **{str(k): v for k, v in extra.items()},
    }
    width = max(_display_width(k) for k in rows)
    body = "".join(
        f"  {k}{' ' * (width - _display_width(k))} : {v}\n" for k, v in rows.items()
    )
    legend = (
        "  逐條紀錄圖例： ✓ 成功（模型／快取／手動）　· 判定不需翻譯"
        "　✗ 回退原文　↳ 單次嘗試的細節\n"
    )
    return f"{'═' * 70}\n{body}{'─' * 70}\n{legend}{'═' * 70}\n"


def _display_width(text: str) -> int:
    """中日韓文字在等寬字型下佔兩欄，len() 對不齊。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
