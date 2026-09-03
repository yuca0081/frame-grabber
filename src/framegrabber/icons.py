"""内嵌 SVG 图标集 + 渲染工具。

统一 24×24 视窗、2px 圆头线条（Lucide 风格几何自绘），
颜色由 currentColor 占位符在渲染时替换，可随主题着色。
运行时经 QtSvg 光栅化，2x 采样保证高分屏不发虚，无需打包资源文件。
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_SVG = "http://www.w3.org/2000/svg"

# (name → svg body)，渲染时包进 <svg> 并替换 currentColor
_BODIES = {
    # 录制/播放控制（几何尺寸尽量占满 24 格视窗并居中——
    # 小图标画得瘦小或偏一边，16px 下会像残缺的碎片）
    "record": '<circle cx="12" cy="12" r="6.5" fill="currentColor"/>',
    "pause": ('<rect x="6.8" y="5" width="4" height="14" rx="1.2" fill="currentColor"/>'
              '<rect x="13.2" y="5" width="4" height="14" rx="1.2" fill="currentColor"/>'),
    "play": ('<path d="M8 5.2v13.6c0 .95 1.05 1.5 1.85.95l10.7-6.8a1.15 1.15 0 0 0 0-1.9'
             'L9.85 4.25C9.05 3.7 8 4.25 8 5.2z" fill="currentColor"/>'),
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/>',
    "close": ('<path d="M7 7l10 10M17 7L7 17" fill="none" '
              'stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>'),
    # 走带（实心三角=步进一帧；跳端点=竖线在外侧：首帧 |◀，末帧 ▶|）
    "tri-left": '<path d="M18.5 5.5v13L7 12z" fill="currentColor"/>',
    "tri-right": '<path d="M5.5 5.5v13L17 12z" fill="currentColor"/>',
    # 洋葱皮层数增减（查看器第二行小步进钮）
    "tri-up": '<path d="M5.5 18.5h13L12 7z" fill="currentColor"/>',
    "tri-down": '<path d="M5.5 5.5h13L12 17z" fill="currentColor"/>',
    "skip-back": ('<path d="M4.5 5v14" stroke="currentColor" stroke-width="2.6" '
                  'stroke-linecap="round"/><path d="M19 5.5v13L8.2 12z" fill="currentColor"/>'),
    "skip-fwd": ('<path d="M19.5 5v14" stroke="currentColor" stroke-width="2.6" '
                 'stroke-linecap="round"/><path d="M5 5.5v13L15.8 12z" fill="currentColor"/>'),
    # 文件（实心剪影，小尺寸下比细线轮廓易读）
    "folder": ('<path d="M3.5 6.8A1.8 1.8 0 0 1 5.3 5h3.9c.5 0 1 .2 1.3.6L12 8.2h6.7'
               'a1.8 1.8 0 0 1 1.8 1.8v7.2a1.8 1.8 0 0 1-1.8 1.8H5.3a1.8 1.8 0 0 1 '
               '-1.8-1.8z" fill="currentColor"/>'),
    "archive": ('<rect x="4" y="4" width="16" height="4.4" rx="1.2" fill="currentColor"/>'
                '<path d="M5.6 9.6h12.8V18a1.6 1.6 0 0 1-1.6 1.6H7.2A1.6 1.6 0 0 1 '
                '5.6 18z" fill="currentColor"/>'),
    # 摄影机（录制 / 应用图标——实心剪影一眼即懂）
    "camera": (
        '<rect x="3" y="7" width="12.5" height="10" rx="2.5" fill="currentColor"/>'
        '<path d="M16 9.4l4.3-2.8c.7-.5 1.7.05 1.7.9v9c0 .85-1 1.4-1.7.9L16 14.6z" '
        'fill="currentColor"/>'),
}


def icon(name: str, color: str = "#F8FAFC", size: int = 16) -> QIcon:
    """渲染一枚矢量图标（2x 采样，高分屏清晰）。

    注意：必须在无 DPR 的 QImage 上光栅化——QtSvg 直接画到带
    devicePixelRatio 的 QPixmap 上会错误光栅化（镜像对称的路径
    渲染结果都不一样）。DPR 只作为元数据在画完后设置。
    """
    body = _BODIES[name].replace("currentColor", color)
    svg = f'<svg xmlns="{_SVG}" viewBox="0 0 24 24">{body}</svg>'.encode("utf-8")
    renderer = QSvgRenderer(QByteArray(svg))
    img = QImage(size * 2, size * 2, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    renderer.render(p)
    p.end()
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(2.0)
    return QIcon(pm)
