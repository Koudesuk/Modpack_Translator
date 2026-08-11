from __future__ import annotations

import json
import os
import shutil
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from modpack_translator.pipeline.preprocessor import (
    format_snbt_lang,
    parse_json_lang,
    parse_legacy_lang,
    parse_snbt_lang,
    replace_inline_snbt_text,
)


# ------------------------------------------------------------------ in-place JSON

def write_inplace_json(
    source_file: Path,
    lang_code: str,
    translations: dict[str, str],
    target_file: Path | None = None,
) -> None:
    target = target_file or source_file.parent / f"{lang_code}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, str] = {}
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
    existing.update(translations)
    target.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


# ------------------------------------------------------------------ jar injection

_JAR_SIGNATURE_SUFFIXES = (".SF", ".DSA", ".RSA", ".EC")
_MODONOMICON_UNIFONT_PATH = "assets/modonomicon/font/include/unifont.json"
_MODONOMICON_UNIFONT_FALLBACK = {
    "providers": [
        {
            "type": "reference",
            "id": "minecraft:include/unifont",
        },
    ],
}


def backup_mods(game_root: Path) -> int:
    """Back up every mod jar once before modifying jars in place."""
    mods_dir = game_root / "mods"
    if not mods_dir.is_dir():
        return 0

    backup_dir = game_root / "mods_bak"
    backup_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for jar in sorted(mods_dir.glob("*.jar")):
        backup = backup_dir / jar.name
        if backup.exists():
            continue
        shutil.copy2(jar, backup)
        count += 1
    return count


def touches_asset_packs(source_files: Iterable[Path], game_root: Path) -> bool:
    """這批目標會不會寫進資源包／光影包——決定要不要先備份。"""
    roots = (game_root / "resourcepacks", game_root / "shaderpacks")
    return any(
        any(Path(source).is_relative_to(root) for root in roots)
        for source in source_files
    )


def backup_asset_packs(game_root: Path) -> int:
    """Back up resource/shader packs once before writing translations into them."""
    count = 0
    for folder in ("resourcepacks", "shaderpacks"):
        source_dir = game_root / folder
        if not source_dir.is_dir():
            continue
        backup_dir = game_root / f"{folder}_bak"
        for entry in sorted(source_dir.iterdir()):
            backup = backup_dir / entry.name
            if backup.exists():
                continue
            backup_dir.mkdir(parents=True, exist_ok=True)
            if entry.is_dir():
                shutil.copytree(entry, backup)
            else:
                shutil.copy2(entry, backup)
            count += 1
    return count


def backup_quest_configs(game_root: Path) -> int:
    """Back up mutable quest config folders once before direct inline edits."""
    candidates = [
        game_root / "config" / "ftbquests" / "quests",
        game_root / "config" / "heracles" / "quests",
        game_root / "config" / "betterquesting",
        game_root / "kubejs" / "assets",
    ]
    count = 0
    backup_root = game_root / "quests_bak"
    for source in candidates:
        if not source.exists():
            continue
        destination = backup_root / source.relative_to(game_root)
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
        count += 1
    return count


def backup_data_files(game_root: Path, files: Iterable[Path]) -> int:
    """就地改寫資料檔（Origins 能力定義之類）前，逐檔備份到 `data_bak/<相對路徑>`。

    刻意逐檔而非整棵複製：這些檔案散在 datapacks／config／kubejs 底下，那幾個資料夾
    可能夾著整包世界資料或結構檔，整棵複製會讓備份本身變成災難。已存在的備份不覆蓋
    ——第一次備份的才是使用者的原始檔。
    """
    backup_root = game_root / "data_bak"
    count = 0
    for source in files:
        source = Path(source)
        if not source.is_file() or not source.is_relative_to(game_root):
            continue
        destination = backup_root / source.relative_to(game_root)
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        count += 1
    return count


def patch_modonomicon_unicode_fonts(game_root: Path) -> int:
    """Wire Modonomicon's empty unifont include to Minecraft's CJK-capable unifont."""
    mods_dir = game_root / "mods"
    if not mods_dir.is_dir():
        return 0

    patched = 0
    for jar_path in sorted(mods_dir.glob("*.jar")):
        if _patch_modonomicon_unicode_font(jar_path):
            patched += 1
    return patched


def _patch_modonomicon_unicode_font(jar_path: Path) -> bool:
    try:
        with zipfile.ZipFile(jar_path) as zf:
            if _MODONOMICON_UNIFONT_PATH not in zf.namelist():
                return False
            raw = zf.read(_MODONOMICON_UNIFONT_PATH).decode("utf-8-sig")
            data = json.loads(raw)
    except (zipfile.BadZipFile, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False

    if not _needs_modonomicon_unicode_font_patch(data):
        return False

    payload = json.dumps(_MODONOMICON_UNIFONT_FALLBACK, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        _rewrite_jar(jar_path, {_MODONOMICON_UNIFONT_PATH: payload})
    except OSError:
        return False
    return True


def _needs_modonomicon_unicode_font_patch(data: Any) -> bool:
    if not isinstance(data, dict):
        return False
    providers = data.get("providers")
    return isinstance(providers, list) and not providers


def read_jar_json_lang(jar_path: Path, path_in_jar: str | None) -> dict[str, str]:
    if not path_in_jar:
        return {}
    try:
        with zipfile.ZipFile(jar_path) as zf:
            if path_in_jar not in zf.namelist():
                return {}
            return parse_json_lang(zf.read(path_in_jar).decode("utf-8-sig"))
    except (zipfile.BadZipFile, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def read_jar_legacy_lang(jar_path: Path, path_in_jar: str | None) -> dict[str, str]:
    if not path_in_jar:
        return {}
    try:
        with zipfile.ZipFile(jar_path) as zf:
            if path_in_jar not in zf.namelist():
                return {}
            return parse_legacy_lang(zf.read(path_in_jar).decode("utf-8-sig"))
    except (zipfile.BadZipFile, OSError, UnicodeDecodeError):
        return {}


def read_jar_json_file(jar_path: Path, path_in_jar: str | None) -> dict[str, Any]:
    if not path_in_jar:
        return {}
    try:
        with zipfile.ZipFile(jar_path) as zf:
            if path_in_jar not in zf.namelist():
                return {}
            data = json.loads(zf.read(path_in_jar).decode("utf-8-sig"))
            return data if isinstance(data, dict) else {}
    except (zipfile.BadZipFile, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}


def write_jar_json_lang(jar_path: Path, path_in_jar: str, translations: dict[str, str]) -> None:
    existing = read_jar_json_lang(jar_path, path_in_jar)
    existing.update(translations)
    payload = json.dumps(existing, ensure_ascii=False, indent=2).encode("utf-8")
    _rewrite_jar(jar_path, {path_in_jar: payload})


def write_jar_legacy_lang(jar_path: Path, path_in_jar: str, translations: dict[str, str]) -> None:
    existing = read_jar_legacy_lang(jar_path, path_in_jar)
    existing.update(translations)
    payload = ("\n".join(f"{k}={v}" for k, v in existing.items()) + "\n").encode("utf-8")
    _rewrite_jar(jar_path, {path_in_jar: payload})


def write_jar_json_file(jar_path: Path, path_in_jar: str, payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    _rewrite_jar(jar_path, {path_in_jar: raw})


def read_jar_text_or_none(jar_path: Path, path_in_jar: str | None) -> str | None:
    """讀 jar 內的純文字檔；缺檔或解碼失敗回 None。

    （preprocessor 另有一個同用途但缺檔會拋例外的 read_jar_text，別搞混。）"""
    if not path_in_jar:
        return None
    try:
        with zipfile.ZipFile(jar_path) as zf:
            if path_in_jar not in zf.namelist():
                return None
            return zf.read(path_in_jar).decode("utf-8-sig")
    except (zipfile.BadZipFile, OSError, UnicodeDecodeError):
        return None


def write_jar_text(jar_path: Path, path_in_jar: str, text: str) -> None:
    _rewrite_jar(jar_path, {path_in_jar: text.encode("utf-8")})


def _rewrite_jar(jar_path: Path, replacements: dict[str, bytes]) -> None:
    tmp_path = jar_path.with_name(f"{jar_path.name}.tmp")
    try:
        with zipfile.ZipFile(jar_path, "r") as src, zipfile.ZipFile(tmp_path, "w") as dst:
            infos = {info.filename: info for info in src.infolist()}
            replacement_paths = set(replacements)

            # ZIP 格式允許同名 entry 重複，少數模組 jar 確實如此。src.read(name)
            # 只會回傳最後一筆，照原樣逐項複製會把所有重複項寫成同一份內容，並在
            # 輸出 jar 留下重複。以「最後一筆為準」去重，與 zipfile 的查表語意一致；
            # 保持原始順序（MANIFEST 之類仍在前）。
            last_index = {info.filename: i for i, info in enumerate(src.infolist())}
            for i, info in enumerate(src.infolist()):
                if info.filename in replacement_paths or _is_signature_file(info.filename):
                    continue
                if last_index[info.filename] != i:
                    continue
                dst.writestr(_clone_zip_info(info), src.read(info.filename))

            for path, data in replacements.items():
                info = _clone_zip_info(infos[path], filename=path) if path in infos else _new_zip_info(path)
                dst.writestr(info, data)

        os.replace(tmp_path, jar_path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _is_signature_file(path_in_jar: str) -> bool:
    upper = path_in_jar.upper()
    return upper.startswith("META-INF/") and upper.endswith(_JAR_SIGNATURE_SUFFIXES)


def _clone_zip_info(info: zipfile.ZipInfo, filename: str | None = None) -> zipfile.ZipInfo:
    cloned = zipfile.ZipInfo(filename or info.filename, date_time=info.date_time)
    cloned.comment = info.comment
    cloned.extra = info.extra
    cloned.internal_attr = info.internal_attr
    cloned.external_attr = info.external_attr
    cloned.create_system = info.create_system
    cloned.compress_type = info.compress_type
    return cloned


def _new_zip_info(path_in_jar: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path_in_jar)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


# ------------------------------------------------------------------ in-place SNBT

def read_existing_snbt(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return parse_snbt_lang(path.read_text(encoding="utf-8"))


def write_inplace_snbt(
    source_file: Path,
    lang_code: str,
    translations: dict[str, str],
    target_file: Path | None = None,
) -> None:
    target = target_file or source_file.parent / f"{lang_code}.snbt"
    target.parent.mkdir(parents=True, exist_ok=True)
    source_values = _read_snbt_source(source_file)
    existing = read_existing_snbt(target)
    if source_values:
        existing = {key: value for key, value in existing.items() if key in source_values}
        translations = {key: value for key, value in translations.items() if key in source_values}
    merged = dict(source_values)
    merged.update(existing)
    merged.update(translations)
    ordered = _ordered_snbt_lang(source_file, merged)
    target.write_text(format_snbt_lang(ordered), encoding="utf-8")


def _read_snbt_source(source_file: Path) -> dict[str, str]:
    try:
        return parse_snbt_lang(source_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return {}


def _ordered_snbt_lang(source_file: Path, values: dict[str, str]) -> dict[str, str]:
    ordered: dict[str, str] = {}
    source = _read_snbt_source(source_file)

    for key in source:
        if key in values:
            ordered[key] = values[key]
    for key, value in values.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


# ------------------------------------------------------------------ in-place legacy .lang

def read_existing_bq_lang(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return parse_legacy_lang(path.read_text(encoding="utf-8"))


def write_inplace_bq_lang(
    source_file: Path,
    lang_code: str,
    translations: dict[str, str],
    target_file: Path | None = None,
) -> None:
    target = target_file or source_file.parent / f"{lang_code}.lang"
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = read_existing_bq_lang(target)
    existing.update(translations)
    lines = [f"{k}={v}" for k, v in existing.items()]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_inline_snbt(source_file: Path, translations: dict[str, str]) -> None:
    raw = source_file.read_text(encoding="utf-8")
    updated = replace_inline_snbt_text(raw, translations)
    if updated != raw:
        source_file.write_text(updated, encoding="utf-8")
