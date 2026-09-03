"""会话管理：一次录制对应磁盘上的一个文件夹。

文件夹结构：
    ~/Videos/FrameGrabber/session_20260901_143000/
        frame_000000.png
        frame_000001.png
        ...
        session.json   (fps、区域、帧数等元数据)
"""
from __future__ import annotations

import json
import os
import zipfile
from datetime import datetime
from pathlib import Path

from PIL import Image


class Session:
    APP_NAME = "FrameGrabber"
    META_FILE = "session.json"
    DEFAULT_ROOT = Path.home() / "Videos" / "FrameGrabber"

    def __init__(self, directory: Path, fps: int = 30, region: dict | None = None,
                 fmt: str = "png"):
        self.dir = Path(directory)
        self.fps = fps
        self.region = region or {}
        self.format = fmt
        self.frame_paths: list[Path] = []
        self.rescan()

    # ---------- 创建 / 打开 ----------

    @classmethod
    def create(cls, fps: int, region: dict, fmt: str = "png",
               root: str | Path | None = None) -> "Session":
        """新建一个带时间戳的会话文件夹（root 指定存储根目录，默认 DEFAULT_ROOT）。"""
        base = Path(root) if root is not None else cls.DEFAULT_ROOT
        directory = base / f"session_{datetime.now():%Y%m%d_%H%M%S}"
        directory.mkdir(parents=True, exist_ok=True)
        s = cls(directory, fps, region, fmt)
        s.write_metadata()
        return s

    @classmethod
    def open(cls, directory: str | Path) -> "Session":
        """打开已有会话。没有 session.json 但有帧图片的文件夹也接受（fps 默认 30）。"""
        directory = Path(directory)
        meta = {}
        meta_file = directory / cls.META_FILE
        if meta_file.exists():
            meta = json.loads(meta_file.read_text("utf-8"))
        # fps 写 null 时 get 默认值不生效，用 or 兜底
        s = cls(directory, int(meta.get("fps") or 30), meta.get("region"),
                meta.get("format") or "png")
        if not s.frame_paths:
            raise FileNotFoundError(f"文件夹里没有帧图片：{directory}")
        return s

    # ---------- 帧 ----------

    def rescan(self):
        """重新扫描帧文件。录制中途崩溃后仍能用这一步恢复已录的帧。"""
        paths = list(self.dir.glob("frame_*.png")) + list(self.dir.glob("frame_*.jpg"))
        self.frame_paths = sorted(paths)

    @property
    def frame_count(self) -> int:
        return len(self.frame_paths)

    def new_frame_path(self, index: int) -> Path:
        ext = ".jpg" if self.format == "jpg" else ".png"
        return self.dir / f"frame_{index:06d}{ext}"

    # ---------- 元数据 ----------

    def write_metadata(self, frame_count: int | None = None):
        meta = {
            "app": self.APP_NAME,
            "fps": self.fps,
            "format": self.format,
            "region": self.region,
            "created": datetime.now().isoformat(timespec="seconds"),
            "frame_count": self.frame_count if frame_count is None else frame_count,
        }
        (self.dir / self.META_FILE).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), "utf-8"
        )

    # ---------- 导出 ----------

    def open_in_explorer(self):
        try:
            if hasattr(os, "startfile"):  # 仅 Windows
                os.startfile(self.dir)
        except OSError:
            pass

    def zip_to(self, dest: str | Path):
        """把整个会话打包成 zip（PNG 已压缩，用存储模式不再二次压缩）。"""
        dest = Path(dest)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED) as zf:
            for p in self.frame_paths:
                zf.write(p, p.name)
            meta = self.dir / self.META_FILE
            if meta.exists():
                zf.write(meta, meta.name)

    def gif_to(self, dest: str | Path):
        """导出循环播放的 GIF（Pillow 编码，逐帧流式处理不占额外内存）。

        GIF 帧间隔以 10ms（厘秒）为最小单位，直接取整到 10ms；
        60fps（16.7ms）受下限 20ms 限制会略微变慢，其余帧率误差 ≤4ms 无感。
        """
        dest = Path(dest)
        duration = max(20, round(1000 / self.fps / 10) * 10)

        def frame(p: Path) -> Image.Image:
            img = Image.open(p)
            if img.mode != "P":    # 已是索引色（≤256 色）直接用，严格无损
                img = img.convert("RGB").quantize(colors=256)
            return img

        frame(self.frame_paths[0]).save(
            dest, save_all=True,
            append_images=(frame(p) for p in self.frame_paths[1:]),
            duration=duration, loop=0, optimize=True)

    def mp4_to(self, dest: str | Path, quality: int = 8):
        """导出 H.264 MP4（imageio-ffmpeg 随包自带 ffmpeg，无需系统安装）。

        yuv420p 要求宽高为偶数：选区为奇数尺寸时用右/下边缘像素补 1px，
        原像素保持不变。quality 0~10，8 ≈ 视觉无损。
        """
        try:
            import imageio_ffmpeg
        except ImportError as exc:
            raise RuntimeError(
                "缺少 MP4 编码组件 imageio-ffmpeg，请执行："
                "pip install imageio-ffmpeg") from exc

        first = Image.open(self.frame_paths[0]).convert("RGB")
        w, h = first.size
        pw, ph = w % 2, h % 2
        writer = imageio_ffmpeg.write_frames(
            str(dest), (w + pw, h + ph), fps=self.fps,
            quality=quality, macro_block_size=1)   # 1 = 不对宽高取整到 16 倍数
        writer.send(None)  # 初始化
        for p in self.frame_paths:
            img = Image.open(p).convert("RGB")
            if pw or ph:
                base = Image.new("RGB", (w + pw, h + ph), img.getpixel((w - 1, h - 1)))
                base.paste(img, (0, 0))
                img = base
            writer.send(img.tobytes())
        writer.close()
