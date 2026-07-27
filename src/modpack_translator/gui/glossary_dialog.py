"""自訂用語表編輯對話框。

使用者在這裡指定「這個英文詞一律翻成這個中文」。存成 JSON 放在輸出目錄底下，
自動更新不會蓋掉。優先序高於內建的官方原版用語，所以也能用來覆蓋官方譯名；
譯名留空即代表把該詞從用語庫移除。
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class GlossaryDialog(QDialog):
    def __init__(self, path: Path, parent=None) -> None:
        super().__init__(parent)
        self._path = Path(path)
        self.setWindowTitle("自訂用語")
        self.setMinimumSize(560, 420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "指定英文詞的固定譯法。優先序高於內建的 Minecraft 官方用語，\n"
            "可用來覆蓋官方譯名；譯名留空則代表不使用該詞條。"
        ))

        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["英文原詞", "繁中譯名"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        layout.addWidget(self.table)

        button_row = QHBoxLayout()
        add_btn = QPushButton("新增一列")
        remove_btn = QPushButton("刪除選取列")
        add_btn.clicked.connect(lambda: self._append_row("", ""))
        remove_btn.clicked.connect(self._remove_selected)
        button_row.addWidget(add_btn)
        button_row.addWidget(remove_btn)
        button_row.addStretch()
        layout.addLayout(button_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._load()

    # ------------------------------------------------------------------ IO

    def _load(self) -> None:
        terms: dict[str, str] = {}
        if self._path.is_file():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    terms = {k: v for k, v in data.items()
                             if isinstance(k, str) and isinstance(v, str)}
            except (OSError, json.JSONDecodeError, UnicodeDecodeError):
                terms = {}
        for source, translated in sorted(terms.items(), key=lambda kv: kv[0].lower()):
            self._append_row(source, translated)
        if not terms:
            self._append_row("", "")

    def _save(self) -> None:
        terms: dict[str, str] = {}
        for row in range(self.table.rowCount()):
            source = self._cell_text(row, 0)
            if not source:
                continue
            terms[source] = self._cell_text(row, 1)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(dict(sorted(terms.items(), key=lambda kv: kv[0].lower())),
                           ensure_ascii=False, indent=1) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            QMessageBox.warning(self, "儲存失敗", f"無法寫入 {self._path}：\n{exc}")
            return
        self.accept()

    # ------------------------------------------------------------------ table

    def _append_row(self, source: str, translated: str) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(source))
        self.table.setItem(row, 1, QTableWidgetItem(translated))

    def _remove_selected(self) -> None:
        for index in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(index)

    def _cell_text(self, row: int, column: int) -> str:
        item = self.table.item(row, column)
        return item.text().strip() if item is not None else ""
