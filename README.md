# 🎬 Kut-Studio

> A lightweight non-linear video editor built with **Python** and **PySide6**.

Kut-Studio is a desktop video editor with a clean dark interface and a focused workflow: a project library, preview monitor, properties inspector, and multi-track timeline.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![PySide6](https://img.shields.io/badge/PySide6-%E2%89%A56.6-41CD52.svg)](https://doc.qt.io/qtforpython-6/)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey.svg)](#requirements)
[![Last commit](https://img.shields.io/github/last-commit/Kyyzen0/kut-studio)](https://github.com/Kyyzen0/kut-studio/commits/main)

[🇫🇷 Version française](#-version-française)

---

## ✨ Features

- 📁 **Project library** — Import media, organize it in folders, mark favorites and browse clip metadata.
- 🎞️ **Multi-track timeline** — Video (V1 / V2), audio (A1) and subtitle (S1) tracks, with blade, roll, slip and slide tools, snapping, ripple edits, markers and undo / redo.
- 🎥 **Preview monitor** — Faithful FFmpeg-based preview with a render cache and quality levels.
- ⚙️ **Inspector** — Clip, Color, Effects and Audio tabs, plus Graphics and Compositing (masks, chroma key, blend modes).
- 🎨 **Color grading and scopes** — Color controls, `.cube` 3D LUT import, and waveform / histogram / vectorscope monitoring.
- 🔀 **Transitions and effects** — Transition presets (including crossfade), an effects library and time remapping.
- 🔊 **Audio** — Mixer, audio effects, automation and voice-over recording.
- 🔤 **Text and graphics** — Styled titles, text presets, and SRT / ASS subtitle import and export.
- 💾 **Projects** — Native `.kut` save and load with autosave and backward-compatible loading of older format versions.
- 📤 **Export** — MP4 (H.264), MOV (H.264) and MOV (ProRes) through FFmpeg.
- 🖥️ **Workspace** — Dockable panels and saved workspaces, preferences, dark theme, and French / English / Spanish interface.
- ⌨️ **Keyboard shortcuts** — Playback, tools, snapping, markers and zoom (see below).

## 🧰 Tech stack

| Component | Technology |
| --- | --- |
| Language | Python 3 |
| GUI | PySide6 (Qt 6, ≥ 6.6) |
| Multimedia | Qt Multimedia (`QMediaPlayer`) |
| Export | FFmpeg |
| Packaging | PyInstaller |
| Tests | pytest + pytest-qt |

## 📋 Requirements

- Python **3.10+**
- `pip`
- **FFmpeg + ffprobe** available from your `PATH` (required for media import,
  faithful preview, scopes and export)
- A platform supported by PySide6: macOS, Windows, or Linux

To build a standalone application, install PyInstaller as well.

## 🚀 Installation

```bash
git clone https://github.com/Kyyzen0/kut-studio.git
cd kut-studio

python3 -m venv .venv
source .venv/bin/activate      # macOS / Linux
# .venv\Scripts\activate       # Windows

python -m pip install -r requirements.txt
```

Install FFmpeg with your platform package manager, for example:

```bash
brew install ffmpeg             # macOS with Homebrew
# winget install Gyan.FFmpeg    # Windows
# sudo apt install ffmpeg       # Debian / Ubuntu
```

## ▶️ Run the app

```bash
.venv/bin/python main.py
```

Or, with the virtual environment active:

```bash
python main.py
```

## 📦 Build a standalone app

```bash
python -m pip install pyinstaller
python build.py
```

The build creates a windowed `Kut-Studio` application in `dist/`.

PyInstaller builds are native: run the command separately on macOS, Windows
and Linux. The CI workflow does this automatically on all three systems.

To embed local FFmpeg binaries in a build, set `KUT_STUDIO_FFMPEG_DIR` to a
folder containing `ffmpeg` and `ffprobe` (`.exe` on Windows) before running
`build.py`. At runtime Kut-Studio checks the embedded `bin/` folder first,
then the configured folder, then the system `PATH`.

## 🖥️ Platform compatibility

Every push and pull request runs the complete tests, an offscreen UI smoke
test, a native PyInstaller build and a packaged-app smoke test on macOS,
Windows and Ubuntu. Configuration, cache, font and executable paths follow
each operating system's conventions.

## 🗺️ Interface overview

```text
┌────────────────────────────────────────────────────────────────┐
│  K  KUT-STUDIO   Sequence / Untitled project       [Export]    │
├────┬─────────────────────────┬─────────────────┬───────────────┤
│Rail│  Library (media bin)    │ Preview monitor │  Inspector    │
│    │                         │ + scopes        │               │
├────┴─────────────────────────┴─────────────────┴───────────────┤
│  Timeline: V1 / V2 / A1 / S1                                   │
└────────────────────────────────────────────────────────────────┘
```

The side rail switches between Media, Edit, Effects, Color, Text,
Transitions, Audio, Graphics and Templates.

## 📁 Project layout

```text
Kut-Studio/
├── main.py              # Application entry point (--smoke-test supported)
├── build.py             # PyInstaller packaging script
├── core/                # UI-independent logic: project model, .kut I/O,
│                        # timeline operations, render plan, export engine,
│                        # color grading, scopes, audio, effects, subtitles
├── ui/                  # PySide6 interface: main window, panels, theme,
│   └── workspace/       # i18n, icons; dockable workspace manager
├── tests/               # pytest suite
├── tools/               # Developer tools (UI capture)
├── docs/                # Design QA notes and screenshots
└── assets/              # Bundled assets
```

## ⌨️ Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Space` / `K` | Play / pause |
| `←` / `→` | Previous / next frame |
| `Shift + ←` / `Shift + →` | Back / forward 1 second |
| `J` / `L` | Shuttle back / forward |
| `V` / `B` / `R` / `Y` / `U` | Select / blade / roll / slip / slide tool |
| `S` / `N` | Toggle snapping / ripple |
| `M` / `[` / `]` | Add marker / previous / next marker |
| `Ctrl + K` | Cut at playhead |
| `Ctrl + A` | Select all |
| `+` / `-` / `Ctrl + 0` | Zoom in / out / fit |
| `Ctrl + N` / `Ctrl + O` / `Ctrl + S` | New / open / save project |
| `Ctrl + Z` / `Ctrl + Shift + Z` | Undo / redo |
| `Ctrl + D` | Duplicate |
| `Delete` / `Ctrl + Backspace` | Delete / ripple delete |
| `Ctrl + E` | Enable / disable clip |
| `Ctrl + ,` | Preferences |

## 🧪 Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

La suite (environ 1 650 tests) couvre le modèle de projet, la timeline, les E/S `.kut`, le plan de rendu, la couleur, les scopes, l’audio, l’intégration de l’interface et le pipeline d’export FFmpeg, y compris des tests d’intégration avec un faux et un vrai FFmpeg. Sur une machine sans écran, définissez `QT_QPA_PLATFORM=offscreen`.

## 🛣️ Feuille de route

- Découper les plus gros modules d’interface (`main_window`, `project_panel`, `timeline_panel`)
- Linter et vérification de types en CI, suite de tests plus rapide
- Raccourcis clavier configurables
- Presets d’export, file de rendu et accélération matérielle
- Performances de l’aperçu et de la timeline sur les gros projets
- Installateurs signés et publications automatisées

## 🤝 Contribution

Les contributions et signalements de bugs sont les bienvenus. Créez une branche, faites une modification ciblée, lancez les tests puis ouvrez une pull request.

<p align="center">
  Fait avec ❤️ et PySide6
</p>
