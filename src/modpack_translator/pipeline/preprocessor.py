from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any


# Single-pass regex: matches structural tokens that must be preserved via {N} encoding.
# Minecraft color/format codes are markup, not words. Encoding them prevents cases like
# "&ricon" being treated as one token and leaving "icon" untranslated.
_PLACEHOLDERS = re.compile(
    r'\$\([^)]*\)'                          # Patchouli: $(thing), $()
    r'|/\$'                                  # Patchouli shorthand close marker
    r'|\[#\]\([0-9A-Fa-f]*\)'                # Modonomicon markdown color markers
    r'|\((?:item|entry|category|book|command|http|https)://[^)]*\)'  # Modonomicon markdown link targets
    # markdown 圖片，且 alt 是識別字而非說明文字：![PEGui1](../pic/aae_intro.png)。
    # 必須連 (src) 一起吃掉——只吃 ![alt] 的話，下一條規則因為 ] 已被消耗而失效。
    # alt 含空白（"![A diagram of the network](…)"）就不match，那種說明文字該翻。
    r'|!\[[A-Za-z0-9_.:#/-]*\]\([^)\s]*\)'
    r'|\]\([^)\s]*\)'                        # markdown link target: [text](./page.md#anchor)
    r'|\\?@[A-Z][A-Z0-9_]*@'                # legacy guide markers: @L@, \@L@, @PAGE@
    r'|\\n'                                 # escaped newline literal
    r'|\\&'                                 # escaped ampersand
    r'|[&§][0-9A-FK-ORa-fk-or]'             # Minecraft color/format codes
    r'|%\d+\$[sdifcbxo%]'                  # positional: %1$s %2$d
    r'|%[sdifcbxo%]'                        # simple: %s %d %f
    r'|\{[^{}]+\}'                          # existing curly-brace placeholders
    # MDX/JSX 元件標籤與 HTML 標籤：<ItemLink id="ae2:controller" />、<powah:EnergyCapacity …/>、
    # <Row>、</Row>、<br/>。GuideME 的表格整格都是這種東西，不當 token 保護的話會被送去
    # 翻譯、翻爛、再被驗證擋下，白白變成「失敗項目」。
    # 屬性必須帶 `=`，所以散文式的角括號（"<Empty text element body>"）不會被誤吃。
    r'|</?[A-Za-z][A-Za-z0-9_.-]*(?::[A-Za-z][A-Za-z0-9_.-]*)?'
    r'(?:\s+[A-Za-z_:][A-Za-z0-9_:.-]*\s*=\s*(?:"[^"]*"|\'[^\']*\'|\{[^{}]*\}))*'
    r'\s*/?>'
)
# 資源路徑（minecraft:stone、ae2/guide/index#anchor）。Minecraft 規格只允許小寫，
# 所以這裡不能加 IGNORECASE——否則「Copy/Paste」「Ticks/Operation」「Filter:Aerial」
# 這類大小寫混合的 GUI 標籤會被當成路徑跳過。
_STRUCTURAL_TEXT_RE = re.compile(r"^[a-z0-9_.-]+(?::|/)[a-z0-9_./-]+(?:#[a-z0-9_./-]+)?$")
# Bare RGB/ARGB hex color, optional leading '#': 3, 4, 6 or 8 hex digits.
_HEX_COLOR_RE = re.compile(r"#?(?:[0-9A-Fa-f]{8}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{4}|[0-9A-Fa-f]{3})")

_PREAMBLE = re.compile(
    r'^(以下是|翻譯如下|譯文：|Translation:|Here is|Here\'s)\s*'
)


def encode(text: str) -> tuple[str, list[str]]:
    tokens: list[str] = []

    def _replace(m: re.Match) -> str:
        idx = len(tokens)
        tokens.append(m.group(0))
        return f"{{{idx}}}"

    return _PLACEHOLDERS.sub(_replace, text), tokens


def decode(text: str, tokens: list[str]) -> str:
    def _restore(m: re.Match) -> str:
        idx = int(m.group(1))
        return tokens[idx] if idx < len(tokens) else m.group(0)

    return re.sub(r"\{(\d+)\}", _restore, text)


def strip_preamble(text: str) -> str:
    return _PREAMBLE.sub("", text).strip()


def _normalized_translation_value(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _has_translatable_text(value: str) -> bool:
    if _is_structural_text(value):
        return False
    if _is_untranslatable_value(value):
        return False
    return _requires_visible_translation(value)


_GENERIC_UNTRANSLATED_WORDS = {
    "any",
    "bottom",
    "button",
    "claim",
    "click",
    "display",
    "icon",
    "inventory",
    "left",
    "menu",
    "page",
    "player",
    "quest",
    "reward",
    "right",
    "screen",
    "slot",
    "slots",
    "task",
    "tasks",
    "time",
    "top",
    "visible",
}


def is_usable_translation(source: str, target: str) -> bool:
    """譯文能不能用。`rejection_reason` 的布林檢視——規則只有那一份。"""
    return rejection_reason(source, target) is None


def rejection_reason(source: str, target: str) -> str | None:
    """譯文被判不可用的原因；可用時回 None。

    有原因才查得出問題。單純回 False 的話，使用者回報「這條沒翻到」時，只能靠猜是
    模型不會翻、還是被哪條規則擋掉——兩者的處理方式完全不同。
    """
    # 引數數量最先查：這條跟「有沒有東西可翻」無關，純粹是譯文餵進 String.format
    # 會不會炸。原文是 "%s" 這種沒得翻的字串時更要查——模組出廠自帶的譯文一樣會壞
    # （farmingforblockheads 的 "%s" 就配著過期的 "%dx %s"）。
    src_arity, dst_arity = _format_arity(source), _format_arity(target)
    if dst_arity > src_arity:
        return f"格式引數變多（原文需要 {src_arity} 個、譯文用了 {dst_arity} 個）"
    if not _has_translatable_text(source):
        return None

    src = _normalized_translation_value(source)
    dst = _normalized_translation_value(target)
    if not dst:
        return "譯文是空的"
    needs_visible_translation = _requires_visible_translation(source)
    if dst == src:
        return "與原文完全相同（這條需要看得見的中文）" if needs_visible_translation else None
    missing = _missing_required_tokens(source, target)
    if missing:
        return "結構標記遺失：" + "、".join(repr(t) for t in missing[:4])
    if not _preserves_internal_newlines(source, target):
        return (f"換行被壓縮（原文 {source.strip().count(chr(10)) + 1} 行、"
                f"譯文 {target.strip().count(chr(10)) + 1} 行）")
    if needs_visible_translation and not _has_cjk_text(target):
        return "譯文裡沒有中文"
    leaked = _leaked_words(source, target)
    if leaked:
        return "英文殘留未譯：" + "、".join(sorted(leaked)[:4])
    return None


def _preserves_internal_newlines(source: str, target: str) -> bool:
    """譯文的內部換行不得比原文少。

    JSON/SNBT 解碼後的 \\n 是真換行（0x0A）；encode() 只 token 化字面的兩字元
    "\\n"，真換行沒有任何保護。模型整串翻譯時傾向把多行併成一長行，於是超出那些
    依英文行寬設計、又不會自動折行的固定框（FancyMenu、config tooltip、NPC 對話
    框）。判為不可用後，runner 會退回既有的逐行分段翻譯，分隔原樣保留。

    只擋「變少」：尾端空白增減與模型主動多折行都放行。
    """
    return target.strip().count("\n") >= source.strip().count("\n")


# %% 是字面百分號、%n 是換行，兩者都不取引數；其餘 %s %d %1$s 都要。
_FORMAT_ARG_RE = re.compile(r"%(?:(\d+)\$)?([A-Za-z])")


def _format_arity(source_or_target: str) -> int:
    """這串文字需要幾個格式化引數。

    引數給不夠，遊戲當場丟例外——Component.translatable 丟 TranslatableFormatException、
    模組自己呼叫的 String.format 丟 MissingFormatArgumentException——那一行文字直接
    變成紅字錯誤。_preserves_required_tokens 只查「原文的 token 有沒有留著」，查不出
    「譯文自己多長了一個 %s」，所以另外算一次需求量。

    只擋變多。變少只是有引數沒被用到，Java 會安靜忽略；譯文把兩個數字併成一句是
    常見且正確的中文寫法，不該擋。
    """
    sequential = 0
    highest = 0
    for index, conversion in _FORMAT_ARG_RE.findall(source_or_target.replace("%%", "")):
        if conversion in "nN":
            continue
        if index:                      # 位置引數 %2$s：需求量是最大的那個索引
            highest = max(highest, int(index))
        else:
            sequential += 1
    return max(highest, sequential)


def _looks_undertranslated(source: str, target: str) -> bool:
    return bool(_leaked_words(source, target))


def _leaked_words(source: str, target: str) -> set[str]:
    """譯文裡原封不動留著的常見英文詞——沒翻到的徵兆。

    兩道防誤判，缺一都會擋掉大量好譯文：

    1. **只認獨立單字。** `player_pos_x`、`player.jem`、`$$button`、`Inventory[0].id`
       裡的那個詞是識別字的一部分，本來就不該翻。
    2. **中文佔多數就不算沒翻好。** 譯文已經以中文為主時，殘存的通用英文詞幾乎都是
       參數名（`- player: 要列出分數的玩家名稱`）或專有名詞（`「Mod Menu」模組`）。
       真正沒翻好的長相很不一樣——整段英文原封不動，中文比例低到 0.1 以下。
    """
    if not _has_cjk_text(target):
        return set()
    if _cjk_ratio(target) >= _UNDERTRANSLATED_CJK_RATIO:
        return set()

    src_words = _free_english_words(source)
    target_words = _free_english_words(target)
    return src_words & target_words & _GENERIC_UNTRANSLATED_WORDS


# 誤判樣本的中文比例最低 0.17、中位 0.54；真沒翻好的最高 0.10。0.30 落在中間的空隙。
_UNDERTRANSLATED_CJK_RATIO = 0.30
_CJK_CHAR_RE = re.compile(r"[一-鿿]")
# 前後都不得鄰接識別字字元，否則 player_pos_x 會被拆出一個「player」。
# `: # /` 是 Minecraft 資源路徑的連接符（computercraft:inventory#pushItems）。
# `[` 只放在後方：Inventory[0].id 要排除，但 markdown 的 [player stats](…) 是散文，
# 前方的 `[` 不能拿來排除。
_FREE_WORD_RE = re.compile(r"(?<![A-Za-z0-9_.:#/$])[A-Za-z]{2,}(?![A-Za-z0-9_.:#/\[])")


def _cjk_ratio(text: str) -> float:
    """中文字佔「中文字 + 英文字母」的比例。結構 token 不列入計算。"""
    body = _PLACEHOLDERS.sub(" ", text)
    cjk = len(_CJK_CHAR_RE.findall(body))
    latin = len(re.findall(r"[A-Za-z]", body))
    return cjk / max(cjk + latin, 1)


def _free_english_words(text: str) -> set[str]:
    """獨立成詞的英文字；夾在識別字裡的不算。"""
    return {m.group(0).lower()
            for m in _FREE_WORD_RE.finditer(_PLACEHOLDERS.sub(" ", text))}


def _preserves_required_tokens(source: str, target: str) -> bool:
    return not _missing_required_tokens(source, target)


def _missing_required_tokens(source: str, target: str) -> list[str]:
    """譯文裡不見了的硬性結構標記。色碼是軟性的，掉了不算。"""
    _encoded, tokens = encode(source)
    return [
        token for token in tokens
        if not _is_soft_token(token) and token not in target
    ]


def _is_soft_token(token: str) -> bool:
    return bool(re.fullmatch(r"[&§][0-9A-FK-ORa-fk-or]", token))


# 技術縮寫詞彙表。有限且可窮舉——這正是它能當白名單的原因；反過來替「全大寫的
# 英文單字」列白名單則永遠列不完。四字母以上且含母音的條目特別重要，否則會被
# _is_stylized_allcaps 誤判成該翻的顯示文字（UUID、ASCII、YAML…）。
_TRANSLATION_OPTIONAL_WORDS = {
    "ae",
    "ansi",
    "api",
    "argb",
    "ascii",
    "cf",
    "emi",
    "eu",
    "fe",
    "forge",
    "ftb",
    "gui",
    "http",
    "https",
    "id",
    "ipv4",
    "ipv6",
    "jei",
    "jpeg",
    "json",
    "kubejs",
    "lvl",
    "midi",
    "minecraft",
    "millibuckets",
    "mo",
    "nbt",
    "neoforge",
    "oled",
    "patchouli",
    "pm",
    "p2p",
    "rei",
    "rf",
    "rgba",
    "rpm",
    "snbt",
    "su",
    "toml",
    "uefi",
    "url",
    "utf8",
    "uuid",
    "xp",
    "yaml",
}
_KEYBIND_WORDS = {
    "alt",
    "cmd",
    "command",
    "control",
    "ctrl",
    "delete",
    "enter",
    "escape",
    "f1",
    "f2",
    "f3",
    "f4",
    "f5",
    "f6",
    "f7",
    "f8",
    "f9",
    "f10",
    "f11",
    "f12",
    "meta",
    "mouse",
    "option",
    "r-click",
    "shift",
    "tab",
}
_UNIT_WORDS = {
    "bar",
    "cf",
    "eu",
    "fe",
    "mm",
    "mb",
    "ms",
    "rf",
    "rpm",
    "tick",
    "ticks",
    "tps",
    "us",
    "xp",
    "μs",
}
# Connectors that glue unit fragments together ("%s mB out of %s mB", "FE per EU").
# Only treated as untranslatable noise when every other word is a unit.
_UNIT_CONNECTOR_WORDS = {"of", "out", "per"}
_GRAMMAR_FRAGMENT_WORDS = {
    "a",
    "an",
    "are",
    "for",
    "has",
    "in",
    "is",
    "of",
    "that",
    "the",
    "to",
    "which",
}
_COPY_ONLY_VALUES = {
    "curseforge",
    "discord",
    "fabric",
    "github",
    "modrinth",
    "neoforge",
    "wiki",
    # Platform / format brand names that stay in English under zh_tw conventions.
    "java",
    "ko-fi",
    "kofi",
    "markdown",
    "mastodon",
    "patreon",
    "reddit",
    "twitter",
    "youtube",
}
_BRAND_WORDS = {
    "ae",
    "advanced",
    "apotheosis",
    "applied",
    "ars",
    "craftoria",
    "create",
    "crowdin",
    "energistics",
    "emi",
    "fabric",
    "immersive",
    "industrial",
    "industrialization",
    "modonomicon",
    "mekanism",
    "modrinth",
    "neoforge",
    "nouveau",
    "occultism",
    "patchouli",
    "pneumaticcraft",
    "powah",
}
_CODE_WORDS = {
    "boolean",
    "class",
    "double",
    "float",
    "int",
    "long",
    "private",
    "protected",
    "public",
    "return",
    "static",
    "string",
    "void",
}


def _has_cjk_text(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", value))


def _is_structural_text(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if text.startswith(("{", "[")):
        try:
            return isinstance(json.loads(text), (dict, list))
        except json.JSONDecodeError:
            pass
    if _STRUCTURAL_TEXT_RE.fullmatch(text):
        return True
    if text.startswith(("{", "[", "#")) and not re.search(r"\s", text):
        return True
    return False


def _is_translatable_entry(key: str, value: str) -> bool:
    if classify_translation_entry(key, value) != "translate":
        return False
    return True


def classify_translation_entry(key: str, value: str) -> str:
    """Classify a lang value as translate/copy/skip without changing file formats."""
    if not _has_translatable_text(value):
        return "skip"

    lowered_key = key.lower()
    if _is_metadata_key(lowered_key):
        return "copy"
    if lowered_key.endswith(".advancement.title.root") and _value_slug_in_key(lowered_key, value):
        return "copy"
    if _is_keybind_key(lowered_key) and _is_keybind_or_shortcut(value):
        return "copy"
    if _is_credit_key(lowered_key) and _looks_like_credit_value(_normalized_translation_value(value)):
        return "copy"
    if _is_copy_only_key_value(lowered_key, value):
        return "copy"
    return "translate"


def _is_metadata_key(key: str) -> bool:
    if key.endswith(".author") or ".author." in key:
        return True
    if "painting." in key and key.endswith(".author"):
        return True
    if "music_disc" in key and key.endswith((".desc", ".description")):
        return True
    if key.startswith(("itemgroup.", "key.category.")):
        return True
    if key.startswith("category.") and key.endswith(".keybinding"):
        return True
    if key.startswith("__comment"):
        return True
    return False


def _is_keybind_key(key: str) -> bool:
    return any(part in key for part in ("keybind", "keyboard", "shortcut", ".key_", "modifier."))


def _is_copy_only_key_value(key: str, value: str) -> bool:
    text = _normalized_translation_value(value)
    lowered = text.lower()
    if lowered in _COPY_ONLY_VALUES:
        return True
    if key.startswith("mod_menu.") and (
        ".badge." in key
        or key.endswith((
            ".crowdin", ".modrinth", ".discord", ".github", ".wiki",
            ".kofi", ".patreon", ".reddit", ".twitter", ".mastodon",
            ".youtube", ".curseforge",
        ))
    ):
        return True
    if key.endswith((".docs", ".discord", ".github", ".modrinth", ".wiki")):
        return True
    if key.endswith((".color", ".colour")) and _HEX_COLOR_RE.fullmatch(text):
        return True
    if ".configuration." in key and key.endswith((".title", ".toml.title")) and _looks_like_config_title(text):
        return True
    if key.startswith(("chapter.", "chapter_group.")) and key.endswith(".title") and _looks_like_brand_name(text):
        return True
    if text.lower().startswith("the ") and _value_slug_in_key(key, text[4:]):
        return True
    return False


def _value_slug_in_key(key: str, value: str) -> bool:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if not slug or len(slug) < 4:
        return False
    return slug in re.sub(r"[^a-z0-9]+", "_", key)


def _is_untranslatable_value(value: str) -> bool:
    text = _normalized_translation_value(value)
    if not text:
        return True
    if _LOCALIZATION_KEY_RE.fullmatch(text):
        return True
    if _RESOURCE_LOCATION_RE.fullmatch(text):
        return True
    if _is_url_or_domain(text):
        return True
    if _is_hex_color(text):
        return True
    if _is_placeholder_or_unit_fragment(text):
        return True
    if _is_short_grammar_fragment(text):
        return True
    if _is_keybind_or_shortcut(text):
        return True
    if _looks_like_code_or_table_line(text):
        return True
    return False


def _is_url_or_domain(text: str) -> bool:
    if re.fullmatch(r"[a-z][a-z0-9+.-]*://\S+", text, re.IGNORECASE):
        return True
    return bool(re.fullmatch(r"(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/\S*)?", text, re.IGNORECASE))


def _is_hex_color(text: str) -> bool:
    """A bare hex color value (dde9f4, #1a2b3c) is markup, never prose.

    Used across every Minecraft version for text/title colors (e.g. Traveler's
    Titles `.color` keys). A digit is required so all-letter words that happen to
    be valid hex (facade, decade, beaded, cafe) stay translatable.
    """
    if not _HEX_COLOR_RE.fullmatch(text):
        return False
    return any(ch.isdigit() for ch in text)


def _is_placeholder_or_unit_fragment(text: str) -> bool:
    stripped = _PLACEHOLDERS.sub(" ", text)
    stripped = re.sub(r"[<>=~+\-–—/:|(),.%\s\d]+", " ", stripped)
    words = [word.lower() for word in re.findall(r"[A-Za-zμ]+", stripped)]
    if not words or not any(word in _UNIT_WORDS for word in words):
        return False
    return all(word in _UNIT_WORDS or word in _UNIT_CONNECTOR_WORDS for word in words)


def _is_short_grammar_fragment(text: str) -> bool:
    if not re.search(r"%\d*\$?[sdifcbxo]|%[sdifcbxo]", text):
        return False
    stripped = _PLACEHOLDERS.sub(" ", text)
    words = [word.lower() for word in re.findall(r"[A-Za-z]+", stripped)]
    return bool(words) and len(words) <= 4 and all(word in _GRAMMAR_FRAGMENT_WORDS for word in words)


def _is_keybind_or_shortcut(text: str) -> bool:
    simplified = re.sub(r"[_+/,|()-]+", " ", text.lower())
    words = re.findall(r"[a-z0-9-]+", simplified)
    if not words:
        return False
    if all(word in _KEYBIND_WORDS or re.fullmatch(r"[a-z0-9]", word) for word in words):
        return True
    return False


# 「作者 - 作品」署名只出現在唱片/畫作/署名這幾種鍵底下。單看值形（含 " - "
# 且左半以大寫開頭）會把「Not enough LP - Sigil deactivated」這種遊戲訊息一起
# 殺掉——實測單一模組包就有 2,700 多條。因此改為鍵語境閘門：先確認鍵屬於署名
# 語境，再驗證值確實是「短名字 - 短標題」。
_CREDIT_KEY_HINTS = (
    "jukebox_song",
    "music_disc",
    "musicdisc",
    "painting.",
    "record.",
)


def _is_credit_key(key: str) -> bool:
    if key.endswith(".author") or ".author." in key:
        return True
    return any(hint in key for hint in _CREDIT_KEY_HINTS)


def _looks_like_credit_value(text: str) -> bool:
    """「Renren - Flash」式署名：左右都是短標題，兩側都不是句子。"""
    if " - " not in text:
        return False
    left, _, right = text.partition(" - ")
    left, right = left.strip(), right.strip()
    if not left or not right:
        return False
    if not re.fullmatch(r"[A-Z][A-Za-z0-9' ._-]+", left):
        return False
    # 句子而非標題：帶句末標點，或右半長得像整句話。
    if re.search(r"[.!?,;:]", right):
        return False
    return len(right.split()) <= 4 and len(left.split()) <= 4


def _looks_like_config_title(text: str) -> bool:
    return bool(re.search(r"\b(?:config|configuration|toml)\b", text, re.IGNORECASE))


def _looks_like_brand_name(text: str) -> bool:
    words = re.findall(r"[A-Za-z][A-Za-z0-9+-]*", text)
    if not words or len(words) > 4:
        return False
    lowered = {word.lower() for word in words}
    return bool(lowered & _BRAND_WORDS)


_CODE_WORD_RE = re.compile(r"\b(?:%s)\b" % "|".join(_CODE_WORDS), re.IGNORECASE)
# 真正的程式碼語法。單看標點（原本是任一個 = ( ) ; { }）會把
# 「Not a valid number! (Long)」這種玩家可見訊息判成程式碼——括號前有空白就
# 不是呼叫，型別註記不是語法。要求識別字緊貼括號、敘述結尾、或運算子。
_CODE_SYNTAX_RE = re.compile(
    r"[;{}]\s*$"          # 敘述/區塊結尾
    r"|\w\("              # 呼叫：識別字緊貼左括號
    r"|::|->|=>"          # 範圍/箭頭運算子
    r"|\w\s*=\s*\w"       # 指派
)


def _looks_like_code_or_table_line(text: str) -> bool:
    plain = _PLACEHOLDERS.sub(" ", text).strip()
    plain = re.sub(r"^[&§][0-9A-FK-ORa-fk-or]\s*", "", plain)
    if re.fullmatch(r"(?:[-+*]\s*)?Tier\s+\d+\s*(?:[-=]*>|,)\s*\d+:\d+", plain, re.IGNORECASE):
        return True
    if re.fullmatch(r"(?:[-+*]\s*)?(?:public|private|protected)\s+[\w<>\[\]]+\s+\w+\s*\(.*", plain):
        return True
    if not _CODE_SYNTAX_RE.search(plain):
        return False
    return bool(_CODE_WORD_RE.search(plain))


def _requires_visible_translation(source: str) -> bool:
    text = _PLACEHOLDERS.sub(" ", source)
    text = re.sub(r"[a-z][a-z0-9+.-]*://\S+", " ", text, flags=re.IGNORECASE)
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", text)
    if any(_is_translation_required_word(word) for word in words):
        return True
    return _is_stylized_allcaps(words)


_ROMAN_NUMERAL_RE = re.compile(r"[IVXLCDM]+")


def _is_stylized_allcaps(words: list[str]) -> bool:
    """整串全大寫時，判斷它是樣式化的顯示文字還是技術縮寫。

    逐字看「有沒有小寫字母」會把 DOWNED（倒地）、INBOUND（輸入）、ANY ITEM 這類
    玩家看得到的文字判成不用翻。改看整串形狀，並把舉證責任反轉：技術縮寫是有限
    集合（可窮舉成詞彙表），全大寫的英文單字則是無限集合，不可能列白名單。
    """
    if not words or not all(word.isupper() for word in words):
        return False

    meaningful = [
        word for word in words
        if len(word) >= 2                            # 「P2P」拆出的 P、座標的 N/E 不算詞
        and word.lower() not in _TRANSLATION_OPTIONAL_WORDS
        and not _ROMAN_NUMERAL_RE.fullmatch(word)    # 附魔等級 XIII、XVII
    ]
    if not meaningful:
        return False
    if len(meaningful) > 1:
        return True     # 全大寫片語（ANY ITEM、HOT! DO NOT EAT）必然是顯示文字
    word = meaningful[0]
    return len(word) >= 4 and bool(re.search(r"[AEIOU]", word))


def _is_translation_required_word(word: str) -> bool:
    normalized = word.strip("'-")
    if len(normalized) < 2:
        return False
    if normalized.lower() in _TRANSLATION_OPTIONAL_WORDS:
        return False
    if normalized.isupper():
        return False
    if re.fullmatch(r"[A-Z0-9]+s?", normalized):
        return False
    if re.search(r"[a-z][A-Z]", normalized):
        return False
    return bool(re.search(r"[a-z]", normalized))


def _english_words(value: str) -> set[str]:
    return {m.group(0).lower() for m in re.finditer(r"[A-Za-z]{2,}", value)}


def diff_keys(en_dict: dict[str, str], zh_dict: dict[str, str]) -> set[str]:
    """Return keys that are missing from zh or still identical to en."""
    translatable_keys = {
        k for k, value in en_dict.items()
        if _is_translatable_entry(k, value)
    }
    missing = translatable_keys - set(zh_dict)
    untranslated = {
        k
        for k in translatable_keys
        if k in zh_dict
        and not is_usable_translation(en_dict[k], zh_dict[k])
    }
    return missing | untranslated | _unsafe_keys(en_dict, zh_dict)


def _unsafe_keys(en_dict: dict[str, str], zh_dict: dict[str, str]) -> set[str]:
    """既有譯文的格式引數比原文多——遊戲跑到就丟例外，必須重寫。

    這一關不能併進上面的 translatable_keys，因為那份集合先問「這條該不該翻」。
    原文是 "%s" 這種沒東西可翻的字串會被排除在外，可是模組出廠自帶的譯文照樣可能
    是壞的（farmingforblockheads 的 "%s" 配著過期的 "%dx %s"），而且正因為沒被列入
    重翻範圍，它會一路存活下來。結構安全與「該不該翻」無關。
    """
    return {
        k for k, translated in zh_dict.items()
        if isinstance(translated, str)
        and isinstance(en_dict.get(k), str)
        and _format_arity(translated) > _format_arity(en_dict[k])
    }


# ------------------------------------------------------------------ readers

_JSON_LINE_COMMENT_RE = re.compile(r"(^|\s)//[^\n]*")
_JSON_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_JSON_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def parse_json_lang(raw: str) -> dict[str, str]:
    """解析語言 JSON，並容忍遊戲讀得動、`json.loads` 卻拒收的寫法。

    Minecraft 用 GSON 的寬鬆模式讀語言檔，所以 `//` 註解、區塊註解與尾逗號在遊戲
    裡都能正常載入。嚴格解析失敗等於整個檔案靜默跳過、該模組全程英文——寧可多花
    一次寬鬆重試，也不要無聲漏翻。
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(_relax_json(raw))
    if not isinstance(data, dict):
        raise json.JSONDecodeError("語言檔不是 JSON 物件", raw[:80], 0)
    return {k: v for k, v in data.items() if isinstance(v, str)}


def _relax_json(raw: str) -> str:
    """移除註解與尾逗號。字串字面值內的內容原樣保留。"""
    pieces: list[str] = []
    last = 0
    for match in re.finditer(r'"(?:[^"\\]|\\.)*"', raw):
        pieces.append(_strip_json_comments(raw[last:match.start()]))
        pieces.append(match.group(0))
        last = match.end()
    pieces.append(_strip_json_comments(raw[last:]))
    return _JSON_TRAILING_COMMA_RE.sub(r"\1", "".join(pieces))


def _strip_json_comments(chunk: str) -> str:
    chunk = _JSON_BLOCK_COMMENT_RE.sub(" ", chunk)
    return _JSON_LINE_COMMENT_RE.sub(r"\1", chunk)


def parse_legacy_lang(raw: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value
    return result


def parse_snbt_lang(raw: str) -> dict[str, str]:
    try:
        data = json.loads(raw)
        result: dict[str, str] = {}
        for key, value in data.items():
            _append_snbt_lang_value(result, key, value)
        return result
    except json.JSONDecodeError:
        pass

    result: dict[str, str] = {}
    key_re = re.compile(r'^\s*(?:"((?:[^"\\]|\\.)*)"|([\w.\-/]+))\s*:', re.MULTILINE)
    consumed_spans: list[tuple[int, int]] = []
    for m in key_re.finditer(raw):
        if any(start <= m.start() < end for start, end in consumed_spans):
            continue

        key = _json_unescape(m.group(1) if m.group(1) is not None else m.group(2))
        pos = m.end()
        while pos < len(raw) and raw[pos].isspace():
            pos += 1
        if pos >= len(raw):
            continue

        if raw[pos] == "[":
            array_raw, end = _read_balanced_snbt_value(raw, pos)
            consumed_spans.append((m.start(), end))
            body = array_raw[1:-1]
            for idx, item in enumerate(_parse_snbt_array_items(body)):
                result[f"{key}[{idx}]"] = item
            continue

        if raw[pos] == '"':
            value, end = _read_snbt_quoted_string(raw, pos)
            consumed_spans.append((m.start(), end))
            result[key] = value
    return result


def _append_snbt_lang_value(result: dict[str, str], key: str, value: Any) -> None:
    if isinstance(value, str):
        result[key] = value
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            if isinstance(item, str):
                result[f"{key}[{idx}]"] = item


def format_snbt_lang(values: dict[str, str]) -> str:
    lines = ["{"]
    emitted_arrays: set[str] = set()
    for key, value in values.items():
        array_key = _split_snbt_array_entry_key(key)
        if array_key is not None:
            base_key, _idx = array_key
            if base_key in emitted_arrays:
                continue
            emitted_arrays.add(base_key)
            lines.append(f"\t{_snbt_key(base_key)}: [")
            for item in _snbt_array_items(values, base_key):
                lines.append(f"\t\t{_snbt_string(item)}")
            lines.append("\t]")
            continue
        lines.append(f"\t{_snbt_key(key)}: {_snbt_string(value)}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _split_snbt_array_entry_key(key: str) -> tuple[str, int] | None:
    m = re.fullmatch(r"(.+)\[(\d+)\]", key)
    if not m:
        return None
    return m.group(1), int(m.group(2))


def _snbt_array_items(values: dict[str, str], base_key: str) -> list[str]:
    items: list[tuple[int, str]] = []
    for key, value in values.items():
        array_key = _split_snbt_array_entry_key(key)
        if array_key is not None and array_key[0] == base_key:
            items.append((array_key[1], value))
    return [value for _idx, value in sorted(items)]


def _snbt_key(key: str) -> str:
    if re.fullmatch(r"[\w.\-/]+", key):
        return key
    return _snbt_string(key)


def _snbt_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_unescape(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value.replace('\\"', '"')


def read_jar_text(source_file: Path, path_in_jar: str) -> str:
    with zipfile.ZipFile(source_file) as zf:
        return zf.read(path_in_jar).decode("utf-8-sig")


def jar_member_exists(source_file: Path, path_in_jar: str) -> bool:
    with zipfile.ZipFile(source_file) as zf:
        return path_in_jar in zf.namelist()


def read_json_lang(source_file: Path, path_in_jar: str | None) -> dict[str, str]:
    if path_in_jar:
        raw = read_jar_text(source_file, path_in_jar)
    else:
        raw = source_file.read_text(encoding="utf-8")
    return parse_json_lang(raw)


def read_legacy_lang(source_file: Path, path_in_jar: str | None) -> dict[str, str]:
    if path_in_jar:
        raw = read_jar_text(source_file, path_in_jar)
    else:
        raw = source_file.read_text(encoding="utf-8")
    return parse_legacy_lang(raw)


def read_snbt_lang(source_file: Path) -> dict[str, str]:
    """Parse FTB Quests / Heracles SNBT lang file.

    Format uses unquoted keys and quoted values, one per line:
        chapter.016D52CB8F1295E5.title: " &eNew Age"
        quest.001201DAFCC3FAEC.title: "Drink Mayonnaise"
    """
    return parse_snbt_lang(source_file.read_text(encoding="utf-8"))

    raw = source_file.read_text(encoding="utf-8")

    # Try standard JSON first (Heracles or future formats may use it)
    try:
        data = json.loads(raw)
        return {k: v for k, v in data.items() if isinstance(v, str)}
    except json.JSONDecodeError:
        pass

    # FTB Quests SNBT: unquoted key, colon, quoted value
    # key chars: word chars, dots, hyphens — no whitespace
    result: dict[str, str] = {}
    for m in re.finditer(
        r'^\s*([\w.\-]+)\s*:\s*"((?:[^"\\]|\\.)*)"',
        raw,
        re.MULTILINE,
    ):
        result[m.group(1)] = m.group(2)
    return result


def read_bq_lang(source_file: Path) -> dict[str, str]:
    """Parse legacy Better Questing .lang format (key=value per line)."""
    return parse_legacy_lang(source_file.read_text(encoding="utf-8"))


def read_patchouli_page(source_file: Path, path_in_jar: str) -> dict[str, Any]:
    with zipfile.ZipFile(source_file) as zf:
        return json.loads(zf.read(path_in_jar).decode("utf-8-sig"))


PATCHOULI_TEXT_FIELDS = ("text", "title", "header", "name")
PATCHOULI_VISIBLE_TEXT_FIELDS = (
    "text",
    "title",
    "header",
    "name",
    "description",
    "link_text",
)
_PATCHOULI_STRUCTURAL_FIELDS = {
    "advancement",
    "anchor",
    "category",
    "entity",
    "extra_recipe_mappings",
    "flag",
    "icon",
    "images",
    "ingredient",
    "ingredients",
    "item",
    "items",
    "multiblock",
    "multiblock_id",
    "parent",
    "recipe",
    "recipe2",
    "tag",
    "trigger",
    "turnin",
    "type",
    "url",
}
_PATCHOULI_TEXT_SUFFIXES = (
    "_text",
    "_title",
    "_header",
    "_description",
    "_label",
)
_RESOURCE_LOCATION_RE = _STRUCTURAL_TEXT_RE   # 同一個東西，別讓兩份定義各自漂移
_LOCALIZATION_KEY_RE = re.compile(r"^[a-z0-9_-]+(?:\.[a-z0-9_-]+)+$", re.IGNORECASE)
_JSON_PATH_PART_RE = re.compile(
    r"\.([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]|\[(\"(?:[^\"\\]|\\.)*\")\]"
)


def read_patchouli_text(data: Any) -> dict[str, str]:
    """Extract player-visible Patchouli strings as stable JSON-path keys."""
    result: dict[str, str] = {}
    for path, value in _iter_patchouli_text(data):
        result[_patchouli_path_key(path)] = value
    return result


def _parse_snbt_array_items(body: str) -> list[str]:
    items: list[str] = []
    pos = 0
    while pos < len(body):
        while pos < len(body) and (body[pos].isspace() or body[pos] == ","):
            pos += 1
        if pos >= len(body):
            break

        char = body[pos]
        if char == '"':
            value, pos = _read_snbt_quoted_string(body, pos)
            items.append(value)
            continue
        if char in "{[":
            value, pos = _read_balanced_snbt_value(body, pos)
            items.append(value.strip())
            continue

        start = pos
        while pos < len(body) and body[pos] not in ",\r\n":
            pos += 1
        value = body[start:pos].strip()
        if value:
            items.append(value)
    return items


def _read_snbt_quoted_string(value: str, start: int) -> tuple[str, int]:
    pos = start + 1
    escaped = False
    while pos < len(value):
        char = value[pos]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            return _json_unescape(value[start + 1:pos]), pos + 1
        pos += 1
    return _json_unescape(value[start + 1:]), len(value)


def _read_balanced_snbt_value(value: str, start: int) -> tuple[str, int]:
    opening = value[start]
    closing = "}" if opening == "{" else "]"
    stack = [closing]
    pos = start + 1
    in_string = False
    escaped = False
    while pos < len(value):
        char = value[pos]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif stack and char == stack[-1]:
            stack.pop()
            if not stack:
                return value[start:pos + 1], pos + 1
        pos += 1
    return value[start:], len(value)


def write_patchouli_text(data: Any, path_key: str, value: str) -> None:
    path = _parse_patchouli_path_key(path_key)
    cursor = data
    for part in path[:-1]:
        cursor = cursor[part]
    cursor[path[-1]] = value


def _iter_patchouli_text(data: Any, path: tuple[str | int, ...] = ()):
    if isinstance(data, dict):
        for key, value in data.items():
            child_path = path + (key,)
            if isinstance(value, str):
                if _is_patchouli_text_field(key) and _is_patchouli_visible_text_value(value):
                    yield child_path, value
            elif isinstance(value, (dict, list)):
                yield from _iter_patchouli_text(value, child_path)
    elif isinstance(data, list):
        for idx, value in enumerate(data):
            child_path = path + (idx,)
            if isinstance(value, str):
                if path and path[-1] == "pages" and _is_patchouli_visible_text_value(value):
                    yield child_path, value
            elif isinstance(value, (dict, list)):
                yield from _iter_patchouli_text(value, child_path)


def _is_patchouli_text_field(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    if lowered in _PATCHOULI_STRUCTURAL_FIELDS:
        return False
    return lowered in PATCHOULI_VISIBLE_TEXT_FIELDS or lowered.endswith(_PATCHOULI_TEXT_SUFFIXES)


def _is_patchouli_visible_text_value(value: str) -> bool:
    text = value.strip()
    if len(text) < 2:
        return False
    if text.startswith(("#", "{", "[")):
        return False
    if re.fullmatch(r"[a-z][a-z0-9+.-]*://\S+", text, re.IGNORECASE):
        return False
    if _RESOURCE_LOCATION_RE.fullmatch(text):
        return False
    if _LOCALIZATION_KEY_RE.fullmatch(text):
        return False
    return True


def _patchouli_path_key(path: tuple[str | int, ...]) -> str:
    if len(path) == 1 and isinstance(path[0], str):
        return path[0]

    result = "$"
    for part in path:
        if isinstance(part, int):
            result += f"[{part}]"
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", part):
            result += f".{part}"
        else:
            result += f"[{json.dumps(part)}]"
    return result


def _parse_patchouli_path_key(path_key: str) -> tuple[str | int, ...]:
    if not path_key.startswith("$"):
        return (path_key,)

    path: list[str | int] = []
    pos = 1
    while pos < len(path_key):
        match = _JSON_PATH_PART_RE.match(path_key, pos)
        if not match:
            raise ValueError(f"Invalid Patchouli path: {path_key}")
        if match.group(1) is not None:
            path.append(match.group(1))
        elif match.group(2) is not None:
            path.append(int(match.group(2)))
        else:
            path.append(json.loads(match.group(3)))
        pos = match.end()
    return tuple(path)


INLINE_SNBT_TEXT_FIELDS = ("title", "subtitle", "description", "text", "hover", "name")
_INLINE_FIELD_RE = re.compile(
    r'(?P<prefix>\b(?P<field>title|subtitle|description|text|hover|name)\s*:\s*)"(?P<value>(?:[^"\\]|\\.)*)"',
    re.IGNORECASE,
)
_INLINE_ARRAY_FIELD_RE = re.compile(
    r'\b(?P<field>title|subtitle|description|text|hover|name)\s*:\s*\[(?P<body>.*?)\]',
    re.IGNORECASE | re.DOTALL,
)
_STRING_LITERAL_RE = re.compile(r'"(?P<value>(?:[^"\\]|\\.)*)"')


def read_inline_snbt_text(source_file: Path) -> dict[str, str]:
    raw = source_file.read_text(encoding="utf-8")
    result: dict[str, str] = {}
    for idx, (field, _start, _end, value) in enumerate(_iter_inline_snbt_text_matches(raw)):
        if _is_translatable_inline_text(value):
            result[f"{idx}:{field}"] = value
    return result


def replace_inline_snbt_text(raw: str, translations: dict[str, str]) -> str:
    pieces: list[str] = []
    last = 0

    for idx, (field, start, end, _value) in enumerate(_iter_inline_snbt_text_matches(raw)):
        key = f"{idx}:{field}"
        if key not in translations:
            continue

        pieces.append(raw[last:start])
        pieces.append(_json_escape(translations[key]))
        last = end

    if not pieces:
        return raw

    pieces.append(raw[last:])
    return "".join(pieces)


def _iter_inline_snbt_text_matches(raw: str) -> list[tuple[str, int, int, str]]:
    matches: list[tuple[str, int, int, str]] = []

    for m in _INLINE_FIELD_RE.finditer(raw):
        matches.append((
            m.group("field").lower(),
            m.start("value"),
            m.end("value"),
            _json_unescape(m.group("value")),
        ))

    for array_match in _INLINE_ARRAY_FIELD_RE.finditer(raw):
        body = array_match.group("body")
        offset = array_match.start("body")
        field = array_match.group("field").lower()
        for string_match in _STRING_LITERAL_RE.finditer(body):
            matches.append((
                field,
                offset + string_match.start("value"),
                offset + string_match.end("value"),
                _json_unescape(string_match.group("value")),
            ))

    matches.sort(key=lambda item: item[1])
    return matches


def _json_escape(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)[1:-1]


def _is_translatable_inline_text(value: str) -> bool:
    text = value.strip()
    if len(text) < 2:
        return False
    if re.fullmatch(r"[a-z0-9_.:/#\-]+", text, re.IGNORECASE):
        return False
    if text.startswith(("{", "[", "$(", "#")):
        return False
    if "://" in text:
        return False
    if re.search(r"[\u3400-\u9fff]", text):
        return False
    return bool(re.search(r"[A-Za-z]", text))
