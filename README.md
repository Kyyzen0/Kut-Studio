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
- 🔊 **Audio** — Mixer, audio effects with a preset library (favorites and your own presets), automation and voice-over recording.
- 🔤 **Text and graphics** — Styled titles, text presets, and SRT subtitle import and export.
- 💾 **Projects** — Native `.kut` save and load with autosave and backward-compatible loading of older format versions.
- 📤 **Export and render queue** — MP4 (H.264), MOV (H.264) and MOV (ProRes) through FFmpeg, with presets (H.264 1080p/1440p/4K, YouTube, vertical 1080×1920, ProRes Master, Custom) and a persistent, reorderable render queue. See [docs/render-queue.md](docs/render-queue.md).
- 🎬 **Keyframe animation** — one central engine (hold, linear, ease in/out, Bézier with linked or broken tangents) for position, scale, rotation and opacity: diamond buttons in the inspector, keyframes on the timeline, a Graph Editor, configurable shortcuts and undo — with preview and export computing exactly the same values. See [docs/animation.md](docs/animation.md).
- 🎞️ **Hardware encoding** — export with VideoToolbox, NVENC, Quick Sync, AMF or VAAPI when your FFmpeg really supports it (detected and validated at runtime), automatic CPU fallback, and an always-available CPU path. See [docs/hardware-encoding.md](docs/hardware-encoding.md).
- ✳️ **Motion graphics and compositing** — text, shape (rectangle, rounded rectangle, ellipse, line, polygon), solid and image layers, groups, null controllers and adjustment layers; anchor point, X/Y scale, skew and flips; parenting with cycle protection; multiple feathered masks (add / subtract / intersect); 8 blend modes identical in preview and export; optional motion blur; direct manipulation in the viewer with snapping, guides and safe areas; a Layers panel, attribute copy/paste and presets. See [docs/motion-graphics.md](docs/motion-graphics.md).
- ⚡ **Hardware decoding and GPU preview** — VideoToolbox, NVDEC (CUDA), D3D11VA, DXVA2, Quick Sync or VAAPI decoding when your FFmpeg really supports it (validated codec by codec), chosen from real measurements and always with a CPU fallback; a GPU monitor (Metal, Direct3D 11, OpenGL through Qt's QRhi) showing transforms, simple effects, blend modes, masks and adjustment layers in real time, checked against the export. See [docs/gpu-preview.md](docs/gpu-preview.md).
- 🎯 **2D tracking and stabilisation** — track one or several points of a video clip forward or backward in the background (progress, stop, resume, partial recompute), with confidence, uncertain / lost frames flagged, manual corrections and a drawn path in the viewer; drive a layer's or clip's position (and rotation / scale with two points), an anchor point or a mask, either **linked** (updates with the track) or **baked** to keyframes; stabilise a clip (position, + rotation, + scale; low / medium / high / custom smoothing or locked shot) with black edges, automatic zoom or a fixed crop. One animation engine: preview and export compute the same values. See [docs/tracking.md](docs/tracking.md).
- 🗂️ **Multiple and nested sequences** — several timelines per project; use a sequence as a clip inside another (rendered once however many times it is used, with its own transform, effects, keyframes and audio), nest a selection in one step, open nested sequences by double-click and navigate with breadcrumbs (`Master › Scene 01 › Intro`), back/forward and parent. Cycles are refused, older single-timeline projects open unchanged. See [docs/nested-sequences.md](docs/nested-sequences.md).
- 🎥 **Multicam** — group several cameras and audio recorders into a Multicam source, synchronize them (by sound, timecode, markers, clip starts or by hand), watch every angle at once and **cut the programme live by pressing `1`–`9` during playback**. Each cut is an ordinary clip boundary: trim, split, transitions, effects, nesting and Undo all work unchanged; only the shown angle is rendered, and preview and export stay identical. Local audio synchronization with an honest confidence score, per-camera colour and effects, audio policy (follows the picture, fixed recorder, mix), offline angles, flatten. See [docs/multicam.md](docs/multicam.md).
- ⏱️ **Time remapping and optical flow** — speed is a **curve**, not a number: constant speeds (25 % … 400 %), speed ramps with hold, linear, ease or Bézier points, freeze frames inside the curve, reverse and even `100 % → 0 % → −100 %`, edited from the right-click *Speed* menu, the inspector, the timeline (a discreet speed curve on the clip) or the Graph Editor — one engine behind all of them. Slow motion gets its in-between frames by **frame sampling**, **frame blending** (`A·(1−t) + B·t`) or **optical flow** (a pyramidal CPU backend, a confidence you can trust, honest fallbacks at cuts and flashes, cached motion vectors, an *Analyze optical flow* pre-computation you can cancel). Cutting or trimming a ramped clip never changes a frame of the export; audio keeps its pitch (or follows the speed), and preview and export use the same model. See [docs/time-remapping.md](docs/time-remapping.md) and [docs/optical-flow.md](docs/optical-flow.md).
- ⚡ **Performance layer** — media proxies for preview (export always uses the originals), a unified cache with disk budget and purge, smart prefetching, timeline indexes for 10,000-clip projects and an adaptive *Auto* preview quality. See [docs/performance.md](docs/performance.md).
- 🛡️ **Reliability** — Asks before discarding unsaved work, refuses damaged or non-finite `.kut` files with a clear message, tells you in the status bar when an edit is refused, writes uncaught errors to a rotating diagnostic log, and keeps preview, scopes and export on the same render graph. See [docs/architecture.md](docs/architecture.md) and [docs/stabilization-report.md](docs/stabilization-report.md).
- 🖥️ **Workspace** — Dockable panels and saved workspaces, preferences, dark theme, and French / English / Spanish interface.
- ⌨️ **Keyboard shortcuts** — Playback, tools, snapping, markers and zoom (see below).

## 🧰 Tech stack

| Component | Technology |
| --- | --- |
| Language | Python 3 |
| GUI | PySide6 (Qt 6, ≥ 6.6; ≥ 6.7 for the optional GPU monitor) |
| Multimedia | Qt Multimedia (`QMediaPlayer`) |
| Export | FFmpeg |
| Tracking analysis | numpy (FFT normalised cross-correlation) |
| GPU preview | Qt QRhi (Metal / Direct3D 11 / OpenGL), shaders compiled with `qsb` |
| Packaging | PyInstaller |
| Tests | pytest + pytest-qt (+ xdist, timeout) |
| Quality | ruff, mypy (`core/`) |

## 📋 Requirements

- Python **3.10+**
- `pip`
- **FFmpeg + ffprobe** available from your `PATH` (required for media import,
  faithful preview, scopes and export). Burned-in subtitles need an FFmpeg built
  with **libass** (the app detects its absence and says so instead of failing).
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

An app launched from the macOS Dock or Finder does not get your shell's `PATH`,
so at startup Kut-Studio appends the usual package-manager folders to it
(`/opt/homebrew/bin`, `/usr/local/bin`, `/opt/local/bin` on macOS; `/usr/local/bin`,
`/usr/bin`, `/snap/bin` and Linuxbrew on Linux) when they exist. An FFmpeg installed
with Homebrew is therefore found without any setup.

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
├── tools/               # Developer tools (UI capture, perf benchmarks)
├── docs/                # Feature docs, architecture, stabilization report
├── .github/workflows/   # CI: macOS, Windows, Linux
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
| `+` / `-` / `Ctrl + 0` (or `Shift + Z`) | Zoom in / out / fit |
| `Ctrl + Alt + S` | Show / hide the scopes |
| `Alt + K` / `Alt + Shift + K` | Add / remove a keyframe |
| `Alt + J` / `Alt + L` | Previous / next keyframe |
| `Ctrl + Alt + A` | Select all keyframes |
| `Ctrl + Alt + G` | Graph Editor |
| `Ctrl + N` / `Ctrl + O` / `Ctrl + S` | New / open / save project |
| `Ctrl + Shift + S` / `Ctrl + Q` | Save as / quit |
| `Ctrl + Z` / `Ctrl + Shift + Z` (or `Ctrl + Y`) | Undo / redo |
| `Ctrl + D` | Duplicate |
| `Delete` (or `Backspace`) / `Ctrl + Backspace` | Delete / ripple delete |
| `Ctrl + E` | Enable / disable clip |
| `Ctrl + ,` | Preferences |
| `Ctrl + Shift + N` | Nest selection into a sequence |
| `Ctrl + Alt + ↓` / `Ctrl + Alt + ↑` | Open nested sequence / go to parent sequence |
| `Ctrl + G` / `Ctrl + Shift + G` | Group / ungroup layers |
| `Ctrl + '` / `Ctrl + ;` | Safe areas / guides in the viewer |
| `Alt + ←` / `Alt + →` | Previous / next sequence |
| `1` … `9` | Multicam: show angle N at the playhead (cuts the segment; `Shift+digit` on AZERTY, numeric keypad works too) |
| `Ctrl + Shift + M` | Show / hide the Multicam monitor |

Every shortcut can be changed in **Preferences → Shortcuts** (search, conflict detection, secondary shortcut, per-command or global reset). Changes apply immediately and are saved with your preferences; `Ctrl` is `⌘` on macOS. Multi-step chords such as `Ctrl+K, B` are supported.

**Adding a command (developers).** All shortcuts live in `core/shortcuts.py`.
1. Add `_cmd("my_id", Category.X, "Ctrl+Alt+M")` to `COMMANDS` (omit the shortcut to leave it unbound). Use `scope=Scope.ACTION` for a menu item, then create it with `self.shortcuts.create_action("my_id", text, self)`.
2. Add `shortcuts.command.my_id` (fr/en/es) in `ui/i18n.py`.
3. Map its function in `MainWindow._shortcut_handlers`.

Tests fail if a command has no translation, no handler, or a default that conflicts with another. A new command with a *default* shortcut must also be added to the expected set in `test_defaults_have_no_extra_shortcut_beyond_legacy_ones`.

## 🧪 Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q -n auto --timeout=600   # parallel; drop -n auto to run serially
python -m ruff check .
python -m mypy                              # core/ ; known debt is listed in pyproject.toml (it can only shrink)
python -m tools.i18n_audit --summary        # hard-coded UI texts (the baseline is empty: a new one fails the tests)
python -m tools.perf.hardware_validation    # tests YOUR GPU encoders: mini export read back with ffprobe
```

The suite (over 3,200 tests) covers the project model, timeline, `.kut`
I/O, render plan, color, scopes, audio, UI integration and the FFmpeg export
pipeline, including parity tests that render with a real FFmpeg and read back
pixels, and a fake FFmpeg for failure paths. On a headless machine, set
`QT_QPA_PLATFORM=offscreen`. Where each piece of information lives, and which
test guards it: [docs/architecture.md](docs/architecture.md).

Some tests depend on what the machine has and **skip with their reason** instead of passing
vacuously (`pytest -rs` lists them): subtitle rendering needs an FFmpeg built with libass
(`pytest -m libass`; `KUT_STUDIO_REQUIRE_LIBASS=1` turns the skip into a failure, as the
`macos-libass` CI job does — see [docs/ci-libass.md](docs/ci-libass.md)), and each hardware encoder
(VideoToolbox, NVENC, Quick Sync, AMF, VAAPI) is validated only where it exists
(`KUT_STUDIO_REQUIRE_HARDWARE=nvenc` makes its absence a failure — see
[docs/hardware-encoding.md](docs/hardware-encoding.md)). FFmpeg children are killed with the
application even after `kill -9`: [docs/process-supervision.md](docs/process-supervision.md).

## 🛣️ Roadmap

- Split the largest UI modules (`main_window`, `project_panel`, `timeline_panel`)
- Extend type checking beyond the clean modules of `core/` (the debt list is in `pyproject.toml`), and a faster test suite
- Open items from the stabilization pass: see [docs/stabilization-report.md](docs/stabilization-report.md)
- More GPU effects (LUTs, colour grading, scopes) on top of the GPU preview
- Preview and timeline performance on large projects
- Signed installers and automated releases
- Motion graphics: per-character text animation, animated colors and mask vertices, effects inside groups
- Optical flow, next steps (the backend interface is ready, nothing else is started): Metal / CUDA / Vulkan / OpenCL / CoreML backends, neural interpolation, real-time blending and flow in the GPU monitor, motion blur and stabilisation built on the same flow. See [docs/optical-flow.md](docs/optical-flow.md#architecture-future).
- Multicam, next steps (the architecture leaves room, nothing is started): automatic multicam proxies, 16+ angles with a tuned grid, remote cameras and live capture, LTC synchronization, advanced waveform fingerprints, collaboration. See [docs/multicam.md](docs/multicam.md#architecture-future).

## 🤝 Contributing

Contributions and bug reports are welcome. Create a branch, make a focused change, run the test suite, then open a pull request.

---

<a id="-version-française"></a>

# 🇫🇷 Version française

> Un éditeur vidéo non linéaire léger, construit avec **Python** et **PySide6**.

Kut-Studio est un éditeur vidéo de bureau à l’interface sombre. Son flux de travail s’organise autour d’une bibliothèque de projet, d’un moniteur de prévisualisation, d’un inspecteur de propriétés et d’une timeline multi-pistes.

## ✨ Fonctionnalités

- 📁 **Bibliothèque de projet** — Importez vos médias, organisez-les en dossiers, marquez des favoris et consultez les métadonnées des clips.
- 🎞️ **Timeline multi-pistes** — Pistes vidéo (V1 / V2), audio (A1) et sous-titres (S1), avec outils lame, roll, slip et slide, snap, montage ripple, marqueurs et annuler / rétablir.
- 🎥 **Moniteur de prévisualisation** — Aperçu fidèle basé sur FFmpeg, avec cache de rendu et niveaux de qualité.
- ⚙️ **Inspecteur** — Onglets Clip, Couleur, Effets et Audio, plus Graphiques et Compositing (masques, chroma key, modes de fusion).
- 🎨 **Étalonnage et scopes** — Réglages couleur, import de LUT 3D `.cube`, et monitoring waveform / histogramme / vectorscope.
- 🔀 **Transitions et effets** — Presets de transitions (dont fondu enchaîné), bibliothèque d’effets et remapping temporel.
- 🔊 **Audio** — Mixeur, effets audio avec bibliothèque de préréglages (favoris et presets personnels), automation et enregistrement de voix off.
- 🔤 **Texte et graphiques** — Titres stylés, presets de texte, import et export de sous-titres SRT.
- 💾 **Projets** — Sauvegarde et chargement `.kut` natifs, avec autosave et chargement rétrocompatible des anciennes versions du format.
- 📤 **Export et file de rendu** — MP4 (H.264), MOV (H.264) et MOV (ProRes) via FFmpeg, avec des presets (H.264 1080p/1440p/4K, YouTube, vertical 1080×1920, ProRes Master, Custom) et une file de rendu persistante et réordonnable. Voir [docs/render-queue.md](docs/render-queue.md).
- 🎬 **Animation par images-clés** — un moteur central (maintien, linéaire, ease in/out, Bézier aux tangentes liées ou séparées) pour la position, l’échelle, la rotation et l’opacité : losanges dans l’inspecteur, images-clés dans la timeline, éditeur de courbes, raccourcis configurables et annulation — l’aperçu et l’export calculent exactement les mêmes valeurs. Voir [docs/animation.md](docs/animation.md).
- 🎞️ **Encodage matériel** — export avec VideoToolbox, NVENC, Quick Sync, AMF ou VAAPI lorsque votre FFmpeg le permet réellement (détecté et validé à l’exécution), repli CPU automatique et chemin CPU toujours disponible. Voir [docs/hardware-encoding.md](docs/hardware-encoding.md).
- ✳️ **Motion graphics et compositing** — calques texte, forme (rectangle, rectangle arrondi, ellipse, ligne, polygone), aplat et image, groupes, contrôleurs et calques d'effets (adjustment) ; point d'ancrage, échelle X/Y, inclinaison et miroirs ; parentage protégé contre les cycles ; masques multiples adoucis (ajouter / soustraire / intersection) ; 8 modes de fusion identiques en aperçu et à l'export ; flou de mouvement optionnel ; manipulation directe dans le viewer avec magnétisme, guides et zones de sécurité ; panneau Calques, copier / coller d'attributs et presets. Voir [docs/motion-graphics.md](docs/motion-graphics.md).
- ⚡ **Décodage matériel et aperçu GPU** — décodage VideoToolbox, NVDEC (CUDA), D3D11VA, DXVA2, Quick Sync ou VAAPI lorsque votre FFmpeg le permet réellement (validé codec par codec), choisi d'après des mesures réelles et toujours avec repli CPU ; moniteur GPU (Metal, Direct3D 11, OpenGL via QRhi de Qt) qui montre en temps réel transforms, effets simples, modes de fusion, masques et calques d'effets, vérifié contre l'export. Voir [docs/gpu-preview.md](docs/gpu-preview.md).
- 🎯 **Tracking 2D et stabilisation** — suivi d'un ou plusieurs points d'un clip vidéo, en avant ou en arrière et en tâche de fond (progression, arrêt, reprise, recalcul partiel), avec confiance, images incertaines / perdues signalées, corrections manuelles et trajectoire dans le viewer ; pilotage de la position d'un calque ou d'un clip (et de la rotation / échelle avec deux points), d'un point d'ancrage ou d'un masque, en **liaison dynamique** (suit le tracking) ou **converti en images-clés** ; stabilisation (position, + rotation, + échelle ; lissage faible / moyen / fort / personnalisé ou plan fixe) avec bords noirs, zoom automatique ou recadrage fixe. Un seul moteur d'animation : l'aperçu et l'export calculent les mêmes valeurs. Voir [docs/tracking.md](docs/tracking.md).
- 🗂️ **Séquences multiples et imbriquées** — plusieurs timelines par projet ; une séquence s'utilise comme un clip dans une autre (rendue une seule fois quel que soit le nombre d'instances, avec ses propres transform, effets, images-clés et audio), une sélection s'imbrique en une étape, double-clic pour ouvrir une séquence imbriquée et navigation par fil d'Ariane (`Master › Scene 01 › Intro`), précédent/suivant et parent. Les cycles sont refusés, les anciens projets à timeline unique s'ouvrent sans changement. Voir [docs/nested-sequences.md](docs/nested-sequences.md).
- 🎥 **Multicam** — regroupez plusieurs caméras et enregistreurs audio dans une source Multicam, synchronisez-les (par le son, le timecode, des repères, le début des clips ou à la main), regardez tous les angles à la fois et **montez le programme en direct en appuyant sur `1`–`9` pendant la lecture**. Chaque coupe est une coupe de clip ordinaire : rogner, couper, transitions, effets, imbrication et Annuler fonctionnent sans changement ; seul l'angle montré est rendu, et l'aperçu et l'export restent identiques. Synchronisation audio locale avec un score de confiance honnête, étalonnage et effets par caméra, politique audio (le son suit l'image, enregistreur fixe, mixage), angles hors ligne, aplatissement. Voir [docs/multicam.md](docs/multicam.md).
- ⏱️ **Remapping temporel et flux optique** — la vitesse est une **courbe**, pas un nombre : vitesses constantes (25 % … 400 %), rampes de vitesse (palier, linéaire, ease ou Bézier), arrêts sur image dans la courbe, sens inverse et même `100 % → 0 % → −100 %`, réglés depuis le menu clic droit *Vitesse*, l’inspecteur, la timeline (une courbe discrète sur le clip) ou le Graph Editor — un seul moteur derrière tous. Un ralenti obtient ses images intermédiaires par **échantillonnage**, **mélange d’images** (`A·(1−t) + B·t`) ou **flux optique** (backend processeur pyramidal, confiance mesurée, replis honnêtes aux coupures et aux flashs, vecteurs de mouvement en cache, pré-calcul annulable *Analyser le flux optique*). Couper ou rogner un clip à rampe ne change aucune image de l’export ; le son garde sa hauteur (ou suit la vitesse), et l’aperçu et l’export utilisent le même modèle. Voir [docs/time-remapping.md](docs/time-remapping.md) et [docs/optical-flow.md](docs/optical-flow.md).
- ⚡ **Couche de performance** — proxies média pour l’aperçu (l’export utilise toujours les originaux), cache unifié avec budget disque et purge, préchargement intelligent, index de timeline pour des projets de 10 000 clips et qualité d’aperçu *Auto* adaptative. Voir [docs/performance.md](docs/performance.md).
- 🛡️ **Fiabilité** — Demande avant d’abandonner un travail non enregistré, refuse un `.kut` abîmé ou contenant des valeurs non finies avec un message clair, dit dans la barre d’état quand une édition est refusée, écrit les erreurs non rattrapées dans un journal tournant, et garde aperçu, scopes et export sur le même graphe de rendu. Voir [docs/architecture.md](docs/architecture.md) et [docs/stabilization-report.md](docs/stabilization-report.md).
- 🖥️ **Espace de travail** — Panneaux ancrables et espaces de travail enregistrés, préférences, thème sombre et interface en français / anglais / espagnol.
- ⌨️ **Raccourcis clavier** — Lecture, outils, snap, marqueurs et zoom (voir plus bas).

## 🧰 Stack technique

| Composant | Technologie |
| --- | --- |
| Langage | Python 3 |
| Interface | PySide6 (Qt 6, ≥ 6.6 ; ≥ 6.7 pour le moniteur GPU optionnel) |
| Multimédia | Qt Multimedia (`QMediaPlayer`) |
| Export | FFmpeg |
| Analyse de tracking | numpy (corrélation croisée normalisée par FFT) |
| Aperçu GPU | QRhi de Qt (Metal / Direct3D 11 / OpenGL), shaders compilés par `qsb` |
| Packaging | PyInstaller |
| Tests | pytest + pytest-qt (+ xdist, timeout) |
| Qualité | ruff, mypy (`core/`) |

## 📋 Pré-requis

- Python **3.10+**
- `pip`
- **FFmpeg et ffprobe** accessibles dans le `PATH` — nécessaires pour
  l’import, l’aperçu fidèle, les scopes et l’export. L’incrustation de
  sous-titres exige un FFmpeg compilé avec **libass** (l’application détecte
  son absence et le dit au lieu d’échouer).
- macOS, Windows ou Linux compatible avec PySide6

PyInstaller est aussi nécessaire pour créer une application autonome.

## 🚀 Installation

```bash
git clone https://github.com/Kyyzen0/kut-studio.git
cd kut-studio

python3 -m venv .venv
source .venv/bin/activate      # macOS / Linux
# .venv\Scripts\activate       # Windows

python -m pip install -r requirements.txt
```

Installez ensuite FFmpeg avec le gestionnaire de paquets de votre système :

```bash
brew install ffmpeg             # macOS avec Homebrew
# winget install Gyan.FFmpeg    # Windows
# sudo apt install ffmpeg       # Debian / Ubuntu
```

## ▶️ Lancer l’application

```bash
.venv/bin/python main.py
```

Ou, avec l’environnement virtuel activé :

```bash
python main.py
```

## 📦 Construire une application autonome

```bash
python -m pip install pyinstaller
python build.py
```

Le build produit une application graphique `Kut-Studio` dans `dist/`.

Un build PyInstaller est natif : la commande doit être exécutée séparément
sur macOS, Windows et Linux. La CI le fait automatiquement sur les trois OS.

Pour embarquer FFmpeg, définissez `KUT_STUDIO_FFMPEG_DIR` vers un dossier
contenant `ffmpeg` et `ffprobe` (`.exe` sous Windows) avant de lancer
`build.py`. Kut-Studio cherche d’abord dans son dossier `bin/`, puis dans le
dossier configuré, puis dans le `PATH` système.

Une application lancée depuis le Dock ou le Finder de macOS ne reçoit pas le
`PATH` de votre shell : au démarrage, Kut-Studio lui ajoute donc les dossiers
habituels des gestionnaires de paquets (`/opt/homebrew/bin`, `/usr/local/bin`,
`/opt/local/bin` sous macOS ; `/usr/local/bin`, `/usr/bin`, `/snap/bin` et Linuxbrew
sous Linux) lorsqu’ils existent. Un FFmpeg installé avec Homebrew est ainsi trouvé
sans réglage.

## 🖥️ Compatibilité des plateformes

Chaque push et pull request exécute les tests complets, un smoke test UI, un
build PyInstaller natif et un smoke test de l’application empaquetée sur
macOS, Windows et Ubuntu. Les chemins de configuration, cache, polices et
outils sont adaptés à chaque système.

## ⌨️ Raccourcis clavier

| Raccourci | Action |
| --- | --- |
| `Espace` / `K` | Lecture / pause |
| `←` / `→` | Image précédente / suivante |
| `Maj + ←` / `Maj + →` | Reculer / avancer d’1 seconde |
| `J` / `L` | Shuttle arrière / avant |
| `V` / `B` / `R` / `Y` / `U` | Outil sélection / lame / roll / slip / slide |
| `S` / `N` | Activer le snap / le ripple |
| `M` / `[` / `]` | Ajouter un marqueur / marqueur précédent / suivant |
| `Ctrl + K` | Couper à la tête de lecture |
| `Ctrl + A` | Tout sélectionner |
| `+` / `-` / `Ctrl + 0` (ou `Maj + Z`) | Zoom avant / arrière / ajusté |
| `Ctrl + Alt + S` | Afficher / masquer les scopes |
| `Alt + K` / `Alt + Maj + K` | Ajouter / supprimer une image-clé |
| `Alt + J` / `Alt + L` | Image-clé précédente / suivante |
| `Ctrl + Alt + A` | Sélectionner toutes les images-clés |
| `Ctrl + Alt + G` | Éditeur de courbes |
| `Ctrl + N` / `Ctrl + O` / `Ctrl + S` | Nouveau / ouvrir / enregistrer le projet |
| `Ctrl + Maj + S` / `Ctrl + Q` | Enregistrer sous / quitter |
| `Ctrl + Z` / `Ctrl + Maj + Z` (ou `Ctrl + Y`) | Annuler / rétablir |
| `Ctrl + D` | Dupliquer |
| `Suppr` (ou `Retour arrière`) / `Ctrl + Retour arrière` | Supprimer / supprimer avec ripple |
| `Ctrl + E` | Activer / désactiver le clip |
| `Ctrl + ,` | Préférences |
| `Ctrl + Maj + N` | Créer une séquence à partir de la sélection |
| `Ctrl + Alt + ↓` / `Ctrl + Alt + ↑` | Ouvrir la séquence imbriquée / revenir à la séquence parente |
| `Ctrl + G` / `Ctrl + Maj + G` | Grouper / dégrouper les calques |
| `Ctrl + '` / `Ctrl + ;` | Zones de sécurité / guides du viewer |
| `Alt + ←` / `Alt + →` | Séquence précédente / suivante |
| `1` … `9` | Multicam : montrer l'angle N à la tête de lecture (coupe le segment ; `Maj + chiffre` sur AZERTY, le pavé numérique fonctionne aussi) |
| `Ctrl + Maj + M` | Afficher / masquer le moniteur Multicam |

Tous les raccourcis se modifient dans **Préférences → Raccourcis** (recherche, détection des conflits, raccourci secondaire, réinitialisation par commande ou globale). Les changements sont appliqués immédiatement et sauvegardés avec vos préférences ; `Ctrl` correspond à `⌘` sur macOS. Les accords en plusieurs étapes, comme `Ctrl+K, B`, sont pris en charge.

**Ajouter une commande (développeurs).** Tous les raccourcis sont dans `core/shortcuts.py`.
1. Ajouter `_cmd("mon_id", Category.X, "Ctrl+Alt+M")` à `COMMANDS` (sans raccourci : la commande reste libre). Pour un élément de menu, ajouter `scope=Scope.ACTION` puis créer l'action avec `self.shortcuts.create_action("mon_id", texte, self)`.
2. Ajouter `shortcuts.command.mon_id` (fr/en/es) dans `ui/i18n.py`.
3. Associer sa fonction dans `MainWindow._shortcut_handlers`.

Les tests échouent si une commande n'a ni traduction, ni fonction, ou si son défaut est en conflit. Une commande avec un raccourci *par défaut* doit aussi être ajoutée à l'ensemble attendu de `test_defaults_have_no_extra_shortcut_beyond_legacy_ones`.

## 🧪 Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q -n auto --timeout=600   # en parallèle ; retirez -n auto pour l’exécution séquentielle
python -m ruff check .
python -m mypy                              # core/ ; la dette connue est listée dans pyproject.toml (elle ne peut que diminuer)
python -m tools.i18n_audit --summary        # textes d’interface en dur (baseline vide : un nouveau fait échouer les tests)
python -m tools.perf.hardware_validation    # teste VOS encodeurs GPU : mini export relu avec ffprobe
```

La suite (plus de 3 200 tests) couvre le modèle de projet, la timeline, les E/S `.kut`, le plan de rendu, la couleur, les scopes, l’audio, l’intégration de l’interface et le pipeline d’export FFmpeg, y compris des tests de parité qui rendent avec un vrai FFmpeg et relisent les pixels, et un faux FFmpeg pour les pannes. Sur une machine sans écran, définissez `QT_QPA_PLATFORM=offscreen`. Où vit chaque information et quel test la garde : [docs/architecture.md](docs/architecture.md).

Certains tests dépendent de la machine et **se sautent avec leur raison** au lieu de passer à vide (`pytest -rs` les liste) : le rendu des sous-titres exige un FFmpeg compilé avec libass (`pytest -m libass` ; `KUT_STUDIO_REQUIRE_LIBASS=1` transforme le saut en échec, comme le fait le job de CI `macos-libass` — voir [docs/ci-libass.md](docs/ci-libass.md)), et chaque encodeur matériel (VideoToolbox, NVENC, Quick Sync, AMF, VAAPI) n’est validé que là où il existe (`KUT_STUDIO_REQUIRE_HARDWARE=nvenc` rend son absence bloquante — voir [docs/hardware-encoding.md](docs/hardware-encoding.md)). Les processus FFmpeg meurent avec l’application, même après un `kill -9` : [docs/process-supervision.md](docs/process-supervision.md).

## 🛣️ Feuille de route

- Découper les plus gros modules d’interface (`main_window`, `project_panel`, `timeline_panel`)
- Étendre la vérification de types au-delà des modules propres de `core/` (la liste de la dette est dans `pyproject.toml`), suite de tests plus rapide
- Points ouverts de la phase de stabilisation : voir [docs/stabilization-report.md](docs/stabilization-report.md)
- Plus d'effets sur GPU (LUT, étalonnage, scopes) au-dessus de l'aperçu GPU (voir `docs/gpu-preview.md`)
- Performances de l’aperçu et de la timeline sur les gros projets
- Installateurs signés et publications automatisées
- Motion graphics : animation caractère par caractère, couleurs et sommets de masque animés, effets à l'intérieur des groupes
- Flux optique, suite (l’interface de backend est prête, rien d’autre n’est commencé) : backends Metal / CUDA / Vulkan / OpenCL / CoreML, interpolation neuronale, mélange et flux en temps réel dans le moniteur GPU, flou de mouvement et stabilisation fondés sur le même flux. Voir [docs/optical-flow.md](docs/optical-flow.md#architecture-future).
- Multicam, suite (l'architecture laisse la place, rien n'est commencé) : proxys Multicam automatiques, 16 angles et plus avec une grille réglée, caméras distantes et capture en direct, synchronisation LTC, empreintes sonores avancées, collaboration. Voir [docs/multicam.md](docs/multicam.md#architecture-future).

## 🤝 Contribution

Les contributions et signalements de bugs sont les bienvenus. Créez une branche, faites une modification ciblée, lancez les tests puis ouvrez une pull request.

<p align="center">
  Fait avec ❤️ et PySide6
</p>
