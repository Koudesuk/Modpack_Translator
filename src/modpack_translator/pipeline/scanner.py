from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from modpack_translator.pipeline.citadel_book import extract_citadel_text
from modpack_translator.pipeline.guide_md import extract_guide_text
from modpack_translator.pipeline.preprocessor import (
    diff_keys,
    parse_json_lang,
    parse_legacy_lang,
    parse_snbt_lang,
    read_patchouli_text,
    read_inline_snbt_text,
)


@dataclass
class TranslationTarget:
    source_file: Path
    path_in_jar: str | None
    mod_id: str
    format: str       # json_lang | legacy_lang | patchouli_json | ftbq_snbt | ftbq_inline_snbt | heracles_snbt | heracles_inline_snbt | bq_lang | kubejs_json
    output_mode: str  # jar_inject | in_place
    output_lang_code: str = "zh_tw"
    target_path_in_jar: str | None = None
    target_file: Path | None = None


_GUIDE_LANG_DIR_RE = re.compile(r"/_[a-z]{2}(?:_[a-z]{2,3})?/")
_CJK_RE = re.compile(r"[㐀-鿿]")
_GUIDE_FRONTMATTER_RE = re.compile(r"^navigation\s*:", re.MULTILINE)


def _guide_root(parts: list[str]) -> str | None:
    """指南根目錄。`assets/<ns>/guides/<a>/<b>` 是 GuideME 預設佈局，其餘取
    `assets/<ns>/<資料夾>`。以 11 個出貨了譯本樹的模組逐一驗證過，全數相符。"""
    if len(parts) < 4 or parts[0] != "assets":
        return None
    if parts[2] == "guides":
        return "/".join(parts[:5]) if len(parts) > 5 else None
    return "/".join(parts[:3])


def _read_member_text(zf: zipfile.ZipFile, path_in_jar: str) -> str | None:
    try:
        return zf.read(path_in_jar).decode("utf-8-sig")
    except (KeyError, UnicodeDecodeError):
        return None


def _mirror_locale_case(source_locale: str, lang_code: str) -> str:
    """依來源語言碼的大小寫慣例產生目標語言碼。

    `en_US` → `zh_TW`、`en_us` → `zh_tw`。光影包沿用舊式大寫地區碼，模組則用小寫；
    與其押寶其中一種，不如照著同一個資料夾裡既有的寫法走。
    """
    lower = lang_code.lower()
    if "_" not in lower or "_" not in source_locale:
        return lower
    left, right = lower.split("_", 1)
    return f"{left}_{right.upper()}" if source_locale.split("_", 1)[1].isupper() else lower


def resolve_game_root(path: Path) -> Path:
    """Detect the actual Minecraft game root inside various launcher structures."""
    # Prism Launcher (CurseForge pack import): instance_dir/minecraft/
    if (path / "minecraft").is_dir():
        return path / "minecraft"

    # Prism Launcher / MultiMC (manual pack): instance_dir/.minecraft/
    if (path / ".minecraft").is_dir():
        return path / ".minecraft"

    # GDLauncher: instance_dir/files/
    if (path / "files" / "mods").is_dir():
        return path / "files"

    # CurseForge App / ATLauncher / FTB App / manual: use path directly
    return path


class ModpackScanner:
    def scan(self, modpack_path: Path, lang_code: str = "zh_tw") -> list[TranslationTarget]:
        root = self._resolve_game_root(modpack_path)
        print(f"Detected game root: {root}")

        targets: list[TranslationTarget] = []

        mods_dir = root / "mods"
        if mods_dir.is_dir():
            for jar in sorted(mods_dir.glob("*.jar")):
                targets.extend(self._scan_jar(jar, lang_code))

        targets.extend(self._scan_resource_packs(root, lang_code))
        targets.extend(self._scan_shader_packs(root, lang_code))
        targets.extend(self._scan_ftbquests(root, lang_code))
        targets.extend(self._scan_heracles(root, lang_code))
        targets.extend(self._scan_betterquesting(root, lang_code))
        targets.extend(self._scan_kubejs(root, lang_code))

        return targets

    def _resolve_game_root(self, path: Path) -> Path:
        return resolve_game_root(path)

    # ------------------------------------------------------------------ jars

    def _scan_jar(self, jar_path: Path, lang_code: str) -> list[TranslationTarget]:
        targets: list[TranslationTarget] = []
        try:
            with zipfile.ZipFile(jar_path) as zf:
                names = zf.namelist()
                name_set = set(names)
                for name in names:
                    parts = name.split("/")
                    lang_ext = self._source_lang_extension(parts)
                    if lang_ext:
                        mod_id = parts[1]
                        target_path = self._target_lang_path(name, lang_code)
                        existing_paths = self._existing_lang_paths(name, lang_code, name_set)
                        if self._jar_lang_needs_translation(zf, name, existing_paths, lang_ext):
                            targets.append(TranslationTarget(
                                source_file=jar_path,
                                path_in_jar=name,
                                mod_id=mod_id,
                                format="json_lang" if lang_ext == "json" else "legacy_lang",
                                output_mode="jar_inject",
                                output_lang_code=lang_code,
                                target_path_in_jar=target_path,
                            ))

                    elif name.endswith(".txt") and parts[0] == "assets":
                        target = self._citadel_page_target(zf, name, parts, lang_code, name_set)
                        if target is not None:
                            targets.append(target)

                    elif name.endswith(".md") and parts[0] == "assets":
                        target = self._guide_page_target(zf, name, parts, lang_code, name_set)
                        if target is not None:
                            targets.append(target)

                    elif (
                        len(parts) >= 3
                        and parts[0] == "assets"
                        and "patchouli_books" in parts
                        and name.endswith(".json")
                        and not name.endswith("/")
                    ):
                        locale_paths = self._patchouli_locale_paths(parts, lang_code)
                        if not locale_paths:
                            continue
                        target_path, existing_paths = locale_paths
                        mod_id = parts[1]
                        if self._patchouli_needs_translation(zf, name, existing_paths):
                            targets.append(TranslationTarget(
                                source_file=jar_path,
                                path_in_jar=name,
                                mod_id=mod_id,
                                format="patchouli_json",
                                output_mode="jar_inject",
                                output_lang_code=lang_code,
                                target_path_in_jar=target_path,
                            ))
        except (zipfile.BadZipFile, OSError):
            pass
        return targets

    # ------------------------------------------------------------ Citadel 圖鑑書

    def _citadel_page_target(
        self,
        zf: zipfile.ZipFile,
        name: str,
        parts: list[str],
        lang_code: str,
        names: set[str],
    ) -> TranslationTarget | None:
        """Citadel 圖鑑書頁：`assets/<ns>/book(s)/…/<locale>/…/*.txt`，逐檔 fallback。

        兩種佈局都見得到（`books/<locale>/…` 與 `book/<書名>/<locale>/…`），所以不
        寫死層數，改成找路徑中的 locale 段來定位。
        """
        if not any(part in ("book", "books") for part in parts):
            return None
        locale_index = next(
            (i for i, part in enumerate(parts) if part.lower() == "en_us"), None
        )
        if locale_index is None:
            return None

        try:
            raw = zf.read(name).decode("utf-8-sig")
        except (KeyError, UnicodeDecodeError):
            return None
        source_text = extract_citadel_text(raw)
        if not source_text:
            return None

        target_parts = list(parts)
        target_parts[locale_index] = lang_code.lower()
        target_path = "/".join(target_parts)

        # 檔級判定，而非逐段 diff：中文得重新折行，輸出行數與原文對不上，行號
        # 當鍵就會位移。譯過的頁面必然含中文，這個判斷簡單且冪等。
        if target_path in names:
            existing_raw = _read_member_text(zf, target_path)
            if existing_raw and _CJK_RE.search(existing_raw):
                return None

        return TranslationTarget(
            source_file=Path(zf.filename),
            path_in_jar=name,
            mod_id=parts[1],
            format="citadel_txt",
            output_mode="jar_inject",
            output_lang_code=lang_code,
            target_path_in_jar=target_path,
        )

    # ------------------------------------------------------------ GuideME 指南

    def _guide_page_target(
        self,
        zf: zipfile.ZipFile,
        name: str,
        parts: list[str],
        lang_code: str,
        names: set[str],
    ) -> TranslationTarget | None:
        """GuideME 指南頁：譯文寫進指南根目錄下的 `_<語言>/`，相對路徑不變。

        交付機制由模組自己出貨的 `_zh_cn`／`_ja_jp`／`_pt_br` 樹實證；根目錄的判定
        規則（`guides/<a>/<b>` 為預設佈局，其餘取 `<ns>/<資料夾>`）也已用這些既有
        譯本樹逐一比對過。
        """
        if _GUIDE_LANG_DIR_RE.search(name):
            return None                     # 既有翻譯樹不能當來源
        root = _guide_root(parts)
        if not root:
            return None
        try:
            head = zf.read(name).decode("utf-8-sig")
        except (KeyError, UnicodeDecodeError):
            return None
        if not _GUIDE_FRONTMATTER_RE.search(head[:400]):
            return None                     # README 之類的雜訊 .md

        source_text = extract_guide_text(head)
        if not source_text:
            return None

        relative = name[len(root) + 1:]
        target_path = f"{root}/_{lang_code.lower()}/{relative}"
        existing_raw = _read_member_text(zf, target_path) if target_path in names else None
        existing_text = extract_guide_text(existing_raw) if existing_raw else {}
        if not diff_keys(source_text, existing_text):
            return None

        return TranslationTarget(
            source_file=Path(zf.filename),
            path_in_jar=name,
            mod_id=parts[1],
            format="guideme_md",
            output_mode="jar_inject",
            output_lang_code=lang_code,
            target_path_in_jar=target_path,
        )

    def _source_lang_extension(self, parts: list[str]) -> str | None:
        if len(parts) != 4 or parts[0] != "assets" or parts[2] != "lang":
            return None
        filename = parts[3]
        lower = filename.lower()
        if lower == "en_us.json":
            return "json"
        if lower == "en_us.lang":
            return "lang"
        return None

    def _target_lang_path(self, source_path: str, lang_code: str) -> str:
        """寫入路徑一律小寫。

        Minecraft 載入語言檔時把語言碼正規化成小寫，只認 `zh_tw.json`。少數模組
        出貨大寫的 `zh_TW.json`——那是遊戲讀不到的死檔，跟著它寫等於白翻。
        """
        lang_dir, filename = source_path.rsplit("/", 1)
        ext = filename.rsplit(".", 1)[1]
        return f"{lang_dir}/{lang_code.lower()}.{ext}"

    def _existing_lang_paths(self, source_path: str, lang_code: str, names: set[str]) -> list[str]:
        """既有譯文的所有大小寫變體——遊戲讀不到大寫檔，但內容仍值得重用。"""
        lang_dir, filename = source_path.rsplit("/", 1)
        ext = filename.rsplit(".", 1)[1]
        paths = [f"{lang_dir}/{c}" for c in self._lang_code_candidates(lang_code, ext)]
        return [path for path in paths if path in names]

    def _lang_code_candidates(self, lang_code: str, ext: str) -> list[str]:
        lower = lang_code.lower()
        candidates = [f"{lower}.{ext}"]
        if "_" in lower:
            left, right = lower.split("_", 1)
            candidates.append(f"{left}_{right.upper()}.{ext}")
        return list(dict.fromkeys(candidates))

    def _jar_lang_needs_translation(
        self,
        zf: zipfile.ZipFile,
        source_path: str,
        existing_paths: list[str],
        lang_ext: str,
    ) -> bool:
        try:
            source_raw = zf.read(source_path).decode("utf-8-sig")
            source = parse_json_lang(source_raw) if lang_ext == "json" else parse_legacy_lang(source_raw)
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not source:
            return False

        existing: dict[str, str] = {}
        for path in existing_paths:
            try:
                raw = zf.read(path).decode("utf-8-sig")
                existing.update(parse_json_lang(raw) if lang_ext == "json" else parse_legacy_lang(raw))
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError):
                continue
        return bool(diff_keys(source, existing))

    def _patchouli_locale_paths(self, parts: list[str], lang_code: str) -> tuple[str, list[str]] | None:
        """回傳 (寫入路徑, 既有譯文候選路徑)。寫入一律小寫，理由同 _target_lang_path。"""
        source_locale_idx = next(
            (i for i, part in enumerate(parts) if part.lower() == "en_us"),
            None,
        )
        if source_locale_idx is None:
            return None

        def with_locale(locale: str) -> str:
            target_parts = list(parts)
            target_parts[source_locale_idx] = locale
            return "/".join(target_parts)

        lower = lang_code.lower()
        candidates = [lower]
        if "_" in lower:
            left, right = lower.split("_", 1)
            candidates.append(f"{left}_{right.upper()}")
        return with_locale(lower), [with_locale(c) for c in dict.fromkeys(candidates)]

    def _patchouli_needs_translation(
        self,
        zf: zipfile.ZipFile,
        source_path: str,
        existing_paths: list[str],
    ) -> bool:
        try:
            source_page = json.loads(zf.read(source_path).decode("utf-8-sig"))
        except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
            return False

        source = read_patchouli_text(source_page)
        if not source:
            return False

        names = zf.namelist()
        existing: dict[str, str] = {}
        for path in existing_paths:
            if path not in names:
                continue
            try:
                existing.update(read_patchouli_text(json.loads(zf.read(path).decode("utf-8-sig"))))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
        return bool(diff_keys(source, existing))

    # ---------------------------------------------------------- local lang files

    def _is_source_locale_name(self, name: str, target_lang: str) -> bool:
        normalized = self._normalize_locale(name)
        target = self._normalize_locale(target_lang)
        if normalized == target:
            return False
        return normalized == "en_us" or normalized.startswith("en_") or normalized == "en"

    def _is_locale_like_name(self, name: str) -> bool:
        normalized = self._normalize_locale(name)
        return bool(re.fullmatch(r"[a-z]{2,3}(?:_[a-z]{2,3})?", normalized))

    def _normalize_locale(self, value: str) -> str:
        stem = Path(value).stem
        return stem.replace("-", "_").lower()

    def _is_ignored_lang_path(self, path: Path) -> bool:
        ignored_parts = {"recovery", "__pycache__"}
        if any(part.lower() in ignored_parts for part in path.parts):
            return True
        return path.name.endswith(".snbt_merged") or path.name.endswith(".bak")

    def _looks_english_like_file(self, path: Path, parser) -> bool:
        try:
            values = [v.strip() for v in parser(path.read_text(encoding="utf-8")).values() if v.strip()]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        if not values:
            return False

        sample = values[:50]
        englishish = sum(1 for value in sample if re.search(r"[A-Za-z]", value))
        cjk = sum(1 for value in sample if re.search(r"[\u3400-\u9fff]", value))
        return englishish >= max(1, len(sample) // 3) and cjk <= max(1, len(sample) // 4)

    def _scan_file_has_pending_text(self, source_file: Path, target_file: Path, parser) -> bool:
        try:
            source = parser(source_file.read_text(encoding="utf-8"))
            existing = parser(target_file.read_text(encoding="utf-8")) if target_file.exists() else {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        return bool(diff_keys(source, existing))

    def _target_flat_locale_file(self, source_file: Path, lang_code: str) -> Path:
        for locale in self._locale_candidates(lang_code):
            candidate = source_file.with_name(f"{locale}{source_file.suffix}")
            if candidate.exists():
                return candidate
        return source_file.with_name(f"{lang_code.lower()}{source_file.suffix}")

    def _target_split_locale_file(self, lang_root: Path, source_file: Path, lang_code: str) -> Path:
        relative = source_file.relative_to(lang_root)
        parts = list(relative.parts)
        parts[0] = self._existing_locale_dir_name(lang_root, lang_code)
        return lang_root.joinpath(*parts)

    def _locale_candidates(self, lang_code: str) -> list[str]:
        lower = lang_code.lower()
        candidates = [lower]
        if "_" in lower:
            left, right = lower.split("_", 1)
            candidates.append(f"{left}_{right.upper()}")
        return list(dict.fromkeys(candidates))

    def _existing_locale_dir_name(self, lang_root: Path, lang_code: str) -> str:
        for locale in self._locale_candidates(lang_code):
            if (lang_root / locale).is_dir():
                return locale
        return lang_code.lower()

    def _scan_snbt_lang_tree(self, lang_root: Path, mod_id: str, fmt: str, lang_code: str) -> list[TranslationTarget]:
        if not lang_root.is_dir():
            return []

        targets: list[TranslationTarget] = []
        for lang_file in sorted(lang_root.rglob("*.snbt")):
            if self._is_ignored_lang_path(lang_file):
                continue

            relative = lang_file.relative_to(lang_root)
            parts = relative.parts
            if len(parts) == 1:
                locale_name = lang_file.stem
                target_file = self._target_flat_locale_file(lang_file, lang_code)
            else:
                locale_name = parts[0]
                target_file = self._target_split_locale_file(lang_root, lang_file, lang_code)

            if not self._is_source_locale_name(locale_name, lang_code):
                if self._is_locale_like_name(locale_name) or not self._looks_english_like_file(lang_file, parse_snbt_lang):
                    continue

            if not self._scan_file_has_pending_text(lang_file, target_file, parse_snbt_lang):
                continue

            targets.append(TranslationTarget(
                source_file=lang_file,
                path_in_jar=None,
                mod_id=mod_id,
                format=fmt,
                output_mode="in_place",
                output_lang_code=lang_code,
                target_file=target_file,
            ))
        return targets

    def _scan_lang_files(self, root: Path, mod_id: str, fmt: str, suffix: str, parser, lang_code: str) -> list[TranslationTarget]:
        if not root.is_dir():
            return []

        targets: list[TranslationTarget] = []
        for lang_file in sorted(root.rglob(f"*{suffix}")):
            if self._is_ignored_lang_path(lang_file):
                continue
            locale_name = lang_file.stem
            if not self._is_source_locale_name(locale_name, lang_code):
                if self._is_locale_like_name(locale_name) or not self._looks_english_like_file(lang_file, parser):
                    continue

            target_file = self._target_flat_locale_file(lang_file, lang_code)
            if not self._scan_file_has_pending_text(lang_file, target_file, parser):
                continue
            targets.append(TranslationTarget(
                source_file=lang_file,
                path_in_jar=None,
                mod_id=mod_id,
                format=fmt,
                output_mode="in_place",
                output_lang_code=lang_code,
                target_file=target_file,
            ))
        return targets

    def _scan_inline_snbt_files(self, root: Path, mod_id: str, fmt: str) -> list[TranslationTarget]:
        if not root.is_dir():
            return []

        skip_dirs = {"lang", "data", "progress", "recovery"}
        targets: list[TranslationTarget] = []
        for source_file in sorted(root.rglob("*.snbt")):
            relative_parts = {part.lower() for part in source_file.relative_to(root).parts[:-1]}
            if relative_parts & skip_dirs:
                continue
            if self._is_ignored_lang_path(source_file):
                continue
            try:
                strings = read_inline_snbt_text(source_file)
            except (OSError, UnicodeDecodeError):
                continue
            if not strings:
                continue
            targets.append(TranslationTarget(
                source_file=source_file,
                path_in_jar=None,
                mod_id=mod_id,
                format=fmt,
                output_mode="in_place",
            ))
        return targets

    # --------------------------------------------------------------- 資源包

    def _scan_resource_packs(self, game_root: Path, lang_code: str) -> list[TranslationTarget]:
        """資源包的 lang 覆蓋檔。

        模組包常用資源包新增或覆蓋 GUI 文字；那些鍵只存在資源包裡，mod jar 沒有，
        不掃就永遠是英文。資源包 zip 的結構與 jar 相同，直接沿用 _scan_jar。
        """
        packs_dir = game_root / "resourcepacks"
        if not packs_dir.is_dir():
            return []

        targets: list[TranslationTarget] = []
        for pack in sorted(packs_dir.iterdir()):
            if pack.suffix.lower() == ".zip" and pack.is_file():
                targets.extend(self._scan_jar(pack, lang_code))
            elif pack.is_dir():
                targets.extend(self._scan_pack_folder(pack, lang_code))
        return targets

    def _scan_pack_folder(self, pack: Path, lang_code: str) -> list[TranslationTarget]:
        """資料夾型資源包：就地寫入語言檔，不必重打包。"""
        targets: list[TranslationTarget] = []
        for lang_dir in sorted(pack.glob("assets/*/lang")):
            if not lang_dir.is_dir():
                continue
            namespace = lang_dir.parent.name
            targets.extend(self._scan_lang_files(
                lang_dir, namespace, "json_lang", ".json", parse_json_lang, lang_code))
            targets.extend(self._scan_lang_files(
                lang_dir, namespace, "legacy_lang", ".lang", parse_legacy_lang, lang_code))
        return targets

    # --------------------------------------------------------------- 光影包

    def _scan_shader_packs(self, game_root: Path, lang_code: str) -> list[TranslationTarget]:
        """光影包的 shaders/lang/。

        OptiFine／Iris 沿用舊式大寫地區碼（`en_US.lang`、`zh_CN.lang`），與模組的
        小寫慣例相反。因此輸出檔名鏡射來源自己的寫法，而不是硬套某一種。
        """
        packs_dir = game_root / "shaderpacks"
        if not packs_dir.is_dir():
            return []

        targets: list[TranslationTarget] = []
        for pack in sorted(packs_dir.iterdir()):
            if not pack.is_dir():
                continue          # zip 光影包需重新打包才能寫入，目前不處理
            lang_dir = pack / "shaders" / "lang"
            if not lang_dir.is_dir():
                continue
            for source in sorted(lang_dir.glob("*.lang")):
                if self._normalize_locale(source.name) != "en_us":
                    continue
                target_file = source.with_name(
                    _mirror_locale_case(source.stem, lang_code) + source.suffix)
                if not self._scan_file_has_pending_text(source, target_file, parse_legacy_lang):
                    continue
                targets.append(TranslationTarget(
                    source_file=source,
                    path_in_jar=None,
                    mod_id=pack.name,
                    format="legacy_lang",
                    output_mode="in_place",
                    output_lang_code=lang_code,
                    target_file=target_file,
                ))
        return targets

    # --------------------------------------------------------------- FTB Quests

    def _scan_ftbquests(self, modpack_path: Path, lang_code: str) -> list[TranslationTarget]:
        config_dir = modpack_path / "config" / "ftbquests"
        if not config_dir.is_dir():
            return []
        quests_dir = config_dir / "quests"
        targets = self._scan_snbt_lang_tree(quests_dir / "lang", "ftbquests", "ftbq_snbt", lang_code)
        targets.extend(self._scan_inline_snbt_files(quests_dir, "ftbquests", "ftbq_inline_snbt"))
        return targets

    # --------------------------------------------------------------- Heracles (Odyssey Quests)

    def _scan_heracles(self, modpack_path: Path, lang_code: str) -> list[TranslationTarget]:
        config_dir = modpack_path / "config" / "heracles"
        if not config_dir.is_dir():
            return []
        quests_dir = config_dir / "quests"
        targets = self._scan_snbt_lang_tree(quests_dir / "lang", "heracles", "heracles_snbt", lang_code)
        targets.extend(self._scan_inline_snbt_files(quests_dir, "heracles", "heracles_inline_snbt"))
        return targets

    # --------------------------------------------------------------- Better Questing (1.12.x)

    def _scan_betterquesting(self, modpack_path: Path, lang_code: str) -> list[TranslationTarget]:
        config_dir = modpack_path / "config" / "betterquesting"
        return self._scan_lang_files(config_dir, "betterquesting", "bq_lang", ".lang", parse_legacy_lang, lang_code)

    # --------------------------------------------------------------- KubeJS lang

    def _scan_kubejs(self, modpack_path: Path, lang_code: str) -> list[TranslationTarget]:
        assets_dir = modpack_path / "kubejs" / "assets"
        if not assets_dir.is_dir():
            return []
        targets: list[TranslationTarget] = []
        for lang_dir in sorted(assets_dir.glob("*/lang")):
            if lang_dir.is_dir():
                namespace = lang_dir.parent.name
                targets.extend(self._scan_lang_files(lang_dir, namespace, "kubejs_json", ".json", parse_json_lang, lang_code))
        return targets
