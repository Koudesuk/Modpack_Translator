"""翻譯失敗項目的手動補譯對話框。

翻譯結束後，把沒能自動翻好的字串直接攤在使用者面前逐條補譯，套用後由程式寫回
模組包——使用者不必自己去 jar 或設定檔裡找那一行在哪。

留空 = 保留原文。補好的譯文會存進手動譯文表，下次翻譯時優先套用，不會被自動流程
覆蓋掉。

項目一多，逐條打字就不是辦法，所以右上角另外給了匯出／匯入：匯出成 JSON 交給
線上大模型翻，翻完再匯回來。匯入只把譯文填進表格，模組包一個位元組都不會動——
要不要採用，仍然由按下「套用」的人決定。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from modpack_translator.gui.theme import active_palette
from modpack_translator.pipeline.preprocessor import rejection_reason
from modpack_translator.version import APP_VERSION

_READ_ONLY_FLAGS = Qt.ItemIsEnabled | Qt.ItemIsSelectable

_ZH_COLUMN = 3                      # 「繁中譯文」欄，唯一可編輯的一欄

_EXPORT_FORMAT = "modpack-translator-failed-items"
_EXPORT_VERSION = 1

# 隨匯出檔一起交給線上大模型的規則。翻譯本身不難，難的是它會順手改壞佔位符與結構。
_LLM_INSTRUCTIONS = [
    "這是 Minecraft 模組包裡沒能自動翻好的字串。"
    "請把每一筆的 en_us 翻成繁體中文（台灣用語），填進同一筆的 zh_tw。",
    "id、source、key、en_us 四個欄位一律原樣保留；items 的筆數與順序也不要更動。",
    "佔位符與控制碼必須照原樣保留，數量、順序、大小寫都不能改："
    "%s、%1$s、{0}、\\n、§a 之類的顏色碼、$(...)、$()、[文字](連結)、<tag>、${...}。",
    "原文有幾行，zh_tw 就要有幾行，換行一行都不能少，也不要把多行併成一行。"
    "FancyMenu、設定說明這類方框不會自動折行，換行被壓掉就會爆版。",
    "專有名詞沿用 Minecraft 繁體中文（台灣）官方譯名；模組自創詞沒有慣用譯法時保留英文。",
    "純代碼、檔名、單純數字這類不該翻的，把 zh_tw 留成空字串，程式會保留英文原文。",
    "最後回覆完整且合法的 JSON，不要在 JSON 之外加說明文字。",
]


class _Entry(NamedTuple):
    """匯入檔裡的一筆譯文，已經正規化成固定欄位。"""

    row_id: int | None
    label: str
    key: str
    en_us: str
    text: str


class FailedItemsDialog(QDialog):
    """rows 為 (來源標籤, 鍵, 原文)；translations() 回傳 {列索引: 譯文}。

    initial 是先前補過的譯文 {列索引: 譯文}，重新開啟時填回去，使用者才能接著改。
    export_dir 是匯出／匯入檔案的預設資料夾。
    """

    def __init__(
        self,
        rows: list[tuple[str, str, str]],
        parent=None,
        initial: dict[int, str] | None = None,
        export_dir: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._rows = rows
        self._export_dir = Path(export_dir) if export_dir else None
        initial = initial or {}
        self.setWindowTitle("翻譯失敗項目")
        self.setMinimumSize(900, 560)

        # 匯入進來、但沒通過程式自己那套品質閘的列 → 原因。標色、提示、篩選都看它。
        self._flagged: dict[int, str] = {}

        # 匯入時把檔案裡的條目對回列號用的索引。由嚴到寬三層，先精確比對再放寬。
        self._by_label_key: dict[tuple[str, str], list[int]] = {}
        self._by_key: dict[str, list[int]] = {}
        self._by_source: dict[str, list[int]] = {}
        for index, (label, key, source) in enumerate(rows):
            self._by_label_key.setdefault((label, key), []).append(index)
            self._by_key.setdefault(key, []).append(index)
            self._by_source.setdefault(source, []).append(index)

        layout = QVBoxLayout(self)

        intro = QLabel(
            f"以下 {len(rows):,} 條字串沒能自動翻好，可以在此手動補上。\n"
            "在「繁中譯文」欄輸入譯文後按「套用」，程式會直接寫回模組包；\n"
            "留空的項目保留英文原文。補上的譯文會被記住，下次翻譯不會被蓋掉。"
        )

        self.export_btn = QPushButton("匯出失敗項目")
        self.export_btn.setToolTip(
            "把整份失敗清單存成 JSON，交給線上大模型（GPT、Claude、Grok、Gemini 等）批次翻譯。\n"
            "檔案裡已附上保留佔位符與格式的指示，模型只需要填每一筆的 zh_tw 欄位。"
        )
        self.export_btn.clicked.connect(self._export_items)

        self.import_btn = QPushButton("匯入翻譯完成之失敗項目")
        self.import_btn.setToolTip(
            "讀回線上大模型翻好的 JSON，把譯文填進下面的表格。\n"
            "匯入不會動到模組包；確認過（必要時直接改）之後按「套用」才會寫回去。"
        )
        self.import_btn.clicked.connect(self._import_items)

        header_row = QHBoxLayout()
        header_row.addWidget(intro)
        header_row.addStretch()
        header_row.addWidget(self.export_btn, 0, Qt.AlignTop)
        header_row.addWidget(self.import_btn, 0, Qt.AlignTop)
        layout.addLayout(header_row)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("搜尋："))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("輸入關鍵字過濾（來源、鍵、原文）")
        self.filter_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter_edit)

        # 匯入完才有意義：一百多列裡挑出那幾條要看的，靠眼睛掃不切實際。
        self.review_only_chk = QCheckBox("只顯示需確認的項目")
        self.review_only_chk.setVisible(False)
        self.review_only_chk.toggled.connect(
            lambda _checked: self._apply_filter(self.filter_edit.text())
        )
        filter_row.addWidget(self.review_only_chk)
        layout.addLayout(filter_row)

        self.table = QTableWidget(len(rows), 4, self)
        self.table.setHorizontalHeaderLabels(["來源", "鍵", "原文", "繁中譯文"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 190)
        self.table.setColumnWidth(1, 190)
        self.table.setColumnWidth(3, 230)

        for index, (label, key, source) in enumerate(rows):
            for column, text in enumerate((label, key, source)):
                item = QTableWidgetItem(text)
                item.setFlags(_READ_ONLY_FLAGS)
                item.setToolTip(text)
                self.table.setItem(index, column, item)
            self.table.setItem(index, _ZH_COLUMN, QTableWidgetItem(initial.get(index, "")))
        layout.addWidget(self.table)

        self.status_label = QLabel()
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)

        buttons = QDialogButtonBox(self)
        self._apply_btn = buttons.addButton("套用", QDialogButtonBox.AcceptRole)
        buttons.addButton("全部保留原文", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def translations(self) -> dict[int, str]:
        """使用者實際填了字的列。"""
        filled: dict[int, str] = {}
        for index in range(self.table.rowCount()):
            text = self._cell_text(index)
            if text:
                filled[index] = text
        return filled

    def _apply_filter(self, needle: str) -> None:
        needle = needle.strip().lower()
        review_only = self.review_only_chk.isChecked()
        for index, (label, key, source) in enumerate(self._rows):
            haystack = f"{label}\n{key}\n{source}".lower()
            hidden = (bool(needle) and needle not in haystack) or (
                review_only and index not in self._flagged
            )
            self.table.setRowHidden(index, hidden)

    def _cell_text(self, index: int) -> str:
        item = self.table.item(index, _ZH_COLUMN)
        return item.text().strip() if item is not None else ""

    # ------------------------------------------------------------- 匯出 / 匯入

    def _export_items(self) -> None:
        """把整份失敗清單存成 JSON，交給線上大模型批次翻。"""
        default_dir = self._export_dir or Path.cwd()
        try:
            default_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            default_dir = Path.cwd()
        suggested = default_dir / f"failed_items_{datetime.now():%Y%m%d_%H%M}.json"

        path, _ = QFileDialog.getSaveFileName(
            self, "匯出失敗項目", str(suggested), "JSON 檔案 (*.json)"
        )
        if not path:
            return

        payload = {
            "format": _EXPORT_FORMAT,
            "version": _EXPORT_VERSION,
            "app_version": APP_VERSION,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "instructions": _LLM_INSTRUCTIONS,
            "items": [
                {
                    "id": index,
                    "source": label,
                    "key": key,
                    "en_us": source,
                    "zh_tw": self._cell_text(index),
                }
                for index, (label, key, source) in enumerate(self._rows)
            ],
        }
        try:
            Path(path).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            QMessageBox.warning(self, "匯出失敗", f"無法寫入 {path}：\n{exc}")
            return

        QMessageBox.information(
            self,
            "已匯出",
            f"已匯出 {len(self._rows):,} 條失敗項目：\n{path}\n\n"
            "把整個檔案交給線上大模型（GPT、Claude、Grok、Gemini 等），\n"
            "請它只填每一筆的 zh_tw 欄位、其餘欄位不要動，\n"
            "再用「匯入翻譯完成之失敗項目」把結果讀回來。",
        )

    def _import_items(self) -> None:
        """讀回翻好的 JSON。只填進表格，模組包要等使用者按「套用」才會被改。"""
        start_dir = str(self._export_dir) if self._export_dir else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "匯入翻譯完成之失敗項目", start_dir, "JSON 檔案 (*.json)"
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            QMessageBox.warning(self, "匯入失敗", f"無法讀取 {path}：\n{exc}")
            return

        entries = _entries_from(data)
        if not entries:
            QMessageBox.warning(
                self,
                "匯入失敗",
                "檔案裡找不到任何譯文。\n\n"
                "請確認交回來的是當初匯出的那份 JSON，\n"
                "而且譯文填在每一筆的 zh_tw 欄位裡。",
            )
            return

        # 匯入的譯文一律標色，提醒還沒經過人確認；沒通過品質閘的另外用警示色，滑鼠
        # 移過去看得到原因。線上模型最愛把多行併成一行，那種東西套下去整個框會爆版。
        palette = active_palette()
        normal = _tint(palette.primary, 48)
        suspect = _tint(palette.warning, 64)

        filled: set[int] = set()
        unmatched: list[str] = []
        self._flagged.clear()
        for entry in entries:
            indexes = self._match_rows(entry)
            if not indexes:
                unmatched.append(entry.key or entry.en_us)
                continue
            for index in indexes:
                reason = rejection_reason(self._rows[index][2], entry.text)
                item = QTableWidgetItem(entry.text)
                item.setBackground(suspect if reason else normal)
                if reason:
                    item.setToolTip(f"這條可能有問題：{reason}")
                    self._flagged[index] = reason
                self.table.setItem(index, _ZH_COLUMN, item)
                filled.add(index)

        self.review_only_chk.setVisible(bool(self._flagged))
        if not self._flagged:
            self.review_only_chk.setChecked(False)
        self._apply_filter(self.filter_edit.text())

        status = f"已匯入 {len(filled):,} 條譯文"
        if self._flagged:
            status += f"，其中 {len(self._flagged):,} 條需要確認（警示色）"
        if unmatched:
            status += f"，{len(unmatched):,} 條對不上目前的清單而略過"
        self.status_label.setText(status + "。確認無誤後按「套用」才會寫回模組包。")
        self.status_label.setVisible(True)

        message = f"已匯入 {len(filled):,} 條譯文，填在「繁中譯文」欄並標了底色。\n\n"
        if self._flagged:
            preview = "\n".join(
                f"  {self._rows[index][1]}：{reason}"
                for index, reason in list(self._flagged.items())[:6]
            )
            message += (
                f"其中 {len(self._flagged):,} 條沒通過程式自己的檢查，已用警示色標出\n"
                "（滑鼠移到譯文上看得到原因）。勾選「只顯示需確認的項目」可單獨檢視：\n"
                f"{preview}\n"
            )
            if len(self._flagged) > 6:
                message += f"  …另外還有 {len(self._flagged) - 6:,} 條\n"
            message += "\n"
        if unmatched:
            preview = "\n".join(f"  {name}" for name in unmatched[:6])
            message += f"另有 {len(unmatched):,} 條對不上目前的失敗清單，已略過：\n{preview}\n"
            if len(unmatched) > 6:
                message += f"  …另外還有 {len(unmatched) - 6:,} 條\n"
            message += "\n"
        message += "請確認翻譯是否恰當（可以直接在表格裡修改），\n按下「套用」之後才會寫回模組包。"

        if self._flagged or unmatched:
            QMessageBox.warning(self, "匯入完成，有項目需要確認", message)
        else:
            QMessageBox.information(self, "已匯入", message)

    def _match_rows(self, entry: _Entry) -> list[int]:
        """把一筆匯入條目對回列號；由嚴到寬，第一層對得上就算數。"""
        if entry.row_id is not None and 0 <= entry.row_id < len(self._rows):
            if not entry.key or self._rows[entry.row_id][1] == entry.key:
                return [entry.row_id]

        pairs = (
            (self._by_label_key, (entry.label, entry.key) if entry.label and entry.key else None),
            (self._by_key, entry.key or None),
            (self._by_source, entry.en_us or None),
        )
        for lookup, needle in pairs:
            candidates = lookup.get(needle) if needle is not None else None
            if candidates:
                return self._narrow(candidates, entry.en_us)
        return []

    def _narrow(self, candidates: list[int], en_us: str) -> list[int]:
        """同一個鍵撞到多列時，原文一樣的才是同一條。"""
        if len(candidates) == 1 or not en_us:
            return candidates
        exact = [index for index in candidates if self._rows[index][2] == en_us]
        return exact or candidates


def _entries_from(data) -> list[_Entry]:
    """把匯入檔正規化成條目清單。

    接受三種形狀：原樣匯出的 {"items": [...]}、只剩清單的裸陣列，以及模型偷懶
    回的 {"鍵": "譯文"} 對照表。對不上的條目留給呼叫端回報，這裡不做判斷。
    """
    if isinstance(data, dict):
        items = data.get("items")
        if items is None:
            return [
                _Entry(None, "", key, "", value.strip())
                for key, value in data.items()
                if isinstance(key, str) and isinstance(value, str) and value.strip()
            ]
        data = items
    if not isinstance(data, list):
        return []

    entries: list[_Entry] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        text = _field(raw, ("zh_tw", "translation", "target", "zh")).strip()
        if not text:
            continue
        row_id = raw.get("id")
        entries.append(_Entry(
            row_id if isinstance(row_id, int) else None,
            _field(raw, ("source", "label")),
            _field(raw, ("key",)),
            _field(raw, ("en_us", "source_text", "en")),
            text,
        ))
    return entries


def _tint(color: str, alpha: int) -> QBrush:
    """半透明底色。取自當下主題的色票，深淺色模式看起來都不刺眼。"""
    rgba = QColor(color)
    rgba.setAlpha(alpha)
    return QBrush(rgba)


def _field(raw: dict, names: tuple[str, ...]) -> str:
    """依序取第一個有字的欄位。原文不 strip，比對時才對得起原始字串。"""
    for name in names:
        value = raw.get(name)
        if isinstance(value, str) and value:
            return value
    return ""
