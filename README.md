# 🎬 Kut-Studio

> A lightweight non-linear video editor prototype built with **Python** and **PySide6**.

Kut-Studio is a desktop video editor with a clean dark interface and a focused workflow: a project library, preview monitor, properties inspector, and multi-track timeline.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![PySide6](https://img.shields.io/badge/PySide6-%E2%89%A56.6-41CD52.svg)](https://doc.qt.io/qtforpython-6/)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey.svg)](#requirements)
[![Last commit](https://img.shields.io/github/last-commit/Kyyzen0/kut-studio)](https://github.com/Kyyzen0/kut-studio/commits/main)

[🇫🇷 Version française](#-version-française)

---

## ✨ Features

- 📁 **Project library** — Import, browse, and select project media from the **Media** bin.
- 🗂️ **Library navigation** — Dedicated sections for Media, Audio, Text, Effects, and Transitions. Audio, Text, Effects, and Transitions are currently interface placeholders.
- 🎥 **Preview monitor** — `QMediaPlayer` playback with play, pause, stop, and seek controls.
- ⚙️ **Properties inspector** — Live brightness, contrast, saturation, and volume controls, plus a subtitle editor synchronized with the playhead.
- 🎞️ **Multi-track timeline** — Two video tracks (V1 / V2) and a subtitle track (S1), with cut-at-playhead and delete-clip actions.
- 🔀 **Transition preview** — One-click crossfade preview between clips.
- 📤 **Export engine** — Export the timeline to `.mp4` or `.mov` using FFmpeg.
- ⌨️ **Keyboard shortcuts** — `Space` / `K` for play-pause, `J` / `←` for back 2 seconds, and `L` / `→` for forward 2 seconds.
- 🎨 **Custom dark theme** — Centralized colors and styles in `ui/theme.py`.

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
- **FFmpeg** available from your `PATH` (required to launch the application and export videos)
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
.venv/bin/python build.py
```

The build creates a windowed `Kut-Studio` application in `dist/`.

## 🗺️ Interface overview

```text
┌────────────────────────────────────────────────────────────────┐
│  K  KUT-STUDIO   Mon montage / Projet sans titre    [Exporter] │
├──────────────┬──────────────────────────────┬──────────────────┤
│  Bibliothèque│                              │  Propriétés      │
│  Médias      │        [Aperçu vidéo]        │  Effets couleur  │
│  Audio       │                              │  Volume          │
│  Texte       │                              │  Sous-titres     │
│  Effets      │                              │                  │
│  Transitions │                              │                  │
├──────────────┴──────────────────────────────┴──────────────────┤
│  Timeline: V1 / V2 / S1                                        │
└────────────────────────────────────────────────────────────────┘
```

## 📁 Project layout

```text
Kut-Studio/
├── main.py                  # Application entry point
├── build.py                 # PyInstaller packaging script
├── requirements.txt         # Runtime dependencies
├── assets/                  # Bundled assets
├── core/
│   ├── effects.py           # Color effects, subtitles, crossfade preview
│   ├── export_engine.py     # FFmpeg export pipeline
│   └── timeline_model.py    # Timeline operations
├── ui/
│   ├── main_window.py       # Main window and keyboard shortcuts
│   ├── project_panel.py     # Library navigation and media bin
│   ├── preview_panel.py     # Video preview
│   ├── timeline_panel.py    # Multi-track timeline
│   ├── properties_panel.py  # Effects, volume, subtitles
│   ├── export_panel.py      # Export screen
│   └── theme.py             # Theme and shared styles
└── tests/                   # pytest suite
```

## ⌨️ Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| `Space` / `K` | Play / pause |
| `←` / `J` | Seek back 2 seconds |
| `→` / `L` | Seek forward 2 seconds |
| `Ctrl + O` | Open a video |
| `Ctrl + N` | New project *(placeholder)* |
| `Ctrl + S` | Save project *(placeholder)* |

## 🧪 Tests

```bash
python -m pip install pytest pytest-qt
python -m pytest -q
```

The suite covers timeline operations and the FFmpeg export pipeline, including integration tests using a fake FFmpeg fixture.

## 🛣️ Roadmap

- Functional audio, text, effect, and transition libraries
- Audio tracks
- Native `.kut` project save and load
- More export, transition, and effect options
- Markers, chapters, magnetic timeline, and snapping
- Configurable keyboard shortcuts
- Continuous integration

## 🤝 Contributing

Contributions and bug reports are welcome. Create a branch, make a focused change, run the test suite, then open a pull request.

---

<a id="-version-française"></a>

# 🇫🇷 Version française

> Un prototype d’éditeur vidéo non linéaire léger, construit avec **Python** et **PySide6**.

Kut-Studio est un éditeur vidéo de bureau à l’interface sombre. Son flux de travail s’organise autour d’une bibliothèque de projet, d’un moniteur de prévisualisation, d’un inspecteur de propriétés et d’une timeline multi-pistes.

## ✨ Fonctionnalités

- 📁 **Bibliothèque de projet** — Importez, parcourez et sélectionnez les médias du bin **Médias**.
- 🗂️ **Navigation de bibliothèque** — Cinq sections : Médias, Audio, Texte, Effets et Transitions. Les quatre dernières sont actuellement des placeholders d’interface.
- 🎥 **Moniteur de prévisualisation** — Lecture via `QMediaPlayer`, avec lecture, pause, arrêt et déplacement dans la vidéo.
- ⚙️ **Inspecteur de propriétés** — Réglages en direct de luminosité, contraste, saturation et volume, avec éditeur de sous-titres synchronisé à la tête de lecture.
- 🎞️ **Timeline multi-pistes** — Deux pistes vidéo (V1 / V2) et une piste de sous-titres (S1), avec coupe à la tête de lecture et suppression de clip.
- 🔀 **Prévisualisation de transition** — Fondu enchaîné entre deux clips.
- 📤 **Moteur d’export** — Exporte la timeline en `.mp4` ou `.mov` via FFmpeg.
- ⌨️ **Raccourcis clavier** — `Espace` / `K` pour lecture-pause, `J` / `←` pour revenir de 2 s et `L` / `→` pour avancer de 2 s.
- 🎨 **Thème sombre** — Couleurs et styles centralisés dans `ui/theme.py`.

## 🧰 Stack technique

| Composant | Technologie |
| --- | --- |
| Langage | Python 3 |
| Interface | PySide6 (Qt 6, ≥ 6.6) |
| Multimédia | Qt Multimedia (`QMediaPlayer`) |
| Export | FFmpeg |
| Packaging | PyInstaller |
| Tests | pytest + pytest-qt |

## 📋 Pré-requis

- Python **3.10+**
- `pip`
- **FFmpeg** accessible dans le `PATH` — il est nécessaire au lancement de l’application et à l’export.
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
.venv/bin/python build.py
```

Le build produit une application graphique `Kut-Studio` dans `dist/`.

## ⌨️ Raccourcis clavier

| Raccourci | Action |
| --- | --- |
| `Espace` / `K` | Lecture / pause |
| `←` / `J` | Reculer de 2 secondes |
| `→` / `L` | Avancer de 2 secondes |
| `Ctrl + O` | Ouvrir une vidéo |
| `Ctrl + N` | Nouveau projet *(placeholder)* |
| `Ctrl + S` | Enregistrer le projet *(placeholder)* |

## 🧪 Tests

```bash
python -m pip install pytest pytest-qt
python -m pytest -q
```

La suite couvre les opérations de timeline et le pipeline d’export FFmpeg, y compris des tests d’intégration avec un faux exécutable FFmpeg.

## 🛣️ Feuille de route

- Bibliothèques Audio, Texte, Effets et Transitions fonctionnelles
- Pistes audio
- Sauvegarde et chargement de projets `.kut`
- Plus d’options d’export, de transitions et d’effets
- Marqueurs, chapitres, timeline magnétique et snap
- Raccourcis clavier configurables
- Intégration continue

## 🤝 Contribution

Les contributions et signalements de bugs sont les bienvenus. Créez une branche, faites une modification ciblée, lancez les tests puis ouvrez une pull request.

<p align="center">
  Fait avec ❤️ et PySide6
</p>
