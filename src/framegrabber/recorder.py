"""录制模块：CaptureWorker 工作线程 + FloatingBar 悬浮控制条。

线程模型：
    mss 实例有线程亲和性——必须在截屏线程里创建和销毁，
    所以 CaptureWorker.run() 里完成从创建到关闭的完整生命周期。
    PNG 存盘也在工作线程做，磁盘 IO 不阻塞界面；
    跨线程只传 (帧号, 累计秒数) 这样的轻量信号。
"""
from __future__ import annotations

import time
from pathlib import Path

import mss
from PIL import Image
from PySide6.QtCore import QObject, QPoint, QSize, QThread, Qt, Signal, Slot
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, \
    QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget,
)

from framegrabber.icons import icon
from framegrabber.session import Session
from framegrabber.theme import ACCENT, GREEN_TEXT, INFO_SKY, MUTED, PAUSE_AMBER, REC_RED, TEXT


def _screen_for_region(region: dict):
    """找到录制区域所在的 Qt 屏幕（区域是物理像素，屏幕几何是逻辑像素）。"""
    cx = region["left"] + region["width"] / 2
    cy = region["top"] + region["height"] / 2
    for s in QGuiApplication.screens():
        d = s.devicePixelRatio()
        g = s.geometry()
        if (g.left() * d <= cx < (g.left() + g.width()) * d
                and g.top() * d <= cy < (g.top() + g.height()) * d):
            return s
    return QGuiApplication.primaryScreen()


class _RegionMarker(QWidget):
    """选区标记：围绕录制区域画一圈蓝色边框。

    边框画在选区外侧的留白里，mss 只截选区内部，所以不会被录进画面；
    窗口内部完全透明，Windows 对分层窗口的透明像素自动鼠标穿透，
    点到选区里的视频/网页不受影响。
    """

    PAD = 3  # 边框与选区的间距（逻辑像素）

    def __init__(self, region: dict, parent=None):
        super().__init__(
            parent,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool,
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        d = _screen_for_region(region).devicePixelRatio()
        self.setGeometry(
            round(region["left"] / d - self.PAD),
            round(region["top"] / d - self.PAD),
            round(region["width"] / d + self.PAD * 2),
            round(region["height"] / d + self.PAD * 2),
        )
        self.show()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setPen(QPen(QColor(ACCENT), 2))
        p.drawRect(self.rect().adjusted(0, 0, -1, -1))


class CaptureWorker(QObject):
    """在工作线程里按固定帧率截屏并存 PNG。"""

    frameSaved = Signal(int, float)  # 帧号, 自录制起累计秒数（已扣除暂停）
    resynced = Signal()              # 落后太多重置了时钟（磁盘繁忙等）
    error = Signal(str)
    finished = Signal(int)           # 总帧数

    def __init__(self, rect: dict, fps: int, session: Session):
        super().__init__()
        self._rect = dict(rect)
        self._fps = fps
        self._session = session
        self._jpg = session.format == "jpg"
        self._stop = False
        self._paused = False

    # 停止/暂停由 GUI 线程直接调用（普通方法而非排队信号）：
    # run() 是长循环，占住了本线程的事件循环，排队投递的槽永远收不到；
    # Python 属性赋值在 GIL 下是原子的，跨线程直接设标志位安全。
    def request_stop(self):
        self._stop = True

    def set_paused(self, paused: bool):
        self._paused = paused

    def _save_frame(self, img: Image.Image, index: int):
        """按会话格式存盘。

        PNG（默认，无损）：屏幕内容 ≤256 色时自动存索引色 PNG——
            无损且小得多（干净利落的色块内容会命中；带视频噪点的内容不会）。
        JPEG：体积小、编码快约 7 倍（大区域高帧率时能保住帧率），
            但有损，放大有压缩痕迹。
        """
        path = self._session.new_frame_path(index)
        if self._jpg:
            img.save(path, quality=90)
        else:
            if img.getcolors(256) is not None:
                img = img.convert("P", palette=Image.ADAPTIVE, colors=256,
                                  dither=Image.Dither.NONE)
            img.save(path, compress_level=6)

    @Slot()
    def run(self):
        i = 0
        try:
            with mss.MSS() as sct:
                t0 = time.perf_counter()
                while not self._stop:
                    # 暂停：轻量轮询。恢复时把时间基准平移掉暂停时长，
                    # 否则"已录时长 = 帧数/帧率"会对不上
                    if self._paused:
                        pause_start = time.perf_counter()
                        while self._paused and not self._stop:
                            time.sleep(0.05)
                        t0 += time.perf_counter() - pause_start

                    # 绝对截止时间调度：deadline = 起点 + 第 n 帧 / fps
                    # （每轮 sleep(interval) 会累积漂移，这里不会）
                    deadline = t0 + (i + 1) / self._fps
                    now = time.perf_counter()
                    if now < deadline:
                        time.sleep(deadline - now)
                    elif now - deadline > 2 / self._fps:
                        # 落后太多（磁盘卡顿等）：重置时钟，下一帧立即截，
                        # 而不是把欠的帧一口气全补出来
                        t0 = time.perf_counter() - i / self._fps
                        deadline = t0 + (i + 1) / self._fps
                        self.resynced.emit()

                    raw = sct.grab(self._rect)
                    # mss 返回 BGRA；"BGRX" 解码跳过 alpha 并换回 RGB 顺序，
                    # 直接按 RGB 读会红蓝互换
                    img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                    self._save_frame(img, i)
                    self.frameSaved.emit(i, time.perf_counter() - t0)
                    i += 1
        except Exception as exc:
            self.error.emit(f"录制出错：{exc}")
        finally:
            self._session.write_metadata(i)
            self.finished.emit(i)


class FloatingBar(QWidget):
    """悬浮控制条：选帧率 → 录制 → 暂停/停止。停在选区旁边，不会把自己录进去。

    信号 stopped(Session 或 None) 在录制结束/取消时发出：
    录到至少 1 帧传 Session，否则传 None。
    """

    stopped = Signal(object)

    FPS_CHOICES = ["10", "15", "30", "60"]

    def __init__(self, region: dict, storage_root: Path | None = None, parent=None):
        super().__init__(
            parent,
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool,
        )
        self._region = region
        self._storage = storage_root  # 会话存储根目录（None = 默认位置）
        self._session: Session | None = None
        self._thread: QThread | None = None
        self._worker: CaptureWorker | None = None
        self._fps: int | None = None  # 点录制时记下，之后不再从界面文本取
        self._resyncs = 0
        self._recording = False
        self._paused = False
        self._finishing = False
        self._drag_off: QPoint | None = None
        self._marker: _RegionMarker | None = None

        self.setWindowTitle("FrameGrabber 录制")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(8)

        self.info = QLabel()
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(self.FPS_CHOICES)
        self.fps_combo.setCurrentText("30")
        self.fps_combo.setToolTip(
            "录制帧率（每秒截多少张）\n体积与帧率成正比：分析动作通常 10~15 就够")
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItems(["PNG 无损", "JPEG 小体积"])
        self.fmt_combo.setToolTip(
            "PNG：无损，放大不糊\n"
            "JPEG：体积小、录制压力小，但有损")
        self.btn_record = QPushButton(" 录制")
        self.btn_record.setObjectName("primaryAction")
        self.btn_record.setIcon(icon("record", GREEN_TEXT, 16))
        self.btn_pause = QPushButton()
        self.btn_pause.setIcon(icon("pause", TEXT, 20))
        self.btn_pause.setIconSize(QSize(20, 20))
        self.btn_pause.setToolTip("暂停 / 继续")
        self.btn_stop = QPushButton()
        self.btn_stop.setIcon(icon("stop", TEXT, 20))
        self.btn_stop.setIconSize(QSize(20, 20))
        self.btn_stop.setToolTip("停止并打开查看器")
        self.btn_close = QPushButton()
        self.btn_close.setIcon(icon("close", MUTED, 14))
        self.btn_close.setIconSize(QSize(14, 14))
        self.btn_record.setCursor(Qt.PointingHandCursor)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.btn_close.setToolTip("取消（不保存）")
        self.btn_close.setFlat(True)

        lay.addWidget(self.info)
        lay.addStretch(1)
        lay.addWidget(self._muted("帧率"))
        lay.addWidget(self.fps_combo)
        lay.addWidget(self._muted("格式"))
        lay.addWidget(self.fmt_combo)
        lay.addWidget(self.btn_record)
        lay.addWidget(self.btn_pause)
        lay.addWidget(self.btn_stop)
        lay.addWidget(self.btn_close)

        self.btn_record.clicked.connect(self._start)
        self.btn_pause.clicked.connect(self._toggle_pause)
        self.btn_stop.clicked.connect(self._request_finish)
        self.btn_close.clicked.connect(self._cancel)
        # 框选完成后按 ESC 取消（未录制=丢弃，已录制=停止并保存，同关闭按钮）
        QShortcut(QKeySequence(Qt.Key_Escape), self, self._cancel)
        self._set_info(f"区域 {region['width']}×{region['height']}", INFO_SKY)

        self.adjustSize()
        self._position()
        self._marker = _RegionMarker(region)  # 选区边框标记，随控制条关闭

    # ---------- 布局 ----------

    @staticmethod
    def _muted(text: str) -> QLabel:
        lab = QLabel(text)
        lab.setObjectName("muted")
        return lab

    def _position(self):
        """把控制条停在选区正下方（空间不够则上方）。"""
        s = _screen_for_region(self._region)
        d = s.devicePixelRatio()
        avail = s.availableGeometry()
        w, h = self.width(), self.height()
        top_logical = self._region["top"] / d
        bottom_logical = (self._region["top"] + self._region["height"]) / d
        x = min(max(self._region["left"] / d, avail.left()),
                avail.right() - w)
        y = bottom_logical + 8
        if y + h > avail.bottom():
            y = max(top_logical - h - 8, avail.top())
        self.move(int(x), int(y))

    # ---------- 录制流程 ----------

    def _start(self):
        if self._recording:
            return
        fps = int(self.fps_combo.currentText())
        fmt = "jpg" if self.fmt_combo.currentText().startswith("JPEG") else "png"
        self._fps = fps
        self._session = Session.create(fps, self._region, fmt, root=self._storage)

        self._thread = QThread(self)
        self._worker = CaptureWorker(self._region, fps, self._session)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.frameSaved.connect(self._on_frame)
        self._worker.resynced.connect(self._on_resync)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_finished)
        self._thread.start()

        self._recording = True
        self.fps_combo.setEnabled(False)
        self.fmt_combo.setEnabled(False)
        self.btn_record.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_stop.setEnabled(True)
        self._set_info("录制中…", REC_RED)

    def _toggle_pause(self):
        self._paused = not self._paused
        if self._worker is not None:
            self._worker.set_paused(self._paused)
        self.btn_pause.setIcon(icon("play" if self._paused else "pause", TEXT, 20))
        self.btn_pause.setToolTip("继续" if self._paused else "暂停")
        self._set_info("已暂停" if self._paused else "录制中…",
                       PAUSE_AMBER if self._paused else REC_RED)

    def _request_finish(self):
        if self._recording and not self._finishing:
            self._finishing = True
            self.btn_pause.setEnabled(False)
            self.btn_stop.setEnabled(False)
            self._set_info("正在结束…", INFO_SKY)
            if self._worker is not None:
                self._worker.request_stop()

    def _cancel(self):
        if self._recording:
            self._request_finish()  # 录过了就按停止处理，保存已录内容
        else:
            self._emit_stopped(None)

    def _on_frame(self, index: int, elapsed: float):
        # 实际帧率 = 已收帧数 / 实测累计时长（而非名义帧率），磁盘卡顿时不会虚报
        actual = (index + 1) / elapsed if elapsed > 0 else 0.0
        extra = f" · 时钟重置×{self._resyncs}" if self._resyncs else ""
        self._set_info(
            f"已录 {index + 1} 帧 · {elapsed:.1f}s · 实际 {actual:.0f} fps{extra}",
            INFO_SKY,
        )

    def _on_resync(self):
        self._resyncs += 1

    def _on_error(self, msg: str):
        self._set_info(msg, REC_RED)

    def _on_finished(self, total: int):
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)
            self._thread = None
            self._worker = None
        session = self._session if total > 0 else None
        if session is not None:
            session.rescan()  # 帧列表是创建会话时空扫的，此刻磁盘上才有帧
        self._session = None
        self._emit_stopped(session)

    def _emit_stopped(self, session):
        if self._marker is not None:
            self._marker.close()
            self._marker.deleteLater()
            self._marker = None
        self.stopped.emit(session)
        self.close()
        self.deleteLater()

    def _set_info(self, text: str, color: str = "#e3e5e8"):
        self.info.setText(f'<span style="color:{color}">{text}</span>')

    # ---------- 拖动控制条 ----------

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_off = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_off is not None:
            self.move(e.globalPosition().toPoint() - self._drag_off)

    def mouseReleaseEvent(self, _):
        self._drag_off = None
