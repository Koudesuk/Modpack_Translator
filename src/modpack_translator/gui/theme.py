"""集中式主題系統。

設計重點：所有顏色集中在 PALETTES 字典，全域 QSS 由 build_stylesheet()
依 palette 生成並套到 QApplication。深淺色切換 = 換一份 palette 重套一次，
不需要逐一追蹤各 widget 的顏色。

狀態色（如「停止=紅」「完成=綠」）一律以 Qt 動態屬性（tone / accent）配合
QSS 屬性選擇器表達，切換主題時隨全域 QSS 自動重繪。
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication, QWidget


@dataclass(frozen=True)
class Palette:
    # 結構
    window: str        # 視窗底色
    surface: str       # 卡片 / 輸入框底色
    surface_alt: str   # 次級表面（log 區）
    border: str        # 一般邊框
    border_strong: str # 強調邊框 / 聚焦
    # 文字
    text: str
    text_muted: str
    # 主色
    primary: str
    primary_hover: str
    primary_press: str
    on_accent: str     # 主色 / 狀態色上的文字
    # 語意狀態色
    success: str
    success_hover: str
    warning: str
    warning_hover: str
    danger: str
    danger_hover: str
    # 互動表面
    hover: str         # 次級按鈕 / 元件 hover 底色
    track: str         # 進度條軌道


LIGHT = Palette(
    window="#f4f5f7",
    surface="#ffffff",
    surface_alt="#fafbfc",
    border="#dbdfe5",
    border_strong="#aeb6c2",
    text="#1f2328",
    text_muted="#6b7280",
    primary="#3b6fe0",
    primary_hover="#3263cf",
    primary_press="#2a55b3",
    on_accent="#ffffff",
    success="#2da44e",
    success_hover="#2c974b",
    warning="#d97706",
    warning_hover="#c2690a",
    danger="#d23142",
    danger_hover="#bb2a3a",
    hover="#eef1f5",
    track="#e7eaef",
)

DARK = Palette(
    window="#13151a",
    surface="#1b1e25",
    surface_alt="#171a20",
    border="#2c313b",
    border_strong="#454d5a",
    text="#e6e9ef",
    text_muted="#9aa4b2",
    primary="#4f8cff",
    primary_hover="#608fff",
    primary_press="#3d7bf0",
    on_accent="#ffffff",
    success="#3fb950",
    success_hover="#4ac45c",
    warning="#e3a008",
    warning_hover="#f0ad1a",
    danger="#f0596a",
    danger_hover="#f56b7a",
    hover="#242833",
    track="#262b34",
)

PALETTES: dict[str, Palette] = {"light": LIGHT, "dark": DARK}

# 進度條 accent 名稱 → palette 屬性名稱（取色 / hover 取色）
_ACCENTS = {
    "blue":   ("primary", "primary_press"),
    "green":  ("success", "success_hover"),
    "orange": ("warning", "warning_hover"),
}


# 即時產生的小圖示（勾選符號 / 上下箭頭）快取，避免重複繪製
_ICON_CACHE: dict = {}


def _icon_dir() -> str:
    # 用純 ASCII 的暫存路徑，避免 QSS url() 解析 CJK 路徑出問題
    d = os.path.join(tempfile.gettempdir(), "modpack_translator_ui")
    os.makedirs(d, exist_ok=True)
    return d


def _check_icon(color: str) -> str:
    """白色（或指定色）勾選符號，置於核取方塊勾選後的底色上。"""
    key = ("check", color)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    pm = QPixmap(18, 18)
    pm.fill(Qt.GlobalColor.transparent)
    pt = QPainter(pm)
    pt.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color))
    pen.setWidthF(2.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pt.setPen(pen)
    pt.drawPolyline(QPolygonF([QPointF(4, 9.5), QPointF(7.5, 13), QPointF(14, 5.5)]))
    pt.end()
    path = os.path.join(_icon_dir(), f"check_{color.lstrip('#')}.png")
    pm.save(path, "PNG")
    out = path.replace("\\", "/")
    _ICON_CACHE[key] = out
    return out


def _arrow_icon(color: str, up: bool) -> str:
    """實心三角形上/下箭頭，給 spinbox 的上下按鈕用。"""
    key = ("arrow", color, up)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    pm = QPixmap(10, 10)
    pm.fill(Qt.GlobalColor.transparent)
    pt = QPainter(pm)
    pt.setRenderHint(QPainter.RenderHint.Antialiasing)
    pt.setPen(Qt.PenStyle.NoPen)
    pt.setBrush(QColor(color))
    if up:
        poly = QPolygonF([QPointF(2, 6.5), QPointF(8, 6.5), QPointF(5, 3)])
    else:
        poly = QPolygonF([QPointF(2, 3.5), QPointF(8, 3.5), QPointF(5, 7)])
    pt.drawPolygon(poly)
    pt.end()
    path = os.path.join(_icon_dir(), f"arrow_{'up' if up else 'dn'}_{color.lstrip('#')}.png")
    pm.save(path, "PNG")
    out = path.replace("\\", "/")
    _ICON_CACHE[key] = out
    return out


def build_stylesheet(p: Palette) -> str:
    """依 palette 生成整個 App 的 QSS。"""

    def progress_chunks() -> str:
        out = []
        for name, (base, _edge) in _ACCENTS.items():
            color = getattr(p, base)
            out.append(
                f'QProgressBar[accent="{name}"]::chunk {{ background-color: {color}; }}'
            )
        return "\n".join(out)

    check = _check_icon(p.on_accent)
    arrow_up = _arrow_icon(p.text_muted, up=True)
    arrow_dn = _arrow_icon(p.text_muted, up=False)
    arrow_up_off = _arrow_icon(p.border_strong, up=True)
    arrow_dn_off = _arrow_icon(p.border_strong, up=False)

    return f"""
/* ── 基礎 ───────────────────────────────────────────────── */
* {{
    font-family: "Segoe UI", "Microsoft JhengHei UI", "Noto Sans TC", sans-serif;
    font-size: 13px;
    color: {p.text};
}}
/* 只有最外層容器塗底色；標籤類維持透明，避免在卡片上出現色塊 */
QMainWindow, QDialog, QMessageBox, #rootCentral {{
    background-color: {p.window};
}}
QLabel, QCheckBox, QRadioButton {{
    background: transparent;
}}
QToolTip {{
    background-color: {p.surface};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: 6px;
    padding: 6px 8px;
}}

/* ── 標題列 ─────────────────────────────────────────────── */
QLabel#titleLabel {{
    font-size: 19px;
    font-weight: 700;
    color: {p.text};
}}
QLabel#versionChip {{
    color: {p.text_muted};
    background-color: {p.hover};
    border: 1px solid {p.border};
    border-radius: 9px;
    padding: 1px 9px;
    font-size: 11px;
    font-weight: 600;
}}
QLabel#statsLabel {{
    color: {p.text_muted};
    font-size: 12px;
}}
QLabel#sectionLabel {{
    font-weight: 700;
    color: {p.text};
}}

/* ── 卡片（GroupBox）─────────────────────────────────────── */
QGroupBox {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: 10px;
    margin-top: 14px;
    padding: 14px 14px 12px 14px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 6px;
    color: {p.text_muted};
    font-size: 12px;
    font-weight: 700;
}}

/* ── 輸入框 / 數字框 ─────────────────────────────────────── */
QLineEdit, QSpinBox {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 6px 10px;
    selection-background-color: {p.primary};
    selection-color: {p.on_accent};
}}
QLineEdit:hover, QSpinBox:hover {{
    border-color: {p.border_strong};
}}
QLineEdit:focus, QSpinBox:focus {{
    border-color: {p.primary};
}}
QLineEdit:disabled, QSpinBox:disabled {{
    color: {p.text_muted};
    background-color: {p.surface_alt};
}}

/* spinbox 的上下按鈕：明確畫出按鈕底色與三角箭頭，確保兩種主題都看得清楚 */
QSpinBox {{
    padding-right: 22px;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border;
    width: 20px;
    background-color: {p.hover};
    border-left: 1px solid {p.border};
}}
QSpinBox::up-button {{
    subcontrol-position: top right;
    border-top-right-radius: 8px;
    border-bottom: 1px solid {p.border};
}}
QSpinBox::down-button {{
    subcontrol-position: bottom right;
    border-bottom-right-radius: 8px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background-color: {p.track};
}}
QSpinBox::up-button:pressed, QSpinBox::down-button:pressed {{
    background-color: {p.border};
}}
QSpinBox::up-arrow {{
    image: url("{arrow_up}");
    width: 10px;
    height: 10px;
}}
QSpinBox::down-arrow {{
    image: url("{arrow_dn}");
    width: 10px;
    height: 10px;
}}
QSpinBox::up-arrow:disabled, QSpinBox::up-arrow:off {{
    image: url("{arrow_up_off}");
}}
QSpinBox::down-arrow:disabled, QSpinBox::down-arrow:off {{
    image: url("{arrow_dn_off}");
}}

/* ── 按鈕（次級 / 預設）─────────────────────────────────── */
QPushButton {{
    background-color: {p.surface};
    border: 1px solid {p.border};
    border-radius: 8px;
    padding: 6px 14px;
    font-weight: 600;
    color: {p.text};
}}
QPushButton:hover {{
    background-color: {p.hover};
    border-color: {p.border_strong};
}}
QPushButton:pressed {{
    background-color: {p.track};
}}
QPushButton:disabled {{
    color: {p.text_muted};
    background-color: {p.surface_alt};
    border-color: {p.border};
}}

/* 主要動作按鈕：實心主色 */
QPushButton#primaryButton {{
    background-color: {p.primary};
    color: {p.on_accent};
    border: none;
}}
QPushButton#primaryButton:hover {{ background-color: {p.primary_hover}; }}
QPushButton#primaryButton:pressed {{ background-color: {p.primary_press}; }}
QPushButton#primaryButton:disabled {{
    background-color: {p.border};
    color: {p.text_muted};
}}
/* 主要按鈕的語意狀態（以動態屬性 tone 切換）*/
QPushButton#primaryButton[tone="danger"]  {{ background-color: {p.danger}; color: {p.on_accent}; }}
QPushButton#primaryButton[tone="danger"]:hover  {{ background-color: {p.danger_hover}; }}
QPushButton#primaryButton[tone="warning"] {{ background-color: {p.warning}; color: {p.on_accent}; }}
QPushButton#primaryButton[tone="warning"]:hover {{ background-color: {p.warning_hover}; }}
QPushButton#primaryButton[tone="success"] {{ background-color: {p.success}; color: {p.on_accent}; }}
QPushButton#primaryButton[tone="success"]:hover {{ background-color: {p.success_hover}; }}

/* 圓形說明（?）按鈕 */
QPushButton#helpButton {{
    color: {p.primary};
    border: 1.5px solid {p.primary};
    border-radius: 11px;
    background: transparent;
    font-weight: 700;
    padding: 0;
}}
QPushButton#helpButton:hover {{
    background-color: {p.primary};
    color: {p.on_accent};
}}

/* 標題列主題切換按鈕 */
QPushButton#themeToggle {{
    background: transparent;
    border: 1px solid {p.border};
    border-radius: 8px;
    font-size: 16px;
    padding: 0;
}}
QPushButton#themeToggle:hover {{
    background-color: {p.hover};
    border-color: {p.border_strong};
}}

/* ── 核取方塊 ───────────────────────────────────────────── */
QCheckBox {{
    spacing: 8px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border: 1px solid {p.border_strong};
    border-radius: 5px;
    background-color: {p.surface};
}}
QCheckBox::indicator:hover {{
    border-color: {p.primary};
}}
QCheckBox::indicator:checked {{
    background-color: {p.primary};
    border-color: {p.primary};
    image: url("{check}");
}}

/* ── log 文字區 ─────────────────────────────────────────── */
QTextEdit {{
    background-color: {p.surface_alt};
    border: 1px solid {p.border};
    border-radius: 10px;
    padding: 8px;
    selection-background-color: {p.primary};
    selection-color: {p.on_accent};
}}

/* ── 進度條 ─────────────────────────────────────────────── */
QProgressBar {{
    border: none;
    border-radius: 8px;
    background-color: {p.track};
    text-align: center;
    font-size: 12px;
    font-weight: 700;
    color: {p.text};
}}
QProgressBar::chunk {{
    border-radius: 8px;
    background-color: {p.primary};
}}
{progress_chunks()}

/* ── 捲軸 ───────────────────────────────────────────────── */
QScrollBar:vertical {{
    background: transparent;
    width: 12px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background-color: {p.border_strong};
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background-color: {p.text_muted};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 12px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background-color: {p.border_strong};
    border-radius: 5px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{
    background-color: {p.text_muted};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
"""


_ACTIVE = LIGHT


def active_palette() -> Palette:
    """apply_theme() 最後套用的那一份 palette。

    給少數必須在程式碼裡取色的地方用（QSS 選不到的東西，例如表格儲存格底色）。
    直接記住套過哪一份，不去問 styleHints——它回報的是系統實際配色，使用者按了
    主題切換鈕之後就跟程式選的那份對不起來了。
    """
    return _ACTIVE


def restyle(widget: QWidget) -> None:
    """改完動態屬性後重新套用 QSS（屬性選擇器才會重新評估）。"""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def apply_theme(mode: str) -> None:
    """套用主題到整個 App：設定原生色彩配置 + 全域 QSS。

    mode 為 "light" 或 "dark"；原生色彩配置讓 Fusion 繪製的元件
    （如 spinbox 箭頭）也跟著深淺色，QSS 則負責現代外觀。
    """
    global _ACTIVE
    app = QApplication.instance()
    if app is None:
        return
    palette = PALETTES.get(mode, LIGHT)
    _ACTIVE = palette
    scheme = Qt.ColorScheme.Dark if mode == "dark" else Qt.ColorScheme.Light
    app.styleHints().setColorScheme(scheme)
    app.setStyleSheet(build_stylesheet(palette))
