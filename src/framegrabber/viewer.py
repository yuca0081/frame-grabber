"""帧查看器：逐帧步进、洋葱皮叠加、变速播放、无损缩放、胶片栏管理帧。

内存策略：一次 60fps × 1 分钟的录制有 3600 张 PNG，全部解码约几个 GB，
所以 FrameCache 惰性解码 + LRU 按字节上限淘汰，并在步进时预加载邻帧。
"""
from __future__ import annotations

import shutil
import time
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import (
    QEvent, QMutex, QPoint, QPointF, QRect, QSize, Qt, QThread, QTimer,
    QWaitCondition, Signal,
)
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter, QPen, \
    QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSlider,
    QVBoxLayout, QWidget,
)

from framegrabber.icons import icon
from framegrabber.session import Session
from framegrabber.theme import ACCENT, BG, CANVAS_BG, MUTED, PANEL_HI, TEXT


class FrameCache:
    """帧图片惰性解码 + LRU 缓存。

    原图与洋葱皮染色帧共用一份字节数预算（默认合计 256MB），
    统一按键 (帧号) / (帧号, 染色类型) 存放，逐出最久未用的。
    """

    TINTS = {"prev": QColor(255, 120, 120),   # 洋葱皮：前一帧染红
            "next": QColor(120, 170, 255)}    #          后一帧染蓝

    def __init__(self, paths: list[Path], max_bytes: int = 256 * 1024 * 1024):
        self._paths = list(paths)
        self._max_bytes = max_bytes
        self._bytes = 0
        self._cache: OrderedDict[object, QPixmap] = OrderedDict()

    def __len__(self) -> int:
        return len(self._paths)

    @staticmethod
    def _size_of(pm: QPixmap) -> int:
        return max(1, pm.width() * pm.height() * 4)

    def _lookup(self, key) -> QPixmap | None:
        pm = self._cache.get(key)
        if pm is not None:
            self._cache.move_to_end(key)
        return pm

    def _store(self, key, pm: QPixmap):
        self._cache[key] = pm
        self._bytes += self._size_of(pm)
        while self._bytes > self._max_bytes and len(self._cache) > 2:
            _, old = self._cache.popitem(last=False)
            self._bytes -= self._size_of(old)

    def pixmap(self, i: int) -> QPixmap | None:
        """解码（或命中缓存）第 i 帧；越界返回 None。"""
        if not 0 <= i < len(self._paths):
            return None
        pm = self._lookup(i)
        if pm is not None:
            return pm
        pm = QPixmap(str(self._paths[i]))
        if pm.isNull():
            return None
        self._store(i, pm)
        return pm

    def set_paths(self, paths: list[Path]):
        """帧列表被编辑（删除/插入）后整体替换。

        缓存按序号索引，增删后序号全部错位，只能整体作废重新惰性解码。
        """
        self._paths = list(paths)
        self._cache.clear()
        self._bytes = 0

    def tinted(self, i: int, kind: str) -> QPixmap | None:
        """染色帧（洋葱皮用）：白底染成主题色，像素内容相乘保留明暗。"""
        if not 0 <= i < len(self._paths):
            return None
        key = (i, kind)
        pm = self._lookup(key)
        if pm is not None:
            return pm
        base = self.pixmap(i)
        if base is None:
            return None
        pm = QPixmap(base.size())
        pm.fill(self.TINTS[kind])
        p = QPainter(pm)
        p.setCompositionMode(QPainter.CompositionMode_Multiply)
        p.drawPixmap(0, 0, base)
        p.end()
        self._store(key, pm)
        return pm

    def warm(self, indices):
        """预加载：步进/洋葱皮马上要用的帧先解码好。"""
        for i in indices:
            self.pixmap(i)


class FrameCanvas(QWidget):
    """绘制当前帧 + 洋葱皮。所有帧同尺寸（同一选区截出），原点对齐直接叠加。"""

    stepRequested = Signal(int)          # 滚轮步进（正=下一帧）
    zoomChanged = Signal(float, bool)    # 缩放倍率, 是否"适配窗口"模式

    def __init__(self, cache: FrameCache, parent=None):
        super().__init__(parent)
        self._cache = cache
        self._zoom = 1.0        # 显示像素 / 图像像素
        self._pan = QPointF(0, 0)
        self._index = 0
        self._onion = 0         # 洋葱皮层数（前后各 N 帧）
        self._alpha = 0.30      # 洋葱皮不透明度
        self._fit = True
        self._drag_pos = None
        self.setMouseTracking(True)
        self.setMinimumSize(200, 150)

    # ---------- 状态设置（由 ViewerWindow 调用） ----------

    def set_index(self, i: int):
        i = max(0, min(i, len(self._cache) - 1))
        span = self._onion + 1
        self._cache.warm(range(i - span, i + span + 1))
        self._index = i
        self.update()

    def set_onion(self, n: int):
        self._onion = max(0, min(5, n))
        self.set_index(self._index)  # 顺便预热新启用的洋葱皮邻帧

    def set_alpha(self, a: float):
        self._alpha = max(0.05, min(0.6, a))
        self.update()

    @property
    def index(self) -> int:
        return self._index

    @property
    def zoom(self) -> float:
        return self._zoom

    # ---------- 缩放 / 平移 ----------

    def fit(self):
        self._fit = True
        base = self._cache.pixmap(self._index)
        if base is None:
            self.update()
            return
        self._zoom = max(0.01, min(self.width() / base.width(),
                                   self.height() / base.height()))
        self._pan = QPointF(
            (self.width() - base.width() * self._zoom) / 2,
            (self.height() - base.height() * self._zoom) / 2,
        )
        self.zoomChanged.emit(self._zoom, True)
        self.update()

    def set_zoom(self, z: float, anchor: QPointF | None = None):
        self._fit = False
        z = max(0.05, min(32.0, float(z)))
        if anchor is None:
            anchor = QPointF(self.width() / 2, self.height() / 2)
        # 缩放时保持锚点（光标）下的图像内容不动
        self._pan = anchor - (anchor - self._pan) * (z / self._zoom)
        self._zoom = z
        self.zoomChanged.emit(self._zoom, False)
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self._fit:
            self.fit()

    # ---------- 绘制 ----------

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(CANVAS_BG))
        base = self._cache.pixmap(self._index)
        if base is None:
            p.setPen(QColor(MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "无帧")
            return
        # 关闭平滑插值 → 放大不糊（最近邻）
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        p.translate(self._pan)
        p.scale(self._zoom, self._zoom)

        if self._onion > 0:
            # 越远的帧越淡：alpha 从 base/N 线性升到 base
            for k in range(self._onion, 0, -1):
                self._draw_layer(p, self._index - k, "prev",
                                 self._alpha * (self._onion + 1 - k) / self._onion)
        p.setOpacity(1.0)
        p.drawPixmap(0, 0, base)
        if self._onion > 0:
            for k in range(self._onion, 0, -1):
                self._draw_layer(p, self._index + k, "next",
                                 self._alpha * (self._onion + 1 - k) / self._onion)

    def _draw_layer(self, p: QPainter, i: int, kind: str, alpha: float):
        pm = self._cache.tinted(i, kind)
        if pm is not None:
            p.setOpacity(alpha)
            p.drawPixmap(0, 0, pm)

    # ---------- 交互 ----------

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            factor = 1.25 if e.angleDelta().y() > 0 else 1 / 1.25
            self.set_zoom(self._zoom * factor, e.position())
        else:
            self.stepRequested.emit(1 if e.angleDelta().y() < 0 else -1)

    def mousePressEvent(self, e):
        if e.button() in (Qt.LeftButton, Qt.MiddleButton):
            self._drag_pos = e.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None:
            self._pan += e.position() - self._drag_pos
            self._fit = False
            self._drag_pos = e.position()
            self.update()

    def mouseReleaseEvent(self, _):
        self._drag_pos = None
        self.setCursor(Qt.ArrowCursor)


THUMB_H = 56      # 缩略图高度（逻辑像素）
THUMB_PAD = 4     # 缩略图四周留白
_MAX_THUMBS = 3000  # 缩略图缓存上限（约 60MB），超出整体清空重来


def _retry_fs(op):
    """执行文件操作，被占用时短暂重试。

    Windows 上文件正被缩略图线程读取的瞬间，删除/改名会抛 PermissionError；
    缩略图解码几十毫秒内结束，等一下再试几乎总能成功。
    """
    for _ in range(20):
        try:
            op()
            return
        except PermissionError:
            time.sleep(0.02)
    op()  # 仍失败则抛出真实异常，交给调用方提示


class _ThumbMaker(QThread):
    """后台解码缩略图。QImage 可以在工作线程创建，QPixmap 必须在界面线程，
    所以线程里只出 QImage，回到主线程再转 QPixmap。
    代际号（gen）：帧列表被编辑后序号错位，旧请求的結果直接丢弃。"""

    ready = Signal(int, int, QImage)  # 代际号, 帧序号, 缩略图

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs: list[tuple[int, int, Path, float]] = []
        self._pending: set[tuple[int, int]] = set()
        self._mutex = QMutex()
        self._cond = QWaitCondition()
        self._stop = False

    def request(self, gen: int, i: int, path: Path, dpr: float):
        key = (gen, i)
        self._mutex.lock()
        if key not in self._pending:
            self._pending.add(key)
            self._jobs.append((gen, i, path, dpr))
        self._mutex.unlock()
        self._cond.wakeAll()

    def shutdown(self):
        self._mutex.lock()
        self._stop = True
        self._mutex.unlock()
        self._cond.wakeAll()
        self.wait(2000)

    def run(self):
        while True:
            self._mutex.lock()
            while not self._jobs and not self._stop:
                self._cond.wait(self._mutex)
            if self._stop:
                self._mutex.unlock()
                return
            gen, i, path, dpr = self._jobs.pop(0)
            self._pending.discard((gen, i))
            self._mutex.unlock()
            # 先把文件读进内存再解码：文件只在读取的几毫秒内被占用，
            # 给主线程的删除/改名留出窗口
            try:
                data = path.read_bytes()
            except OSError:
                continue  # 刚被删掉的帧
            img = QImage.fromData(data)
            if not img.isNull():
                img = img.scaledToHeight(
                    max(1, round(THUMB_H * dpr)), Qt.SmoothTransformation)
                self.ready.emit(gen, i, img)


class FilmStrip(QWidget):
    """胶片栏：一行缩略图展示所有帧。

    交互：单击跳帧 · 按住拖动平移 · 滚轮横向滚动 · 右键删除/插入 ·
    拖入图片文件插入到落点位置。当前帧青色高亮并自动保持在视野内。
    """

    indexSelected = Signal(int)      # 单击第 i 帧
    deleteRequested = Signal(int)    # 删除第 i 帧
    insertRequested = Signal(int)    # 在第 pos 帧之前插入（pos == 帧数 = 追加）
    filesDropped = Signal(int, list)  # 在第 pos 帧之前插入这些图片文件

    def __init__(self, parent=None):
        super().__init__(parent)
        self._paths: list[Path] = []
        self._gen = 0
        self._thumbs: dict[int, QPixmap] = {}
        self._widths: list[int] = []   # 每帧缩略图宽度（解码前按会话宽高比估）
        self._current = -1
        self._hover = -1
        self._press_pos = None
        self._press_idx = -1
        self._dragging = False
        self._user_scrolled = False  # 用户手动滚过：解码完成不拽回中心
        self._maker = _ThumbMaker(self)
        self._maker.ready.connect(self._on_thumb_ready)
        self._maker.start()
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setFixedHeight(THUMB_H + THUMB_PAD * 2)

    def shutdown(self):
        self._maker.shutdown()

    # ---------- 数据 ----------

    def set_paths(self, paths: list[Path], aspect: float = 4 / 3):
        self._paths = list(paths)
        self._gen += 1  # 作废在途的缩略图请求
        self._thumbs.clear()
        default_w = round(THUMB_H * max(0.1, aspect))
        self._widths = [default_w] * len(self._paths)
        self._hover = -1
        self._user_scrolled = False
        self._relayout()
        self.update()

    def _relayout(self):
        if not self._widths:
            self.setMinimumWidth(1)  # 空会话：无留白，不出滚动条
            return
        total = self._pad() * 2 + THUMB_PAD + sum(w + THUMB_PAD
                                                  for w in self._widths)
        self.setMinimumWidth(max(total, 1))

    def _xs(self) -> list[int]:
        """每帧缩略图左边缘 x（首尾各有半视口留白，帧间以 PAD 分隔）。"""
        xs, x = [], self._pad() + THUMB_PAD
        for w in self._widths:
            xs.append(x)
            x += w + THUMB_PAD
        return xs

    # 视口尺寸变化（窗口缩放）→ 留白和居中位置都要跟着变
    def event(self, e):
        if e.type() == QEvent.ParentChange and self.parentWidget() is not None:
            self.parentWidget().installEventFilter(self)
            self._relayout()
        return super().event(e)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Resize:
            self._relayout()
            if not self._user_scrolled:
                self._center_on(self._current)
        return super().eventFilter(obj, ev)

    def set_current(self, i: int):
        self._current = i
        self._user_scrolled = False
        self._center_on(i)
        self.update()

    def _center_on(self, i: int):
        """把第 i 帧滚到视口正中（播放头固定居中的时间轴行为）。"""
        sb = self._hbar()
        if sb is None or not 0 <= i < len(self._widths):
            return
        x, w = self._xs()[i], self._widths[i]
        sb.setValue(round(x + w / 2 - self._view_w() / 2))

    def _scroll_area(self) -> QScrollArea | None:
        p = self.parentWidget()
        while p is not None and not isinstance(p, QScrollArea):
            p = p.parentWidget()
        return p

    def _hbar(self):
        sa = self._scroll_area()
        return sa.horizontalScrollBar() if sa is not None else None

    def _view_w(self) -> int:
        sa = self._scroll_area()
        return sa.viewport().width() if sa is not None else 0

    def _pad(self) -> int:
        """内容首尾各留半个视口宽，让第 0 帧和末帧也能居中。"""
        return self._view_w() // 2

    def _index_at(self, x: int) -> int:
        for i, x0 in enumerate(self._xs()):
            if x0 <= x < x0 + self._widths[i] + THUMB_PAD:
                return i
        return -1

    # ---------- 缩略图 ----------

    def _request_thumb(self, i: int):
        self._maker.request(self._gen, i, self._paths[i], self.devicePixelRatio())

    def _on_thumb_ready(self, gen: int, i: int, img: QImage):
        if gen != self._gen or not 0 <= i < len(self._paths):
            return
        if len(self._thumbs) >= _MAX_THUMBS:  # 超预算整体清空，可见的马上会重新请求
            self._thumbs.clear()
        dpr = self.devicePixelRatio()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        self._thumbs[i] = pm
        w = round(pm.width() / dpr)
        if w != self._widths[i]:
            self._widths[i] = w
            self._relayout()
            if not self._user_scrolled:
                self._center_on(self._current)

    # ---------- 绘制 ----------

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(BG))
        n = len(self._paths)
        if n == 0:
            p.setPen(QColor(MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "无帧 · 右键或拖入图片可插入")
            return
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        vis = self.visibleRegion().boundingRect()
        xs = self._xs()
        for i in range(n):
            x, w = xs[i], self._widths[i]
            if x > vis.right() or x + w < vis.left():
                continue
            rect = QRect(x, THUMB_PAD, w, THUMB_H)
            pm = self._thumbs.get(i)
            if pm is None:
                p.fillRect(rect, QColor(PANEL_HI))
                p.setPen(QColor(MUTED))
                p.drawText(rect, Qt.AlignCenter, str(i + 1))
                self._request_thumb(i)
            else:
                p.drawPixmap(x, THUMB_PAD, pm)
            # 当前帧青色描边（选区语义色），悬停弱描边
            edge = ACCENT if i == self._current else (
                "#64748B" if i == self._hover else None)
            if edge:
                p.setPen(QPen(QColor(edge), 2))
                p.drawRect(rect.adjusted(1, 1, -1, -1))

    # ---------- 鼠标 ----------

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press_pos = e.position().toPoint()
            self._press_idx = self._index_at(self._press_pos.x())
            self._dragging = False

    def mouseMoveEvent(self, e):
        pos = e.position().toPoint()
        if self._press_pos is not None and not self._dragging:
            if (pos - self._press_pos).manhattanLength() > 5:
                self._dragging = True
                self.setCursor(Qt.ClosedHandCursor)
        if self._dragging:
            sb = self._hbar()
            if sb is not None:
                self._user_scrolled = True
                sb.setValue(sb.value() + self._press_pos.x() - pos.x())
            self._press_pos = pos
        elif self._press_pos is None:
            i = self._index_at(pos.x())
            if i != self._hover:
                self._hover = i
                self.setCursor(Qt.PointingHandCursor if i >= 0
                               else Qt.ArrowCursor)
                self.update()

    def mouseReleaseEvent(self, e):
        if (e.button() == Qt.LeftButton and self._press_pos is not None
                and not self._dragging and self._press_idx >= 0):
            self.indexSelected.emit(self._press_idx)
        self._press_pos = None
        self._press_idx = -1
        self._dragging = False
        self.setCursor(Qt.ArrowCursor)

    def leaveEvent(self, _):
        self._hover = -1
        self.update()

    def wheelEvent(self, e):
        sb = self._hbar()
        if sb is not None:
            self._user_scrolled = True
            d = e.angleDelta().x() or e.angleDelta().y()
            sb.setValue(sb.value() - d)
            e.accept()

    def contextMenuEvent(self, e):
        m = QMenu(self)
        i = self._index_at(e.pos().x())
        if i >= 0:
            m.addAction(f"删除第 {i + 1} 帧\tDel",
                       lambda: self.deleteRequested.emit(i))
            m.addAction(f"在第 {i + 1} 帧后插入图片…",
                       lambda: self.insertRequested.emit(i + 1))
        else:
            m.addAction("追加图片到末尾…",
                       lambda: self.insertRequested.emit(len(self._paths)))
        m.exec(e.globalPos())

    # ---------- 拖放插入 ----------

    _IMG_EXTS = {".png", ".jpg", ".jpeg"}

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() and self._droppable(e.mimeData().urls()):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls()
                 if Path(u.toLocalFile()).suffix.lower() in self._IMG_EXTS]
        if not files:
            return
        i = self._index_at(e.position().x())
        pos = i + 1 if i >= 0 else len(self._paths)  # 空白处 = 追加
        self.filesDropped.emit(pos, files)

    def _droppable(self, urls) -> bool:
        return any(Path(u.toLocalFile()).suffix.lower() in self._IMG_EXTS
                   for u in urls)


class ViewerWindow(QMainWindow):
    SPEEDS = [0.1, 0.25, 0.5, 1.0, 2.0]
    DEFAULT_SPEED = 1.0
    # (下拉框文字, 缩放倍率)；倍率 0 表示"适配窗口"
    ZOOM_PRESETS = [("适配", 0.0), ("50%", 0.5), ("100%", 1.0),
                    ("200%", 2.0), ("400%", 4.0), ("800%", 8.0)]

    closed = Signal()

    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self._session = session
        self._cache = FrameCache(session.frame_paths)
        self._speed = self.DEFAULT_SPEED
        self._playing = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        self.setWindowTitle(f"{session.dir.name} — 帧查看器")
        self.resize(1120, 720)
        self.setMinimumSize(780, 440)
        self._build_ui()
        self._bind_keys()

        self._update_status()
        self._set_index(0, pause=False)
        self.canvas.fit()
        # 应用退出时窗口可能不经 closeEvent 直接销毁，确保缩略图线程先停下
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.strip.shutdown)

    # ---------- 界面 ----------

    @staticmethod
    def _muted(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("muted")
        return lab

    def _build_ui(self):
        # 画布最先创建——下面工具条的信号要连到它
        self.canvas = FrameCanvas(self._cache)
        self.canvas.stepRequested.connect(self._step)
        self.canvas.zoomChanged.connect(self._on_canvas_zoom)

        # 第一行：播放控制 + 帧进度条
        row1 = QWidget()
        h1 = QHBoxLayout(row1)
        h1.setContentsMargins(8, 6, 8, 2)
        self.btn_first = QPushButton()
        self.btn_first.setIcon(icon("skip-back", TEXT, 20))
        self.btn_first.setToolTip("首帧 (Home)")
        self.btn_prev = QPushButton()
        self.btn_prev.setIcon(icon("tri-left", TEXT, 20))
        self.btn_prev.setToolTip("上一帧 (←)")
        self.btn_play = QPushButton()
        self.btn_play.setIcon(icon("play", TEXT, 20))
        self.btn_play.setToolTip("播放 / 暂停（空格，循环播放）")
        self.btn_next = QPushButton()
        self.btn_next.setIcon(icon("tri-right", TEXT, 20))
        self.btn_next.setToolTip("下一帧 (→)")
        self.btn_last = QPushButton()
        self.btn_last.setIcon(icon("skip-fwd", TEXT, 20))
        self.btn_last.setToolTip("末帧 (End)")
        for b in (self.btn_first, self.btn_prev, self.btn_play,
                  self.btn_next, self.btn_last):
            b.setIconSize(QSize(20, 20))
        for b, slot in ((self.btn_first, lambda: self._step_to(0)),
                        (self.btn_prev, lambda: self._step(-1)),
                        (self.btn_play, self._toggle_play),
                        (self.btn_next, lambda: self._step(1)),
                        (self.btn_last, lambda: self._step_to(len(self._cache) - 1))):
            b.setObjectName("transport")
            b.clicked.connect(slot)
            h1.addWidget(b)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setObjectName("frameSlider")  # QSS 里用行动色填充已看过部分
        self.slider.setMinimum(0)
        self.slider.setMaximum(max(0, len(self._cache) - 1))
        self.slider.setMinimumWidth(240)
        self.slider.valueChanged.connect(lambda v: self._set_index(v, from_slider=True))
        self.frame_label = QLabel("0 / 0")
        self.frame_label.setObjectName("frameCounter")
        self.frame_label.setMinimumWidth(90)
        self.frame_label.setAlignment(Qt.AlignCenter)
        h1.addWidget(self.slider, 1)
        h1.addWidget(self.frame_label)

        # 第二行：参数调节 + 导出
        row2 = QWidget()
        h2 = QHBoxLayout(row2)
        h2.setContentsMargins(8, 2, 8, 6)
        self.speed_combo = QComboBox()
        self.speed_combo.addItems([f"{s:g}x" for s in self.SPEEDS])
        self.speed_combo.setCurrentIndex(self.SPEEDS.index(self.DEFAULT_SPEED))
        self.speed_combo.currentIndexChanged.connect(self._on_speed)
        self.zoom_combo = QComboBox()
        self.zoom_combo.addItems([label for label, _ in self.ZOOM_PRESETS])
        self.zoom_combo.activated.connect(self._on_zoom_preset)
        # 洋葱皮层数步进：用 ▲▼ 小按钮而非 QSpinBox——Windows 11 原生样式
        # 会把上下箭头并排摆放且行编辑区盖住 +1 箭头（点不动、悬停 I 型光标）
        self.onion = 0
        self.btn_onion_dec = QPushButton()
        self.btn_onion_dec.setIcon(icon("tri-down", TEXT, 16))
        self.btn_onion_dec.setToolTip("洋葱皮 -1 层（- 键）")
        self.btn_onion_inc = QPushButton()
        self.btn_onion_inc.setIcon(icon("tri-up", TEXT, 16))
        self.btn_onion_inc.setToolTip("洋葱皮 +1 层（+ 键）")
        for b in (self.btn_onion_dec, self.btn_onion_inc):
            b.setObjectName("transport")
            b.setIconSize(QSize(16, 16))
        self.btn_onion_dec.clicked.connect(lambda: self._set_onion(self.onion - 1))
        self.btn_onion_inc.clicked.connect(lambda: self._set_onion(self.onion + 1))
        self.btn_onion_dec.setEnabled(False)   # 0 层时不能再减
        self.onion_label = QLabel("0 层")
        self.alpha_slider = QSlider(Qt.Horizontal)
        self.alpha_slider.setRange(5, 60)
        self.alpha_slider.setValue(30)
        self.alpha_slider.setMaximumWidth(130)
        self.alpha_slider.setToolTip("洋葱皮不透明度 %")
        self.alpha_slider.valueChanged.connect(lambda v: self.canvas.set_alpha(v / 100))
        self.zoom_label = QLabel("适配")
        self.zoom_label.setMinimumWidth(56)
        self.btn_folder = QPushButton(" 打开文件夹")
        self.btn_folder.setIcon(icon("folder", TEXT, 16))
        self.btn_export = QPushButton(" 导出")
        self.btn_export.setIcon(icon("archive", TEXT, 16))
        self.btn_export.setToolTip("导出为 ZIP 压缩包 / GIF 动图 / MP4 视频")
        self.btn_folder.clicked.connect(self._session.open_in_explorer)
        self.btn_export.clicked.connect(self._export_menu)

        h2.addWidget(self._muted("速度"))
        h2.addWidget(self.speed_combo)
        h2.addWidget(self._muted("缩放"))
        h2.addWidget(self.zoom_combo)
        h2.addWidget(self.zoom_label)
        h2.addWidget(self._muted("洋葱皮"))
        h2.addWidget(self.btn_onion_dec)
        h2.addWidget(self.onion_label)
        h2.addWidget(self.btn_onion_inc)
        h2.addWidget(self._muted("不透明度"))
        h2.addWidget(self.alpha_slider)
        h2.addStretch(1)
        h2.addWidget(self.btn_folder)
        h2.addWidget(self.btn_export)

        # 第三行：胶片栏（全部帧的缩略图；单击跳帧、右键删除/插入、拖入图片插入）
        self.strip = FilmStrip()
        self.strip.indexSelected.connect(self._set_index)
        self.strip.deleteRequested.connect(self._delete_frame)
        self.strip.insertRequested.connect(self._insert_images_at)
        self.strip.filesDropped.connect(self._insert_images_at)
        self.strip.set_paths(self._session.frame_paths, self._strip_aspect())
        self.strip_area = QScrollArea()
        self.strip_area.setObjectName("filmstripArea")
        self.strip_area.setWidget(self.strip)
        self.strip_area.setWidgetResizable(True)
        self.strip_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.strip_area.setFrameShape(QFrame.Shape.NoFrame)
        self.strip_area.setFixedHeight(THUMB_H + THUMB_PAD * 2 + 12)

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(row1)
        v.addWidget(row2)
        v.addWidget(self.canvas, 1)
        v.addWidget(self.strip_area)
        self.setCentralWidget(central)

    def _bind_keys(self):
        pairs = [
            (QKeySequence(Qt.Key_Left), lambda: self._step(-1)),
            (QKeySequence(Qt.Key_Right), lambda: self._step(1)),
            (QKeySequence(Qt.Key_Space), self._toggle_play),
            (QKeySequence(Qt.Key_Up), lambda: self._cycle_speed(1)),
            (QKeySequence(Qt.Key_Down), lambda: self._cycle_speed(-1)),
            (QKeySequence(Qt.Key_0), self.canvas.fit),
            (QKeySequence(Qt.Key_1), lambda: self.canvas.set_zoom(1)),
            (QKeySequence(Qt.Key_2), lambda: self.canvas.set_zoom(2)),
            (QKeySequence(Qt.Key_4), lambda: self.canvas.set_zoom(4)),
            (QKeySequence(Qt.Key_Plus), lambda: self._set_onion(self.onion + 1)),
            (QKeySequence(Qt.Key_Equal), lambda: self._set_onion(self.onion + 1)),
            (QKeySequence(Qt.Key_Minus), lambda: self._set_onion(self.onion - 1)),
            (QKeySequence(Qt.Key_Home), lambda: self._step_to(0)),
            (QKeySequence(Qt.Key_End), lambda: self._step_to(len(self._cache) - 1)),
            (QKeySequence(Qt.Key_Delete),
             lambda: self._delete_frame(self.canvas.index)),
            (QKeySequence(Qt.Key_O), self._session.open_in_explorer),
        ]
        for seq, slot in pairs:
            QShortcut(seq, self, slot)

    # ---------- 洋葱皮 ----------

    def _set_onion(self, n: int):
        n = max(0, min(5, n))
        self.onion = n
        self.onion_label.setText(f"{n} 层")
        self.btn_onion_dec.setEnabled(n > 0)
        self.btn_onion_inc.setEnabled(n < 5)
        self.canvas.set_onion(n)

    # ---------- 播放 ----------

    def _interval(self) -> int:
        return max(1, round(1000 / (self._session.fps * self._speed)))

    def _tick(self):
        n = len(self._cache)
        if n == 0:
            self._toggle_play()
            return
        self._set_index((self.canvas.index + 1) % n, pause=False, from_slider=False)

    def _toggle_play(self):
        if len(self._cache) < 2:
            return
        self._playing = not self._playing
        if self._playing:
            self._timer.setInterval(self._interval())
            self._timer.start()
            self.btn_play.setIcon(icon("pause", TEXT, 20))
        else:
            self._timer.stop()
            self.btn_play.setIcon(icon("play", TEXT, 20))

    # ---------- 帧导航 ----------

    def _set_index(self, i: int, pause: bool = True, from_slider: bool = False):
        n = len(self._cache)
        if n == 0:
            self.frame_label.setText("无帧")
            return
        i = max(0, min(i, n - 1))
        if pause and self._playing:
            self._toggle_play()
        self.canvas.set_index(i)
        self.strip.set_current(i)
        if not from_slider:
            self.slider.blockSignals(True)
            self.slider.setValue(i)
            self.slider.blockSignals(False)
        self.frame_label.setText(f"{i + 1} / {n}")

    def _step(self, delta: int):
        self._set_index(self.canvas.index + delta)

    def _step_to(self, i: int):
        self._set_index(i)

    # ---------- 帧编辑（胶片栏：删除 / 插入） ----------

    def _strip_aspect(self) -> float:
        r = self._session.region or {}
        if r.get("width") and r.get("height"):
            return r["width"] / r["height"]
        return 4 / 3

    def _delete_frame(self, i: int):
        paths = self._session.frame_paths
        if not 0 <= i < len(paths):
            return
        try:
            _retry_fs(paths[i].unlink)  # 直接删磁盘文件，无撤销——按 Del 前看清楚
        except OSError as exc:
            QMessageBox.warning(self, "删除失败", f"无法删除文件：\n{exc}")
            return
        del paths[i]
        self._paths_changed(prefer=i)  # 跳到被删帧的后一帧

    def _insert_images_at(self, pos: int, files: list | None = None):
        """把外部图片插入序列。pos = 插入位置（插到第 pos 帧之前）。"""
        if files is None:  # 来自右键菜单：弹文件选择框
            files, _ = QFileDialog.getOpenFileNames(
                self, "选择要插入的图片", str(self._session.dir),
                "图片 (*.png *.jpg *.jpeg);;所有文件 (*)")
        files = [f for f in files
                 if Path(f).suffix.lower() in FilmStrip._IMG_EXTS]
        if not files:
            return
        paths = self._session.frame_paths
        pos = max(0, min(pos, len(paths)))
        # 先复制成 .insert_ 前缀（rescan 的 frame_* 匹配不到，中途失败不留半成品）
        staged: list[Path] = []
        try:
            for k, f in enumerate(files):
                src = Path(f)
                ext = (".jpg" if src.suffix.lower() in (".jpg", ".jpeg")
                       else ".png")
                dst = self._session.dir / f".insert_{pos + k:06d}{ext}"
                shutil.copy2(src, dst)
                staged.append(dst)
        except OSError as exc:
            for p in staged:
                p.unlink(missing_ok=True)
            QMessageBox.warning(self, "插入失败", f"无法复制图片：\n{exc}")
            return
        new_paths = list(paths)
        new_paths[pos:pos] = staged
        try:
            self._session.frame_paths = self._renumber(new_paths)
        except OSError as exc:
            QMessageBox.warning(self, "插入失败", f"无法重排帧文件：\n{exc}")
            return
        self._paths_changed(prefer=pos, pause=False)  # 跳到第一张插入的图

    def _renumber(self, paths: list[Path]) -> list[Path]:
        """两阶段重命名：先全部改成 .tmp_ 前缀（此时目标名可能仍被占用），
        再按给定顺序改回 frame_%06d 连续编号。"""
        tmps = []
        for i, p in enumerate(paths):
            t = p.with_name(f".tmp_{i:06d}{p.suffix}")
            _retry_fs(lambda p=p, t=t: p.rename(t))
            tmps.append(t)
        final = []
        for i, t in enumerate(tmps):
            f = self._session.dir / f"frame_{i:06d}{t.suffix}"
            _retry_fs(lambda t=t, f=f: t.rename(f))
            final.append(f)
        return final

    def _paths_changed(self, prefer: int, pause: bool = True):
        """删除/插入后统一收口：元数据、缓存、滑块、胶片栏、状态栏同步。"""
        paths = self._session.frame_paths
        n = len(paths)
        self._session.write_metadata()
        self._cache.set_paths(paths)
        self.strip.set_paths(paths, self._strip_aspect())
        self.slider.setMaximum(max(0, n - 1))
        self._update_status()
        self._set_index(min(prefer, n - 1) if n else 0, pause=pause)

    def _update_status(self):
        n = len(self._cache)
        self.statusBar().showMessage(
            f"{n} 帧 · {self._session.fps} fps · {self._session.dir}    "
            f"←/→ 逐帧 · 空格 播放 · ↑/↓ 变速 · Ctrl+滚轮 缩放 · "
            f"+/- 洋葱皮 · Del 删除帧"
        )

    # ---------- 参数 ----------

    def _on_speed(self):
        self._speed = self.SPEEDS[self.speed_combo.currentIndex()]
        if self._playing:
            self._timer.setInterval(self._interval())

    def _cycle_speed(self, direction: int):
        """↑/↓ 在速度档位间切换（超出两端则停在最档）。"""
        i = self.speed_combo.currentIndex() + direction
        i = max(0, min(i, len(self.SPEEDS) - 1))
        self.speed_combo.setCurrentIndex(i)  # 触发 _on_speed 更新播放间隔

    def _on_zoom_preset(self, index: int):
        _, z = self.ZOOM_PRESETS[index]
        if z == 0:
            self.canvas.fit()
        else:
            self.canvas.set_zoom(z)

    def _on_canvas_zoom(self, zoom: float, fit: bool):
        self.zoom_label.setText("适配" if fit else f"{zoom * 100:.0f}%")

    # ---------- 导出 ----------

    def _export_menu(self):
        m = QMenu(self)
        m.addAction("ZIP 压缩包", self._export_zip)
        m.addAction("GIF 动图", self._export_gif)
        m.addAction("MP4 视频", self._export_mp4)
        m.exec(self.btn_export.mapToGlobal(QPoint(0, self.btn_export.height())))

    def _export_zip(self):
        dest, _ = QFileDialog.getSaveFileName(
            self, "导出 ZIP", f"{self._session.dir.name}.zip", "ZIP (*.zip)")
        if dest:
            self._run_export(dest, self._session.zip_to)

    def _export_gif(self):
        dest, _ = QFileDialog.getSaveFileName(
            self, "导出 GIF", f"{self._session.dir.name}.gif", "GIF (*.gif)")
        if dest:
            self._run_export(dest, self._session.gif_to)

    def _export_mp4(self):
        dest, _ = QFileDialog.getSaveFileName(
            self, "导出 MP4", f"{self._session.dir.name}.mp4", "MP4 (*.mp4)")
        if dest:
            self._run_export(dest, self._session.mp4_to)

    def _run_export(self, dest: str, job):
        """同步执行导出：编码期间显示等待光标（长会话可能需要几秒）。"""
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            job(dest)
        except (OSError, RuntimeError) as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
        else:
            QMessageBox.information(self, "导出完成", f"已导出到：\n{dest}")
        finally:
            QApplication.restoreOverrideCursor()

    def closeEvent(self, e):
        self._timer.stop()
        self.strip.shutdown()  # 停缩略图线程，避免窗口销毁时线程仍在跑
        self.closed.emit()
        super().closeEvent(e)
