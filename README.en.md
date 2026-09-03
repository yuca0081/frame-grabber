# FrameGrabber — Frame-by-Frame Motion Reference

[简体中文](README.md) ｜ **English**

Drag-select a screen region like taking a screenshot, record it as a **PNG frame sequence** at a chosen frame rate, then study the motion frame by frame in the built-in viewer.

## Installation

```bash
python -m venv .venv
.venv\Scripts\pip install -e .
```

Dependencies are declared in `pyproject.toml` (pyside6 / mss / pillow). The `-e` flag is an editable install — source changes take effect immediately, no reinstall needed.

## Usage

```bash
.venv\Scripts\python -m framegrabber     # option 1
.venv\Scripts\framegrabber.exe           # option 2 (entry point created at install time)
```

1. Click **⏺ New Recording** and drag to select a screen region (ESC cancels; selections larger than 2560×1600 are clamped with a red warning)
2. A floating bar and a blue border marker appear next to the selection: pick a frame rate (10 / 15 / 30 / 60) → click **● Record**
3. When done, click **⏹ Stop** — the frame viewer opens automatically

Frames are saved to `~/Videos/FrameGrabber/session_date_time/` (changeable via "Storage location" in the launcher; the "Clear" button empties the recent-session list, and deleted session folders disappear from it).

## Viewer Shortcuts

| Key | Action |
|---|---|
| ← / → / Wheel | Step one frame (auto-pauses playback) |
| Space | Play / pause (loops) |
| ↑ / ↓ | Speed 0.1x ~ 2x |
| Ctrl + Wheel | Zoom centered on the cursor |
| 0 / 1 / 2 / 4 | Fit window / 100% / 200% / 400% |
| + / − | Onion-skin layers 0–5 |
| Home / End | First / last frame |
| O | Open session folder |

**Onion skin**: overlays the previous frames (red) and next frames (blue) semi-transparently on the current frame, so motion displacement is visible at a glance — very handy for drawing continuous action.
Zoom uses nearest-neighbor interpolation, so magnified frames stay sharp.

## Project Structure

```
frame-grabber/
├── pyproject.toml          # metadata + dependencies
├── src/framegrabber/       # source package (src layout)
│   ├── app.py              # launcher window + main()
│   ├── selector.py         # fullscreen region selection (DPI conversion lives here)
│   ├── recorder.py         # capture worker thread + floating control bar
│   ├── viewer.py           # frame viewer (onion skin / variable speed / lossless zoom / LRU cache)
│   ├── session.py          # session directory & metadata
│   └── theme.py            # UI colors
├── tests/                  # smoke tests (offscreen, won't disturb your desktop)
└── scripts/                # real-machine verification scripts (windows flash briefly)
```

## Tests

```bash
# smoke tests (partly offscreen, partly real screen capture)
QT_QPA_PLATFORM=offscreen .venv\Scripts\python.exe -m tests.test_smoke
# real recording pipeline check (floating bar flashes for ~4 seconds)
.venv\Scripts\python.exe scripts\rec_check.py
```

## Size & Format

Recorded size ≈ frame count × per-frame size; both levers are on the floating bar:

- **Frame rate**: size scales linearly with fps. **10–15 fps is usually enough** for motion analysis (traditional animation is mostly 12/24 fps anyway) — no need for 60
- **Format** (measured at 800×450):
  - **PNG lossless** (default): ~35–66 KB/frame. Clean content with ≤256 colors is automatically saved as indexed-color PNG — smaller and strictly lossless
  - **JPEG compact**: ~7× faster encoding — keeps the frame rate up for large regions at high fps, and beats PNG on smooth photo/video-like content. But lossy (compression artifacts when zoomed in)

  Content with sharp edges (UI, charts) is actually smaller as PNG — the PNG default is the right choice.

Sessions all live under `~/Videos/FrameGrabber/`; just delete the folder when you no longer need it.

## Notes

- mss does not capture the mouse cursor (the pointer never blocks your reference)
- Maximum selection size is 2560×1600 (keeps capture + saving fast enough at high frame rates)
- On multi-monitor setups, keep the selection within a single screen

## Packaging as exe (optional)

```bash
.venv\Scripts\pip install pyinstaller
.venv\Scripts\pyinstaller --noconsole --onefile --name FrameGrabber --icon assets\icon.ico src/framegrabber/__main__.py
```

The resulting `dist\FrameGrabber.exe` is self-contained and can be copied anywhere.

## License

[MIT](LICENSE) © yuca0081
