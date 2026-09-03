"""界面主题：颜色与全局样式表集中一处，改主题不用满文件找。

配色：slate 深色系 + 绿色行动色（CTA）。
    - 绿色 = 行动（新建录制、录制按钮、滑块已填充部分）
    - 青色 = 选区语义（框选边框、选区标记），需在任意画面上可见
    - 文字：主 #F8FAFC / 次 #94A3B8（深底上对比度均 ≥7:1）
"""

# ---- 语义色（代码里引用） ----
ACCENT = "#39c5ff"     # 选区边框、尺寸标签（青色 = 选区语义）
WARNING = "#f87171"    # 超限/错误提示
CANVAS_BG = "#1E293B"  # 查看器画布底色（slate-800 面板色）

# ---- 调色板（QSS 用） ----
BG = "#0F172A"         # 窗口底 slate-900
PANEL = "#1E293B"      # 面板/控件底 slate-800
PANEL_HI = "#334155"   # hover/边框 slate-700
BORDER = "#334155"
TEXT = "#F8FAFC"
MUTED = "#94A3B8"
GREEN = "#22C55E"      # 行动色 green-500
GREEN_HI = "#16A34A"   # hover green-600
GREEN_TEXT = "#052e16"

INFO_SKY = "#7dd3fc"   # 悬浮条信息文字
REC_RED = "#f87171"    # 录制中
PAUSE_AMBER = "#fbbf24"  # 已暂停

FONT = '"Microsoft YaHei UI", "Microsoft YaHei", sans-serif'

APP_QSS = f"""
* {{
    font-family: {FONT};
    font-size: 9pt;
    color: {TEXT};
}}
QWidget {{ background: {BG}; }}
QToolTip {{
    background: {PANEL}; color: {TEXT};
    border: 1px solid {BORDER}; padding: 4px 8px;
}}

/* ---- 按钮 ---- */
QPushButton {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 14px;
}}
QPushButton:hover {{ background: {PANEL_HI}; border-color: #475569; }}
QPushButton:pressed {{ background: {BG}; }}
QPushButton:disabled {{ color: #64748B; background: #16213a; border-color: #243350; }}
QPushButton:focus {{ border-color: {GREEN}; }}

/* 主行动按钮（新建录制 / ● 录制） */
QPushButton#primaryAction {{
    background: {GREEN}; color: {GREEN_TEXT};
    border: none; font-weight: 600;
}}
QPushButton#primaryAction:hover {{ background: {GREEN_HI}; }}
QPushButton#primaryAction:disabled {{ background: #1d3a2a; color: #4b6b58; }}

/* 查看器走带按钮（|◀ ◀ ▶ ▶|）：统一小方块 */
QPushButton#transport {{
    padding: 5px 10px; min-width: 30px;
}}

/* ---- 输入控件 ---- */
QComboBox, QSpinBox {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 4px 8px;
}}
QComboBox:hover, QSpinBox:hover {{ border-color: #475569; }}
QComboBox:focus, QSpinBox:focus {{ border-color: {GREEN}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: {PANEL}; border: 1px solid {BORDER};
    selection-background-color: {PANEL_HI};
}}

/* ---- 弹出菜单（导出按钮） ----
   不写的话走原生浅色菜单，但全局 * 规则把文字染成浅色 → 看不清 */
QMenu {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{ padding: 6px 28px; border-radius: 4px; }}
QMenu::item:selected {{ background: {PANEL_HI}; }}

/* ---- 滑块（中性灰，无填充色） ---- */
QSlider::groove:horizontal {{
    height: 4px; background: {PANEL_HI}; border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {PANEL_HI}; border-radius: 2px; }}
/* 帧进度条例外：把手左侧（已看过部分）用行动色标出 */
QSlider#frameSlider::sub-page:horizontal {{ background: {GREEN}; }}
QSlider::handle:horizontal {{
    width: 14px; height: 14px; margin: -6px 0;
    border-radius: 7px; background: {TEXT};
}}
QSlider::handle:horizontal:hover {{ background: #cbd5e1; }}

/* ---- 列表 ---- */
QListWidget {{
    background: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px;
}}
QListWidget::item {{ padding: 8px 6px; border-radius: 6px; }}
QListWidget::item:hover {{ background: #26334a; }}
QListWidget::item:selected {{ background: {PANEL_HI}; }}

/* ---- 滚动条 ---- */
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{
    background: {PANEL_HI}; border-radius: 5px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: #475569; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ---- 文字 ---- */
QLabel#title {{ font-size: 22px; font-weight: 700; }}
QLabel#muted, QLabel#storagePath {{ color: {MUTED}; }}
QLabel#frameCounter {{
    font-family: "Consolas, monospace"; font-size: 10pt;
    color: {TEXT};
}}
QStatusBar {{ color: {MUTED}; background: transparent; }}

/* ---- 悬浮控制条 ---- */
FloatingBar {{
    background: {BG};
    border: 1px solid {BORDER};
    border-radius: 10px;
}}
FloatingBar QLabel {{ background: transparent; }}

/* ---- 半透明窗口豁免 ---- */
/* 上面的 QWidget 类型选择器会匹配所有子类，给半透明窗口垫上
   不透明背景（表现为遮罩上出现一块额外的深色矩形），
   这里必须显式改回透明 */
_ScreenOverlay, _RegionMarker {{
    background: transparent;
    border: none;
}}
"""
