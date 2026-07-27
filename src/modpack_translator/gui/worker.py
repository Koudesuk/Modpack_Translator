from __future__ import annotations

import json
import random
import threading
import time
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from modpack_translator.config import AppConfig
from modpack_translator.pipeline.patcher import (
    backup_asset_packs,
    backup_mods,
    backup_quest_configs,
    touches_asset_packs,
    patch_modonomicon_unicode_fonts,
)
from modpack_translator.pipeline.glossary import default_glossary
from modpack_translator.pipeline.preprocessor import diff_keys
from modpack_translator.pipeline.runner import (
    load_manual_translations,
    manual_translations_path,
    _write_failed_items,
    failed_target_name,
    process_target,
    read_existing_target,
    read_target_strings,
)
from modpack_translator.pipeline.scanner import ModpackScanner, TranslationTarget, resolve_game_root
from modpack_translator.pipeline.translator import GGUFTranslator
from modpack_translator import run_log

# src/modpack_translator/gui/ → 上 4 層到專案根目錄
_PROJECT_ROOT = Path(__file__).parents[3]


def _load_cache(cache_path: Path) -> dict[str, str]:
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    return {}


def _flush_cache(cache_path: Path, cache: dict[str, str]) -> None:
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _filter_pending_targets(all_targets: list[TranslationTarget], lang_code: str) -> list[TranslationTarget]:
    pending: list[TranslationTarget] = []
    for target in all_targets:
        try:
            strings = read_target_strings(target)
            existing = read_existing_target(target, lang_code)
        except Exception:
            pending.append(target)
            continue
        if diff_keys(strings, existing):
            pending.append(target)
    return pending


class ScanWorker(QThread):
    log      = Signal(str)
    finished = Signal(list, dict, int, dict)  # targets, format_counts, total_pairs, samples
    error    = Signal(str)

    def __init__(self, modpack_path: Path, skip_mods: bool, skip_quests: bool, lang_code: str = "zh_tw"):
        super().__init__()
        self._modpack_path = modpack_path
        self._skip_mods    = skip_mods
        self._skip_quests  = skip_quests
        self._lang_code    = lang_code

    def run(self):
        try:
            scanner = ModpackScanner()

            root = scanner._resolve_game_root(self._modpack_path)
            self.log.emit(f"偵測到遊戲根目錄：{root}")

            targets = _filter_pending_targets(scanner.scan(self._modpack_path, self._lang_code), self._lang_code)

            if self._skip_mods:
                targets = [t for t in targets if t.output_mode != "jar_inject"]
            if self._skip_quests:
                targets = [t for t in targets if t.output_mode != "in_place"]

            fmt_counts: dict[str, int] = {}
            for t in targets:
                fmt_counts[t.format] = fmt_counts.get(t.format, 0) + 1

            SAMPLES_PER_FMT = 3
            pair_counts: dict[str, int] = {}
            samples: dict[str, list[tuple[str, str, str]]] = {}
            total_pairs = 0

            for i, target in enumerate(targets):
                try:
                    strings = read_target_strings(target)
                    existing = read_existing_target(target, self._lang_code)
                except Exception:
                    continue
                pending_keys = diff_keys(strings, existing)
                pending = {k: strings[k] for k in pending_keys}

                fmt = target.format
                pair_counts[fmt] = pair_counts.get(fmt, 0) + len(pending)
                total_pairs += len(pending)

                if fmt not in samples:
                    samples[fmt] = []
                if len(samples[fmt]) < SAMPLES_PER_FMT and pending:
                    key, val = random.choice(list(pending.items()))
                    if val.strip():
                        samples[fmt].append((target.mod_id, key, val))

            self.finished.emit(targets, fmt_counts, total_pairs, samples)

        except Exception as exc:
            self.log.emit(f"[致命錯誤] {exc!r}\n{traceback.format_exc().rstrip()}")
            self.error.emit(str(exc))


class TranslateWorker(QThread):
    log          = Signal(str)
    progress     = Signal(int, int, str, int) # current_idx, total, mod_id, pairs_done_so_far
    pair_progress = Signal(int)               # 每條字串完成後：累計已處理對數
    finished     = Signal(int, int, int, int, object)
    # translated, cached, fallback, failed_files, failed_items
    # failed_items: list[(TranslationTarget, {鍵: 原文})]，供主視窗開手動補譯視窗
    error    = Signal(str)

    def __init__(
        self,
        targets: list[TranslationTarget],
        cfg: AppConfig,
        modpack_path: Path,
        retry_count: int = 0,
    ):
        super().__init__()
        self._targets      = targets
        self._cfg          = cfg
        self._modpack_path = modpack_path
        self._retry_count  = retry_count
        self._cancel       = False
        self._translator: GGUFTranslator | None = None

    def cancel(self):
        self._cancel = True
        if self._translator is not None:
            self._translator.close()

    @staticmethod
    def _target_location(target: TranslationTarget) -> str:
        return target.path_in_jar or str(target.target_file or target.source_file)

    @classmethod
    def _log_target_start(cls, target: TranslationTarget, index: int, total: int) -> None:
        """檔案標題必須印在該檔的逐條明細之前，否則讀日誌的人分不清哪條屬於哪個檔。"""
        run_log.detail(
            f"▼ [{index}/{total}] {target.mod_id} · {target.format}\n"
            f"    來源：{target.source_file}\n"
            f"    位置：{cls._target_location(target)}"
        )

    @staticmethod
    def _log_target_done(stats, seconds: float) -> None:
        run_log.detail(
            f"▲ 來源 {stats.source_total:,} 條 → 模型 {stats.translated:,}"
            f"／快取 {stats.cached:,}"
            f"／回退 {stats.fallback:,}"
            f"／不需翻譯 {stats.skipped:,}"
            f"／既有譯文已可用 {stats.already_ok:,}"
            f"　（{seconds:.1f} 秒）"
        )

    def run(self):
        self._thread_id = threading.current_thread().ident
        try:
            cache_path = self._cfg.paths.translation_cache
            cache = _load_cache(cache_path)
            run_started = time.monotonic()
            total_translated = total_cached = total_fallback = total_skipped = 0
            total_already = total_source = 0
            skipped_targets = 0
            total = len(self._targets)
            cache_dirty = 0
            failed_by_target: dict[str, dict[str, str]] = {}
            failed_items: list[tuple[TranslationTarget, dict[str, str]]] = []
            manual = load_manual_translations(
                manual_translations_path(self._cfg.paths.output_root))
            if manual:
                self.log.emit(f"已載入 {len(manual):,} 條手動補譯，將優先套用")
            total_pairs_done = 0

            game_root = resolve_game_root(self._modpack_path)
            if any(t.output_mode == "jar_inject" for t in self._targets):
                backed_up = backup_mods(game_root)
                self.log.emit(f"已備份 {backed_up} 個原始模組 jar 至 mods_bak/")
                patched_fonts = patch_modonomicon_unicode_fonts(game_root)
                if patched_fonts:
                    self.log.emit(f"已修補 {patched_fonts} 個 Modonomicon Unicode 字型 fallback")
            if any(t.output_mode == "in_place" for t in self._targets):
                backed_up = backup_quest_configs(game_root)
                self.log.emit(f"已備份 {backed_up} 個任務/設定資料夾至 quests_bak/")
            if touches_asset_packs((t.source_file for t in self._targets), game_root):
                backed_up = backup_asset_packs(game_root)
                self.log.emit(f"已備份 {backed_up} 個資源包／光影包至 *_bak/")

            self.log.emit("正在連線或啟動本機模型服務，請稍候…")
            translator = None
            try:
                translator = GGUFTranslator(self._cfg.model, self._cfg.language.system_prompt)
                translator.glossary = default_glossary(self._cfg.paths.output_root)
                self._translator = translator
                if translator.glossary:
                    self.log.emit(f"已載入用語庫 {len(translator.glossary):,} 條詞彙")
            except Exception as exc:
                self.log.emit(
                    f"[致命錯誤] 模型服務啟動失敗：{exc!r}\n"
                    f"{traceback.format_exc().rstrip()}\n"
                    f"llama-server 的詳細輸出見 .runtime/llama-server.log"
                )
                self.error.emit(f"模型服務啟動失敗：{exc}")
                return
            try:
                self.log.emit("模型服務已就緒，開始翻譯…")

                # 每條字串完成後觸發：更新累計數並節流發送信號（每 0.5 秒最多 1 次）
                _last_emit_t = [0.0]

                def _on_pair_done(n: int = 1) -> None:
                    nonlocal total_pairs_done
                    total_pairs_done += n
                    now = time.monotonic()
                    if now - _last_emit_t[0] >= 0.5:
                        self.pair_progress.emit(total_pairs_done)
                        _last_emit_t[0] = now

                for i, target in enumerate(self._targets):
                    if self._cancel:
                        self.log.emit("已由使用者取消翻譯。")
                        break

                    self.progress.emit(i, total, target.mod_id, total_pairs_done)

                    started = time.monotonic()
                    self._log_target_start(target, i + 1, total)
                    try:
                        stats = process_target(
                            target, translator, cache,
                            self._cfg.language.code,
                            self._retry_count,
                            cancel_check=lambda: self._cancel,
                            on_pair_done=_on_pair_done,
                            manual=manual,
                        )
                        total_translated += stats.translated
                        total_cached     += stats.cached
                        total_fallback   += stats.fallback
                        total_skipped    += stats.skipped
                        total_already   += stats.already_ok
                        total_source    += stats.source_total
                        # total_pairs_done 已由 _on_pair_done 累加，不再重複計算
                        if stats.failed:
                            failed_by_target[failed_target_name(target)] = stats.failed
                            failed_items.append((target, stats.failed))
                        self._log_target_done(stats, time.monotonic() - started)
                    except Exception as exc:
                        if self._cancel:
                            # 取消時 close() 會殺掉模型服務，飛行中的請求必然被切斷
                            # （WinError 10054）。那是取消的結果，不是故障，不該報成
                            # 「略過」也不該計入錯誤數——否則使用者回報時會被誤導。
                            run_log.detail(f"▲ 取消時中斷：{exc.__class__.__name__}")
                            break
                        # 帶上 traceback：只寫 str(exc) 的話，使用者回報過來也查不出是哪一行。
                        skipped_targets += 1
                        self.log.emit(
                            f"[警告] 略過 {target.mod_id}/{target.format}"
                            f"（{target.source_file}）：{exc!r}\n"
                            + traceback.format_exc().rstrip()
                        )
                        continue

                    cache_dirty += 1
                    if cache_dirty >= 100:
                        _flush_cache(cache_path, cache)
                        cache_dirty = 0
                        self.log.emit(f"進度已儲存（{i + 1}/{total} 個檔案）…")

                _flush_cache(cache_path, cache)

                # 寫出失敗項目
                failed_dir = _PROJECT_ROOT / "Failed Items"
                failed_files_written = _write_failed_items(failed_by_target, failed_dir)

                run_log.section("結果統計")
                elapsed = time.monotonic() - run_started
                handled = total_translated + total_cached + total_fallback
                run_log.table([
                    ("處理檔案", f"{len(self._targets) - skipped_targets:,} / {total:,}"
                                 f"（{skipped_targets} 個因錯誤略過）"),
                    ("來源字串總計", f"{total_source:,}"),
                    ("　模型翻譯", f"{total_translated:,}"),
                    ("　快取／手動命中", f"{total_cached:,}"),
                    ("　回退原文（失敗）", f"{total_fallback:,}"),
                    ("　判定不需翻譯", f"{total_skipped:,}"),
                    ("　既有譯文已可用", f"{total_already:,}"),
                    ("失敗率", f"{total_fallback / max(total_translated + total_fallback, 1) * 100:.1f}%"
                              f"（以送進模型的條數為分母）"),
                    ("本次實際處理", f"{handled:,}"),
                    ("耗時", f"{elapsed / 60:.1f} 分鐘"
                            f"（{handled / max(elapsed, 1):.1f} 條/秒）"),
                    ("快取條目", f"{len(cache):,}"),
                ])

                if failed_by_target:
                    total_failed = sum(len(v) for v in failed_by_target.values())
                    by_format: dict[str, int] = {}
                    for target, failed in failed_items:
                        by_format[target.format] = by_format.get(target.format, 0) + len(failed)
                    run_log.write(f"失敗項目 {total_failed:,} 條，依格式分佈：")
                    run_log.table(sorted(by_format.items(), key=lambda kv: -kv[1]))
                    run_log.write(f"失敗最多的檔案（前 20 名，完整清單見 {failed_dir}）：")
                    run_log.table([
                        (name, f"{len(items):,} 條")
                        for name, items in sorted(
                            failed_by_target.items(), key=lambda kv: -len(kv[1]))[:20]
                    ])
                    self.log.emit(
                        f"⚠ {failed_files_written} 個模組/任務書含 {total_failed:,} 條翻譯失敗項目，"
                        f"詳見 {failed_dir}"
                    )

                self.finished.emit(total_translated, total_cached, total_fallback,
                                   failed_files_written, failed_items)
            finally:
                translator.close()
                self._translator = None

        except Exception as exc:
            self.log.emit(f"[致命錯誤] {exc!r}\n{traceback.format_exc().rstrip()}")
            self.error.emit(str(exc))
