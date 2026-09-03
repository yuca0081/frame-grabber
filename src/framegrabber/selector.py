"""全屏区域框选：像截图工具一样，拖拽框出要录制的屏幕区域。

DPI 说明（重要）：
    Qt 界面用"逻辑像素"，mss 截屏用"物理像素"（Windows 150% 缩放下两者相差 1.5 倍）。
    逻辑 → 物理的换算只在 selector.py 里做一次，
    其他模块拿到的一律是物理像素（mss 坐标系）。
"""
from __future__ import annotations

import mss
from PySide6.QtCore import QEventLoop, QRect, QRectF, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

from framegrabber.theme import ACCENT, WARNING

MIN_SIZE = 32            # 选区最小边长（物理像素）
MAX_SIZE = (2560, 1600)  # 选区最大 宽/高——更大的区域高帧率下来不及截图+存盘


class _ScreenOverlay(QWidget):
    """盖在某一块屏幕上的半透明遮罩，负责在这块屏幕上拖拽框选。"""

    def __init__(self, screen, controller: "RegionSelector"):
        super().__init__(
            None,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool,
        )
        self._screen = screen
        self._ctrl = controller
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.CrossCursor)
        self._origin = None   # 按下时的全局逻辑坐标
        self._current = None  # 拖拽中的全局逻辑坐标

    # ---------- 鼠标 / 键盘 ----------

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._origin = self._current = e.globalPosition().toPoint()
            self.update()
        elif e.button() == Qt.RightButton and self._origin is None:
            self._ctrl.cancel()  # 未拖拽时右键也可取消

    def mouseMoveEvent(self, e):
        if self._origin is not None:
            self._current = e.globalPosition().toPoint()
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._origin is not None:
            rect, phys_w, phys_h, _clamped = self._drag_rect()
            self._origin = self._current = None
            if phys_w >= MIN_SIZE and phys_h >= MIN_SIZE:
                self._ctrl.finish(rect, self._screen)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self._ctrl.cancel()

    # ---------- 几何换算 ----------

    def _drag_rect(self):
        """返回 (本屏幕逻辑坐标下的选区, 物理宽, 物理高, 是否因超限被钳制)。

        拖拽时实时钳制：不超出本屏幕、不超过最大尺寸（锚定拖拽起点方向），
        因此用户看到的选区就是最终截出来的区域。
        """
        if self._origin is None:
            return None, 0, 0, False
        d = self._screen.devicePixelRatio()
        sg = self._screen.geometry()
        # 全局逻辑 → 本屏幕本地物理（浮点运算，避免整数截断造成截边错位）
        x1 = (self._origin.x() - sg.left()) * d
        y1 = (self._origin.y() - sg.top()) * d
        x2 = (self._current.x() - sg.left()) * d
        y2 = (self._current.y() - sg.top()) * d
        left, right = (x1, x2) if x1 <= x2 else (x2, x1)
        top, bottom = (y1, y2) if y1 <= y2 else (y2, y1)
        # 限制在本屏幕内
        left, top = max(left, 0.0), max(top, 0.0)
        right = min(right, sg.width() * d)
        bottom = min(bottom, sg.height() * d)
        want_w, want_h = right - left, bottom - top  # 钳制前想要的尺寸
        # 最大尺寸（按拖拽方向锚定起点）
        max_w, max_h = MAX_SIZE
        if x1 <= x2:
            right = min(right, left + max_w)
        else:
            left = max(left, right - max_w)
        if y1 <= y2:
            bottom = min(bottom, top + max_h)
        else:
            top = max(top, bottom - max_h)
        phys_w, phys_h = round(right - left), round(bottom - top)
        clamped = (right - left) < want_w - 0.5 or (bottom - top) < want_h - 0.5
        logical = QRectF(left / d, top / d, (right - left) / d,
                         (bottom - top) / d).toAlignedRect()
        return logical, phys_w, phys_h, clamped

    # ---------- 绘制 ----------

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0, 90))

        rect, phys_w, phys_h, clamped = self._drag_rect()
        if rect is None or rect.isEmpty():
            self._draw_hint(p)
            return
        # 超限时整组（边框+文字）变红提醒
        color = QColor(WARNING if clamped else ACCENT)
        # 把选区从遮罩里"挖空"并描边
        p.setCompositionMode(QPainter.CompositionMode_Clear)
        p.fillRect(QRectF(rect), QColor(0, 0, 0, 0))
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        p.setPen(QPen(color, 2))
        p.drawRect(rect)
        # 尺寸标签——显示的就是最终截出的物理像素尺寸
        text = f"{phys_w} × {phys_h}" + ("（已达上限）" if clamped else "")
        fm = p.fontMetrics()
        tw, th = fm.horizontalAdvance(text) + 12, fm.height() + 6
        lx = min(max(rect.left(), 0), max(0, self.width() - tw))
        ly = rect.top() - th - 6
        if ly < 0:
            ly = rect.bottom() + 6
        p.fillRect(QRect(lx, ly, tw, th), QColor(0, 0, 0, 200))
        p.setPen(color)
        p.drawText(QRect(lx, ly, tw, th), Qt.AlignCenter, text)

    def _draw_hint(self, p):
        text = "拖拽框选要录制的区域，按 ESC 取消"
        f = QFont(self.font())
        f.setPointSize(11)
        p.setFont(f)
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(text)
        x, y = (self.width() - tw) / 2, self.height() / 5
        p.fillRect(QRectF(x - 14, y - fm.height() - 4, tw + 28, fm.height() + 12),
                   QColor(0, 0, 0, 190))
        p.setPen(QColor("#ffffff"))
        p.drawText(QPointF(x, y), text)


class RegionSelector:
    """用法：rect = RegionSelector.pick()
    返回 {'left','top','width','height'}（物理像素，mss 坐标），ESC 取消返回 None。
    """

    def __init__(self):
        self.result: dict | None = None
        self._loop: QEventLoop | None = None

    @classmethod
    def pick(cls) -> dict | None:
        self = cls()
        pairs = [(_ScreenOverlay(s, self), s) for s in QGuiApplication.screens()]
        overlays = [o for o, _screen in pairs]
        for o in overlays:
            o.show()
        for o in overlays:
            o.raise_()
        primary = next(
            (o for o, s in pairs if s is QGuiApplication.primaryScreen()),
            overlays[0],
        )
        primary.activateWindow()  # 让 ESC 键盘事件有焦点
        loop = QEventLoop()
        self._loop = loop
        loop.exec()
        for o in overlays:
            o.close()
            o.deleteLater()
        return self.result

    # 由遮罩回调
    def finish(self, local_logical_rect: QRect, screen):
        d = screen.devicePixelRatio()
        phys = QRectF(local_logical_rect.x() * d, local_logical_rect.y() * d,
                      local_logical_rect.width() * d,
                      local_logical_rect.height() * d).toAlignedRect()
        mon = self._monitor_for(screen)
        rect = {
            "left": mon["left"] + phys.x(),
            "top": mon["top"] + phys.y(),
            "width": min(phys.width(), mon["width"]),
            "height": min(phys.height(), mon["height"]),
        }
        # 最终钳制在该显示器范围内
        rect["left"] = max(mon["left"],
                           min(rect["left"], mon["left"] + mon["width"] - rect["width"]))
        rect["top"] = max(mon["top"],
                          min(rect["top"], mon["top"] + mon["height"] - rect["height"]))
        self.result = rect
        self._loop.quit()

    def cancel(self):
        self.result = None
        if self._loop:
            self._loop.quit()

    @staticmethod
    def _monitor_for(screen) -> dict:
        """找到这块 Qt 屏幕对应的 mss 显示器（物理全局坐标）。"""
        with mss.MSS() as sct:
            mons = sct.monitors[1:]  # monitors[0] 是所有屏幕的合集
        if len(mons) == 1:
            return mons[0]
        d = screen.devicePixelRatio()
        g = screen.geometry()
        cx, cy = (g.left() + g.width() / 2) * d, (g.top() + g.height() / 2) * d
        return min(mons, key=lambda m: abs(m["left"] + m["width"] / 2 - cx)
                                   + abs(m["top"] + m["height"] / 2 - cy))
