"""真实平台下验证完整录制链路：悬浮条 → 开始 → 停止 → 收到会话。

同时验证：
    1. 停止后回传的会话已包含帧（不需要手动 rescan）
    2. 选区周围有蓝色边框标记（截屏核对，且不进入录制画面）
    3. 信息栏显示实测帧率而非名义帧率

运行：.venv/Scripts/python.exe scripts/rec_check.py
（屏幕上会闪现悬浮条约 4 秒，并录制屏幕一角 1.5 秒）
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import mss  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from framegrabber.recorder import FloatingBar  # noqa: E402
from framegrabber.viewer import ViewerWindow  # noqa: E402

OUT = ROOT / "build"
MARGIN = 40  # 截屏时在选区四周多留的边


def main():
    app = QApplication(sys.argv)
    region = {"left": 100, "top": 100, "width": 400, "height": 250}
    bar = FloatingBar(region)
    bar.show()

    result = {}
    bar.stopped.connect(lambda s: (result.__setitem__("session", s), app.quit()))

    def start_rec():
        # 截一块带余量的屏幕，核对选区边框标记 + 悬浮条
        with mss.MSS() as sct:
            shot = {"left": region["left"] - MARGIN, "top": region["top"] - MARGIN,
                    "width": region["width"] + MARGIN * 2 + 300,
                    "height": region["height"] + MARGIN * 2 + 60}
            mss.tools.to_png(sct.grab(shot).rgb,
                             (shot["width"], shot["height"]),
                             output=str(OUT / "ui_region.png"))
        bar._start()
        QTimer.singleShot(600, lambda: bar.grab().save(str(OUT / "ui_bar.png")))

    def stop_rec():
        bar._request_finish()

    QTimer.singleShot(800, start_rec)
    QTimer.singleShot(2300, stop_rec)
    QTimer.singleShot(8000, app.quit)  # 兜底：卡住也能退出
    app.exec()

    s = result.get("session")
    if s is None:
        print("REC CHECK FAIL: no session returned")
        sys.exit(1)
    # 注意：这里刻意不做 rescan——直接验证"停止后打开查看器就有帧"
    print(f"stopped session frames: {s.frame_count}")
    assert s.frame_count >= 5, "停止后会话里没有帧（rescan 修复失效）"

    v = ViewerWindow(s)
    v.show()
    app.processEvents()
    ok = v.frame_label.text() == f"1 / {s.frame_count}"
    v.grab().save(str(OUT / "ui_viewer_after_rec.png"))
    v.close()
    assert ok, f"查看器未显示帧：{v.frame_label.text()}"
    print(f"REC CHECK OK: {s.frame_count} frames @ {s.fps}fps, viewer ok -> {s.dir}")


if __name__ == "__main__":
    main()
