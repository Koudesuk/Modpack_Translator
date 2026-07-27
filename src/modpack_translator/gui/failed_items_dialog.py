"""翻譯失敗項目的手動補譯對話框。

翻譯結束後，把沒能自動翻好的字串直接攤在使用者面前逐條補譯，套用後由程式寫回
模組包——使用者不必自己去 jar 或設定檔裡找那一行在哪。

留空 = 保留原文。補好的譯文會存進手動譯文表，下次翻譯時優先套用，不會被自動流程
覆蓋掉。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

_READ_ONLY_FLAGS = Qt.ItemIsEnabled | Qt.ItemIsSelectable


class FailedItemsDialog(QDialog):
    """rows 為 (來源標籤, 鍵, 原文)；translations() 回傳 {列索引: 譯文}。

    initial 是先前補過的譯文 {列索引: 譯文}，重新開啟時填回去，使用者才能接著改。
    """

    def __init__(
        self,
        rows: list[tuple[str, str, str]],
        parent=None,
        initial: dict[int, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self._rows = rows
        initial = initial or {}
        self.setWindowTitle("翻譯失敗項目")
        self.setMinimumSize(900, 560)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"以下 {len(rows):,} 條字串沒能自動翻好，可以在此手動補上。\n"
            "在「繁中譯文」欄輸入譯文後按「套用」，程式會直接寫回模組包；\n"
            "留空的項目保留英文原文。補上的譯文會被記住，下次翻譯不會被蓋掉。"
        ))

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("搜尋："))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("輸入關鍵字過濾（來源、鍵、原文）")
        self.filter_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.filter_edit)
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
            self.table.setItem(index, 3, QTableWidgetItem(initial.get(index, "")))
        layout.addWidget(self.table)

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
            item = self.table.item(index, 3)
            text = item.text().strip() if item is not None else ""
            if text:
                filled[index] = text
        return filled

    def _apply_filter(self, needle: str) -> None:
        needle = needle.strip().lower()
        for index, (label, key, source) in enumerate(self._rows):
            haystack = f"{label}\n{key}\n{source}".lower()
            self.table.setRowHidden(index, bool(needle) and needle not in haystack)
