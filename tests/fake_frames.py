"""合成测试会话：生成一串"平移方块"帧，模拟真实录制产物。

测试和验证脚本共用，避免两份相同的夹具代码。
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw


def make_fake_session(tmp: Path, n: int = 20, w: int = 320, h: int = 180,
                      fps: int = 12) -> Path:
    d = tmp / "session_fake"
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        img = Image.new("RGB", (w, h), (245, 245, 245))
        dr = ImageDraw.Draw(img)
        x = 10 + i * 12
        dr.rectangle([x, 70, x + 34, 130], fill=(200, 45, 45))
        dr.text((8, 8), f"frame {i}", fill=(30, 30, 30))
        img.save(d / f"frame_{i:06d}.png")
    (d / "session.json").write_text(
        json.dumps({"fps": fps, "region": {}}), "utf-8")
    return d
