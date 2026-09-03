"""生成 assets/icon.ico —— exe 文件图标（打包时 --icon 参数用）。

运行时界面图标由 icons.py 用 SVG 现画、无需资源文件；但 Windows
exe 的文件图标必须是一个 .ico 文件。这里把同一枚相机图形（选区
青色）画在深色圆角底上，光栅化成多尺寸 ico，与窗口图标视觉一致。

用法：.venv\\Scripts\\python scripts\\make_icon.py
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

# 纯离屏渲染，不弹任何窗口，CI 也能跑
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image                                 # noqa: E402
from PySide6.QtCore import QBuffer, QByteArray, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer                # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from framegrabber.icons import _BODIES  # noqa: E402  同仓库开发脚本，借用私有表
from framegrabber.theme import ACCENT, BG, BORDER  # noqa: E402

SIZE = 512      # 主渲染尺寸（一次性画大，再缩到各档位，抗锯齿质量好）
INNER = 0.58    # 相机图形占整图的比例（四周留呼吸空间）


def render_qimage() -> QImage:
    svg_body = _BODIES["camera"].replace("currentColor", ACCENT)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
           f"{svg_body}</svg>").encode("utf-8")
    renderer = QSvgRenderer(QByteArray(svg))

    img = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    # 深色圆角底（圆角比例 ≈ 常见应用图标的 22.5%）+ 细描边提轮廓
    m = SIZE * 0.045
    p.setPen(QColor(BORDER))
    p.setBrush(QColor(BG))
    p.drawRoundedRect(QRectF(m, m, SIZE - 2 * m, SIZE - 2 * m),
                      SIZE * 0.225, SIZE * 0.225)
    side = SIZE * INNER
    off = (SIZE - side) / 2
    renderer.render(p, QRectF(off, off, side, side))
    p.end()
    return img


def main():
    app = QGuiApplication([])  # noqa: F841 —— 图像插件可能需要（离屏、无窗口）
    img = render_qimage()

    # QImage → 内存 PNG → PIL（避免落临时文件）
    buf = QBuffer()
    buf.open(QBuffer.ReadWrite)
    img.save(buf, "PNG")
    pil = Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")
    pil = pil.resize((256, 256), Image.LANCZOS)

    out = Path(__file__).resolve().parents[1] / "assets" / "icon.ico"
    out.parent.mkdir(parents=True, exist_ok=True)
    pil.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                         (64, 64), (128, 128), (256, 256)])
    print(f"已生成 {out}")


if __name__ == "__main__":
    main()
