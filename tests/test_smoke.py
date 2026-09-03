"""冒烟测试：不打开真实窗口，离屏验证各模块能跑通。

运行（在项目根目录）：
    QT_QPA_PLATFORM=offscreen .venv/Scripts/python.exe -m tests.test_smoke

覆盖：
    1. Session 元数据读写与帧扫描
    2. 查看器：构造、步进、洋葱皮、缩放、播放 tick、渲染截图
    3. CaptureWorker：真实 mss 截屏 + PNG 存盘 + 停止
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:  # 兼容"python -m tests.test_smoke"和"python tests/test_smoke.py"两种跑法
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from PIL import Image  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    from tests.fake_frames import make_fake_session
except ImportError:
    from fake_frames import make_fake_session

from framegrabber.recorder import CaptureWorker  # noqa: E402
from framegrabber.session import Session  # noqa: E402
from framegrabber.viewer import ViewerWindow  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "build"
OUT.mkdir(exist_ok=True)
RESULTS = []


def check(name: str, cond: bool, extra: str = ""):
    RESULTS.append((name, bool(cond)))
    mark = "PASS" if cond else "FAIL"
    print(f"[{mark}] {name}" + (f"  ({extra})" if extra else ""))


def test_session(tmp: Path):
    print("\n== Session ==")
    s = Session.open(make_fake_session(tmp))
    check("打开会话，帧数正确", s.frame_count == 20, f"{s.frame_count}")
    check("读取元数据 fps", s.fps == 12)

    empty = tmp / "session_empty"
    empty.mkdir()
    try:
        Session.open(empty)
        check("空文件夹应报错", False)
    except FileNotFoundError:
        check("空文件夹应报错", True)

    bad_fps = tmp / "session_badfps"
    bad_fps.mkdir()
    (bad_fps / "frame_000000.png").write_bytes(b"")
    (bad_fps / "session.json").write_text('{"fps": null}', "utf-8")
    check("fps 为 null 时兜底 30", Session.open(bad_fps).fps == 30)

    dest = tmp / "export.zip"
    s.zip_to(dest)
    check("导出 zip", dest.exists() and dest.stat().st_size > 1000)

    gif = tmp / "export.gif"
    s.gif_to(gif)
    gimg = Image.open(gif)
    check("导出 GIF 帧数", getattr(gimg, "n_frames", 1) == s.frame_count,
          f"{getattr(gimg, 'n_frames', 1)}")
    check("GIF 无限循环", gimg.info.get("loop") == 0)
    d = gimg.info.get("duration")
    check("GIF 帧间隔按 fps（10ms 取整）",
          d is not None and d % 10 == 0 and abs(d - 1000 / s.fps) <= 10,
          f"{d}ms（理想 {1000 / s.fps:.1f}ms）")

    try:
        import imageio_ffmpeg  # noqa: F401
    except ImportError:
        print("[SKIP] MP4 导出（未安装 imageio-ffmpeg）")
    else:
        mp4 = tmp / "export.mp4"
        s.mp4_to(mp4)
        check("导出 MP4 非空", mp4.exists() and mp4.stat().st_size > 1000,
              f"{mp4.stat().st_size} B")
        odd = Session.open(make_fake_session(tmp / "odd", n=4, w=61, h=41))
        odd_mp4 = tmp / "odd.mp4"
        odd.mp4_to(odd_mp4)
        check("奇数尺寸 MP4（补 1px 到偶数）", odd_mp4.stat().st_size > 1000,
              f"{odd_mp4.stat().st_size} B")

    custom = Session.create(12, {}, root=tmp / "custom_root")
    check("自定义存储路径",
          custom.dir.parent == tmp / "custom_root" and custom.dir.exists(),
          str(custom.dir))


def test_viewer(tmp: Path, app):
    print("\n== Viewer ==")
    s = Session.open(make_fake_session(tmp))
    v = ViewerWindow(s)
    v.show()
    app.processEvents()

    check("初始帧标签", v.frame_label.text() == "1 / 20", v.frame_label.text())

    v._step(1)
    check("步进到第 2 帧", v.frame_label.text() == "2 / 20")

    v._step(-1)
    v._set_index(9)
    check("跳转到第 10 帧", v.frame_label.text() == "10 / 20")

    # 洋葱皮
    v._set_onion(2)
    app.processEvents()
    pm = v._cache.tinted(9, "prev")
    check("洋葱皮层数生效", v.canvas._onion == 2)
    check("洋葱皮标签更新", v.onion_label.text() == "2 层", v.onion_label.text())
    v._set_onion(5)
    check("5 层时 + 禁用", not v.btn_onion_inc.isEnabled())
    v._set_onion(0)
    check("0 层时 - 禁用", not v.btn_onion_dec.isEnabled())
    v._set_onion(2)
    check("染色帧生成", pm is not None and not pm.isNull())

    # 变速（↑/↓ 快捷键的处理函数，未播放时只改档位）
    v._cycle_speed(1)
    check("变速到 2x", v._speed == 2.0, f"{v._speed}")
    v._cycle_speed(-1)

    # 缩放
    v.canvas.set_zoom(2.0)
    check("缩放到 2x", abs(v.canvas.zoom - 2.0) < 1e-6, f"{v.canvas.zoom}")

    # 播放 tick 与循环
    v._toggle_play()
    check("进入播放状态", v._playing is True)
    v._set_index(19, pause=False)          # 末帧
    v._tick()                              # 应回绕到第 1 帧
    check("末帧后循环回绕", v.canvas.index == 0, f"index={v.canvas.index}")
    v._toggle_play()
    check("退出播放状态", v._playing is False)

    # 渲染截图（保存下来供人工核对）
    shot = v.grab()
    check("渲染截图非空", not shot.isNull(), f"{shot.width()}x{shot.height()}")
    shot.save(str(OUT / "smoke_viewer.png"))
    v.close()


def test_palette_lossless():
    """≤256 色内容自动存索引 PNG——必须严格无损。"""
    print("\n== 索引色 PNG 无损性 ==")
    src = Image.new("RGB", (64, 64))
    for x in range(64):
        for y in range(64):
            src.putpixel((x, y), ((x // 4) * 16, (y // 4) * 16, 128))
    assert src.getcolors(256) is not None, "测试图应 ≤256 色"
    pal = src.convert("P", palette=Image.ADAPTIVE, colors=256,
                      dither=Image.Dither.NONE)
    back = pal.convert("RGB")
    check("索引 PNG 往返像素一致", src.tobytes() == back.tobytes())


def test_capture_worker(tmp: Path):
    print("\n== CaptureWorker（真实截屏）==")
    rect = {"left": 60, "top": 60, "width": 320, "height": 200}
    t0 = time.time()
    for fmt in ("png", "jpg"):
        d = tmp / f"session_live_{fmt}"
        d.mkdir()
        s = Session(d, 15, rect, fmt)
        s.write_metadata()
        worker = CaptureWorker(rect, 15, s)
        threading.Timer(0.7, worker.request_stop).start()
        worker.run()
        s.rescan()
        n = s.frame_count
        check(f"{fmt} 录到帧", n >= 5, f"{n} 帧")
        first = Image.open(s.frame_paths[0])
        check(f"{fmt} 帧尺寸与选区一致", first.size == (320, 200), f"{first.size}")
        check(f"{fmt} 扩展名正确", s.frame_paths[0].suffix == f".{fmt}")
    elapsed = time.time() - t0


def main():
    tmp = Path(tempfile.mkdtemp(prefix="fg_smoke_"))
    app = QApplication.instance() or QApplication(sys.argv)

    test_session(tmp)
    test_viewer(tmp, app)
    test_palette_lossless()
    test_capture_worker(tmp)

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n==== {len(RESULTS) - len(failed)}/{len(RESULTS)} 通过 ====")
    if failed:
        print("失败项：", ", ".join(failed))
        sys.exit(1)


if __name__ == "__main__":
    main()
