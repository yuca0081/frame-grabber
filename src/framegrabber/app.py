"""应用入口 + 启动器窗口。

用法：python -m framegrabber
流程：新建录制 → 全屏框选 → 悬浮条上选帧率并录制 → 停止后自动打开帧查看器。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from framegrabber.icons import icon
from framegrabber.recorder import FloatingBar
from framegrabber.selector import RegionSelector
from framegrabber.session import Session
from framegrabber.theme import APP_QSS, GREEN_TEXT, TEXT
from framegrabber.viewer import ViewerWindow


def _muted_label(text: str) -> QLabel:
    """次要文字标签（统一灰色，样式表按 objectName 命中）。"""
    lab = QLabel(text)
    lab.setObjectName("muted")
    return lab


class LauncherWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FrameGrabber — 逐帧动作参考")
        self.resize(430, 500)
        self._settings = QSettings("FrameGrabber", "FrameGrabber")
        self._bar: FloatingBar | None = None
        # 持有查看器引用：无父窗口的窗口若只剩 C++ 侧引用，可能被 Python 回收
        self._viewers: list[ViewerWindow] = []

        title = QLabel("FrameGrabber")
        title.setObjectName("title")
        sub = QLabel("框选屏幕区域录成帧序列，逐帧分析视频 / 动图里的动作")
        sub.setObjectName("muted")
        sub.setWordWrap(True)

        self.btn_new = QPushButton("  新建录制")
        self.btn_new.setObjectName("primaryAction")
        self.btn_new.setIcon(icon("camera", GREEN_TEXT, 20))
        self.btn_new.setMinimumHeight(42)
        self.btn_open = QPushButton("  打开会话…")
        self.btn_open.setIcon(icon("folder", TEXT, 18))
        self.btn_open.setMinimumHeight(34)
        self.btn_new.clicked.connect(self._new_recording)
        self.btn_open.clicked.connect(self._open_dialog)

        # 存储位置：当前路径 + 更改 + 打开
        storage_row = QWidget()
        sh = QHBoxLayout(storage_row)
        sh.setContentsMargins(0, 0, 0, 0)
        sh.setSpacing(6)
        self.storage_label = QLabel()
        self.storage_label.setObjectName("storagePath")
        btn_change = QPushButton("更改…")
        btn_open_store = QPushButton("打开")
        btn_change.clicked.connect(self._change_storage)
        btn_open_store.clicked.connect(self._open_storage)
        sh.addWidget(_muted_label("存储位置"))
        sh.addWidget(self.storage_label, 1)
        sh.addWidget(btn_change)
        sh.addWidget(btn_open_store)
        self._refresh_storage_label()

        # 最近会话标题行 + 清除按钮
        recent_header = QWidget()
        rh = QHBoxLayout(recent_header)
        rh.setContentsMargins(0, 0, 0, 0)
        rh.addWidget(_muted_label("最近会话（双击打开）"))
        rh.addStretch(1)
        btn_clear = QPushButton("清除")
        btn_clear.setFlat(True)
        btn_clear.clicked.connect(self._clear_recent)
        rh.addWidget(btn_clear)

        self.recent = QListWidget()
        self.recent.itemDoubleClicked.connect(self._open_recent_item)
        self._refresh_recent()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(10)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addSpacing(8)
        lay.addWidget(self.btn_new)
        lay.addWidget(self.btn_open)
        lay.addWidget(storage_row)
        lay.addSpacing(6)
        lay.addWidget(recent_header)
        lay.addWidget(self.recent, 1)

    # ---------- 录制流程 ----------

    def _new_recording(self):
        self.hide()
        rect = RegionSelector.pick()
        if rect is None:
            self.show()
            return
        self._bar = FloatingBar(rect, storage_root=self._storage_root())
        self._bar.stopped.connect(self._on_stopped)
        self._bar.show()
        self._bar.activateWindow()  # 拿到键盘焦点，ESC 立即可用

    def _on_stopped(self, session):
        self._bar = None
        if session is None:
            self.show()
            return
        self._open_viewer(session)

    # ---------- 打开会话 ----------

    def _open_dialog(self):
        path = QFileDialog.getExistingDirectory(self, "选择会话文件夹")
        if path:
            self._try_open(path)

    def _open_recent_item(self, item: QListWidgetItem):
        self._try_open(item.data(Qt.UserRole) or item.text())

    def _try_open(self, path: str):
        try:
            session = Session.open(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "打不开", str(exc))
            return
        self._open_viewer(session)

    def _open_viewer(self, session: Session):
        viewer = ViewerWindow(session)
        viewer.closed.connect(lambda v=viewer: self._forget_viewer(v))
        self._viewers.append(viewer)
        self._push_recent(str(session.dir))
        viewer.show()
        self.hide()

    def _forget_viewer(self, viewer: ViewerWindow):
        if viewer in self._viewers:
            self._viewers.remove(viewer)
        self.show()

    # ---------- 存储位置 ----------

    def _storage_root(self) -> Path:
        p = self._settings.value("storage_dir", "")
        return Path(p) if p else Session.DEFAULT_ROOT

    def _refresh_storage_label(self):
        self.storage_label.setText(str(self._storage_root()))

    def _change_storage(self):
        path = QFileDialog.getExistingDirectory(
            self, "选择会话存储位置", str(self._storage_root()))
        if not path:
            return
        try:
            Path(path).mkdir(parents=True, exist_ok=True)  # 顺手验证可写
        except OSError as exc:
            QMessageBox.warning(self, "路径不可用",
                                f"无法创建或访问该文件夹：\n{exc}")
            return
        self._settings.setValue("storage_dir", path)
        self._refresh_storage_label()

    def _open_storage(self):
        root = self._storage_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if hasattr(os, "startfile"):
            os.startfile(root)

    # ---------- 最近列表 ----------

    def _recent_paths(self) -> list[str]:
        val = self._settings.value("recent", [])
        return list(val) if isinstance(val, list) else []

    def _push_recent(self, path: str):
        paths = [p for p in self._recent_paths() if p != path]
        paths.insert(0, path)
        self._settings.setValue("recent", paths[:12])
        self._refresh_recent()

    def _refresh_recent(self):
        self.recent.clear()
        for p in self._recent_paths():
            if not os.path.isdir(p):  # 会话文件夹已删除的不再显示
                continue
            item = QListWidgetItem(p)
            item.setData(Qt.UserRole, p)
            self.recent.addItem(item)

    def _clear_recent(self):
        self._settings.setValue("recent", [])
        self._refresh_recent()

    def closeEvent(self, e):
        # 应用生命周期由启动器管理：关掉启动器 = 退出整个程序
        QApplication.instance().quit()
        super().closeEvent(e)


def main():
    # Windows 125%/150% 缩放下保留精确比例，避免选区和截图错位
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # 窗口都由启动器统一管理
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setStyleSheet(APP_QSS)
    app.setWindowIcon(icon("camera", "#39c5ff", 32))
    launcher = LauncherWindow()
    launcher.show()
    sys.exit(app.exec())
