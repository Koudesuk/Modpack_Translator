"""離線建置 Minecraft 官方用語庫（en_us → zh_tw 名稱對照表）。

從 InventivetalentDev/minecraft-assets 取得各版本官方語言檔，抽出「名稱類」詞條
（方塊、物品、生物、生態域、狀態效果、附魔、屬性）後合併成單一對照表，輸出到
assets/glossary/minecraft_zh_tw.json。產物 commit 進 repo，程式執行期不連網。

為什麼是「一份合併表」而不是每個版本一份：實測 1.16.5 → 1.21.5 的譯名衝突只有
0.55%（1,811 詞中 10 個，全是 Mojang 自己改的用詞，如 切割→切製、凋零→凋零怪），
而工具本身並不知道使用者的模組包是哪個 MC 版本。為 10 個詞維護 19 份對照表不划算，
衝突時取較新版本＝現行官方譯名。

用法：
    uv run python scripts/build_glossary.py
    uv run python scripts/build_glossary.py --versions 1.21.5 1.20.1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from modpack_translator.config import MC_PACK_FORMAT  # noqa: E402

LANG_URL = (
    "https://raw.githubusercontent.com/InventivetalentDev/minecraft-assets/"
    "{version}/assets/minecraft/lang/{name}"
)

# 只收「前綴命中且其後只剩單一段」的鍵，排除旗幟圖樣（block.minecraft.banner.base.black）、
# 唱片作者（item.minecraft.music_disc_13.desc）、床訊息（block.minecraft.spawn.not_valid）
# 這類非名稱詞條。
_NAME_PREFIXES = (
    "block.minecraft.",
    "item.minecraft.",
    "entity.minecraft.",
    "biome.minecraft.",
    "effect.minecraft.",
    "enchantment.minecraft.",
    "structure.minecraft.",
)
# 深層鍵白名單：屬性名與藥水家族值得收錄。
_DEEP_PREFIXES = (
    "attribute.name.",
    "item.minecraft.potion.effect.",
    "item.minecraft.splash_potion.effect.",
    "item.minecraft.lingering_potion.effect.",
    "item.minecraft.tipped_arrow.effect.",
)
# 維度名不在名稱類前綴底下，卻是最常被誤譯的詞。
_EXTRA_KEYS = (
    "advancements.nether.root.title",         # Nether = 地獄（非「下界」）
    "advancements.end.root.title",             # The End = 終界
    "flat_world_preset.minecraft.overworld",   # Overworld = 主世界
)
# 同一英文詞對到多個中文時，前綴排名靠前者勝：
# Wither 取生物的「凋零怪」而非狀態效果的「凋零」。
_PRIORITY = (
    "entity.minecraft.",
    "block.minecraft.",
    "item.minecraft.",
    "effect.minecraft.",
    "enchantment.minecraft.",
    "biome.minecraft.",
    "structure.minecraft.",
    "attribute.name.",
)


def supported_versions() -> list[str]:
    """專案宣稱支援的 MC 版本，由舊到新排序。"""
    def sort_key(version: str) -> tuple[int, ...]:
        return tuple(int(part) for part in version.split("."))

    return sorted(MC_PACK_FORMAT, key=sort_key)


def fetch_lang(version: str, name: str, timeout: float = 30.0) -> dict[str, str]:
    request = Request(LANG_URL.format(version=version, name=name),
                      headers={"User-Agent": "Modpack-Translator-GlossaryBuilder/1.0"})
    with urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{version}/{name} 不是 JSON 物件")
    return data


def _is_name_key(key: str) -> bool:
    if key in _EXTRA_KEYS:
        return True
    if any(key.startswith(p) and len(key) > len(p) for p in _DEEP_PREFIXES):
        return True
    return any(key.startswith(p) and "." not in key[len(p):] for p in _NAME_PREFIXES)


def _priority_rank(key: str) -> int:
    for index, prefix in enumerate(_PRIORITY):
        if key.startswith(prefix):
            return index
    return len(_PRIORITY)


def build_version_terms(en: dict[str, str], zh: dict[str, str]) -> dict[str, str]:
    """單一版本的 {英文名: 繁中名}。"""
    best_rank: dict[str, int] = {}
    terms: dict[str, str] = {}
    for key, en_value in en.items():
        if not _is_name_key(key):
            continue
        zh_value = zh.get(key)
        if not isinstance(en_value, str) or not isinstance(zh_value, str):
            continue
        source, translated = en_value.strip(), zh_value.strip()
        if len(source) < 3 or not translated:
            continue
        if "%" in source or "%" in translated:
            continue
        if source == translated:          # 官方未翻的詞（TNT 等）
            continue
        rank = _priority_rank(key)
        if source in terms and best_rank[source] <= rank:
            continue
        terms[source] = translated
        best_rank[source] = rank
    return terms


def main() -> None:
    parser = argparse.ArgumentParser(description="建置 Minecraft 官方 en→zh_tw 用語庫")
    parser.add_argument("--versions", nargs="*", default=None,
                        help="要合併的 MC 版本（預設為專案支援的全部版本）")
    parser.add_argument("--output", default=None, help="輸出路徑")
    args = parser.parse_args()

    versions = args.versions or supported_versions()
    merged: dict[str, str] = {}
    conflicts = 0

    for version in versions:
        try:
            en = fetch_lang(version, "en_us.json")
            zh = fetch_lang(version, "zh_tw.json")
        except (HTTPError, URLError, ValueError, json.JSONDecodeError) as exc:
            print(f"  {version}: 略過（{type(exc).__name__}: {exc}）")
            continue
        terms = build_version_terms(en, zh)
        conflicts += sum(1 for k, v in terms.items() if k in merged and merged[k] != v)
        merged.update(terms)          # 由舊到新，較新版本覆蓋＝現行官方譯名
        print(f"  {version}: {len(terms):,} 詞（累計 {len(merged):,}）")

    if not merged:
        raise SystemExit("沒有取得任何詞條，請檢查網路連線")

    ordered = dict(sorted(merged.items(), key=lambda kv: kv[0].lower()))
    output = Path(args.output) if args.output else (
        PROJECT_ROOT / "assets" / "glossary" / "minecraft_zh_tw.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(ordered, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"\n合併 {len(versions)} 個版本，跨版本覆蓋 {conflicts} 次")
    print(f"已寫出 {len(ordered):,} 條詞彙 -> {output}")


if __name__ == "__main__":
    main()
