"""帧查看器：逐帧步进、洋葱皮叠加、变速播放、无损缩放。

内存策略：一次 60fps × 1 分钟的录制有 3600 张 PNG，全部解码约几个 GB，
所以 FrameCache 惰性解码 + LRU 按字节上限淘汰，并在步进时预加载邻帧。
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel, QMainWindow,
    QMenu, QMessageBox, QPushButton, QSlider, QVBoxLayout, QWidget,
)

from framegrabber.icons import icon
from framegrabber.session import Session
from framegrabber.theme import CANVAS_BG, MUTED, TEXT


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

        n = len(self._cache)
        self.statusBar().showMessage(
            f"{n} 帧 · {session.fps} fps · {session.dir}    "
            f"←/→ 逐帧 · 空格 播放 · ↑/↓ 变速 · Ctrl+滚轮 缩放 · +/- 洋葱皮"
        )
        self._set_index(0, pause=False)
        self.canvas.fit()

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

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(row1)
        v.addWidget(row2)
        v.addWidget(self.canvas, 1)
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
        if not from_slider:
            self.slider.blockSignals(True)
            self.slider.setValue(i)
            self.slider.blockSignals(False)
        self.frame_label.setText(f"{i + 1} / {n}")

    def _step(self, delta: int):
        self._set_index(self.canvas.index + delta)

    def _step_to(self, i: int):
        self._set_index(i)

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
        self.closed.emit()
        super().closeEvent(e)
