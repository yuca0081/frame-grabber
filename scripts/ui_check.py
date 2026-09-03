"""真实平台（非 offscreen）下截取启动器与查看器截图，核对中文渲染。

运行：.venv/Scripts/python.exe scripts/ui_check.py
（会在屏幕上闪现窗口约 1.2 秒）
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # 为了导入 tests 包（包内代码用可编辑安装）

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from framegrabber.app import LauncherWindow  # noqa: E402
from framegrabber.session import Session  # noqa: E402
from framegrabber.viewer import ViewerWindow  # noqa: E402
from tests.fake_frames import make_fake_session  # noqa: E402

OUT = ROOT / "build"


def main():
    app = QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="fg_ui_"))

    launcher = LauncherWindow()
    launcher.show()

    session = Session.open(make_fake_session(tmp))
    viewer = ViewerWindow(session)
    viewer.show()
    viewer.onion_spin.setValue(2)

    def shot():
        OUT.mkdir(exist_ok=True)
        launcher.grab().save(str(OUT / "ui_launcher.png"))
        viewer.grab().save(str(OUT / "ui_viewer.png"))
        app.quit()

    QTimer.singleShot(1200, shot)
    app.exec()


if __name__ == "__main__":
    main()
