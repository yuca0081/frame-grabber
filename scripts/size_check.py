"""对比 PNG / JPEG 两种格式录制同样内容的体积。

运行：.venv/Scripts/python.exe scripts/size_check.py
（屏幕上会闪现悬浮条约 6 秒，各录 2 秒）
"""
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from framegrabber.recorder import FloatingBar

REGION = {"left": 100, "top": 100, "width": 800, "height": 450}


def main():
    app = QApplication(sys.argv)
    state = {"bar": None, "results": [], "pending": ["PNG 无损", "JPEG 小体积"]}

    def start_next():
        # 上一轮 bar 已完全结束（延迟调用），再创建下一个
        if not state["pending"]:
            app.quit()
            return
        label = state["pending"].pop(0)
        bar = FloatingBar(REGION)
        state["bar"] = bar  # 持引用，防止回调期间被回收
        bar.show()

        def start():
            bar.fmt_combo.setCurrentIndex(bar.fmt_combo.findText(label))
            bar._start()

        def on_stopped(session):
            state["bar"] = None
            if session is not None:
                total = sum(p.stat().st_size for p in session.frame_paths)
                state["results"].append((label, session.frame_count, total,
                                         session.dir))
            # 延迟到下一轮事件循环，避免在 stopped 信号回调里叠窗口
            QTimer.singleShot(150, start_next)

        bar.stopped.connect(on_stopped)
        QTimer.singleShot(400, start)
        QTimer.singleShot(2500, bar._request_finish)

    QTimer.singleShot(300, start_next)
    QTimer.singleShot(30000, app.quit)  # 兜底
    app.exec()

    for label, n, total, d in state["results"]:
        print(f"{label}: {n} 帧, 总计 {total/1024/1024:.1f} MB, "
              f"平均 {total/n/1024:.0f} KB/帧 -> {d}")
    if len(state["results"]) == 2:
        a, b = state["results"]
        print(f"PNG/JPEG 体积比: {a[2] / b[2]:.2f}x")


if __name__ == "__main__":
    main()
