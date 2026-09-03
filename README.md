# FrameGrabber — 逐帧动作参考工具

**简体中文** ｜ [English](README.en.md)

看视频 / 动图学动作时：像截图一样**框选一块屏幕区域**，按设定帧率录成 **PNG 帧序列**，然后在内置查看器里逐帧慢慢研究。

## 安装

```bash
python -m venv .venv
.venv\Scripts\pip install -e .
```

依赖声明在 `pyproject.toml`（pyside6 / mss / pillow / imageio-ffmpeg），`-e` 是可编辑安装——源码改动即时生效，无需重装。

## 使用

```bash
.venv\Scripts\python -m framegrabber     # 方式一
.venv\Scripts\framegrabber.exe           # 方式二（安装时生成的入口）
```

1. 点 **⏺ 新建录制**，拖拽框选屏幕上想录的区域（ESC 取消；超过 2560×1600 会被钳制并红字提示）
2. 选区旁出现悬浮条和蓝色边框标记：选帧率（10 / 15 / 30 / 60）→ 点 **● 录制**
3. 录完点 **⏹ 停止**，自动打开帧查看器

录制的帧保存在 `~/Videos/FrameGrabber/session_日期_时间/`（可在启动器的「存储位置」里更改；「清除」按钮清空最近会话列表，已删除的会话文件夹也不会再出现在列表里）。

## 查看器快捷键

| 按键 | 功能 |
|---|---|
| ← / → / 滚轮 | 逐帧步进（自动暂停播放） |
| 空格 | 播放 / 暂停（循环播放） |
| ↑ / ↓ | 变速 0.1x ~ 2x |
| Ctrl + 滚轮 | 以光标为中心缩放 |
| 0 / 1 / 2 / 4 | 适配窗口 / 100% / 200% / 400% |
| + / − | 洋葱皮层数 0~5 |
| Home / End | 首帧 / 末帧 |
| O | 打开会话文件夹 |

**洋葱皮**：把前几帧（红）和后几帧（蓝）半透明叠在当前帧上，一眼看出动作的位移幅度——画连续动作时非常好用。
缩放是最近邻插值，放大边缘不糊。工具条「导出」按钮可导出 **ZIP 压缩包 / GIF 动图（循环播放）/ MP4 视频**。

## 项目结构

```
frame-grabber/
├── pyproject.toml          # 元数据 + 依赖（相当于 pom.xml）
├── src/framegrabber/       # 源码包（src 布局）
│   ├── app.py              # 启动器窗口 + main()
│   ├── selector.py         # 全屏框选（DPI 换算只在这里做）
│   ├── recorder.py         # 截屏工作线程 + 悬浮控制条
│   ├── viewer.py           # 帧查看器（洋葱皮/变速/无损缩放/LRU 缓存）
│   ├── session.py          # 会话目录与元数据
│   ├── icons.py            # 内嵌 SVG 图标渲染
│   └── theme.py            # 界面颜色
├── tests/                  # 冒烟测试（离屏跑，不打扰桌面）
└── scripts/                # 真机验证脚本（会闪现窗口）
```

## 测试

```bash
# 冒烟测试（离屏 + 真实截屏各一部分）
QT_QPA_PLATFORM=offscreen .venv\Scripts\python.exe -m tests.test_smoke
# 真机录制链路验证（闪现悬浮条约 4 秒）
.venv\Scripts\python.exe scripts\rec_check.py
```

## 体积与格式

录制的体积 ≈ 帧数 × 单帧体积，两个杠杆都在悬浮条上：

- **帧率**：体积与帧率成正比。分析动作通常 **10~15 fps 就够**（传统动画本身多为 12/24 fps），没必要 60
- **格式**（800×450 实测）：
  - **PNG 无损**（默认）：约 35~66 KB/帧。颜色数 ≤256 的干净内容自动存索引色 PNG，更小且严格无损
  - **JPEG 小体积**：编码快约 7 倍——大区域 + 高帧率时能保住帧率；照片/视频类平滑内容下比 PNG 小。但有损（放大有压缩痕迹）

  边缘锐利的内容（UI、图表）PNG 反而比 JPEG 小，默认 PNG 即可。

会话都在 `~/Videos/FrameGrabber/`，不需要的整个文件夹删掉即可。

## 说明

- mss 不捕获鼠标指针（分析参考时正好不被指针挡住）
- 单次选区上限 2560×1600（保证高帧率下截图+存盘来得及）
- 多显示器下选区请保持在同一块屏幕内

## 打包成 exe（可选）

```bash
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller --noconsole --onefile --name FrameGrabber --icon assets\icon.ico src/framegrabber/__main__.py
```

生成的 `dist\FrameGrabber.exe` 可以单独拷贝使用。

## 许可证

[MIT](LICENSE) © yuca0081
