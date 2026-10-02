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
- 🗂️ **Multiple and nested sequences** — several timelines per project; use a sequence as a clip inside another (rendered once however many times it is used, with its own transform, effects, keyframes and audio), nest a selection in one step, open nested sequences by double-click and navigate with breadcrumbs (`Master › Scene 01 › Intro`), back/forward and parent. Cycles are refused, older single-timeline projects open unchanged. See [docs/nested-sequences.md](docs/nested-sequences.md).
- ⚡ **Performance layer** — media proxies for preview (export always uses the originals), a unified cache with disk budget and purge, smart prefetching, timeline indexes for 10,000-clip projects and an adaptive *Auto* preview quality. See [docs/performance.md](docs/performance.md).
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
| `Ctrl + Shift + N` | Nest selection into a sequence |
| `Ctrl + Alt + ↓` / `Ctrl + Alt + ↑` | Open nested sequence / go to parent sequence |
| `Alt + ←` / `Alt + →` | Previous / next sequence |

Every shortcut can be changed in **Preferences → Shortcuts** (search, conflict detection, secondary shortcut, per-command or global reset). Changes apply immediately and are saved with your preferences; `Ctrl` is `⌘` on macOS. Multi-step chords such as `Ctrl+K, B` are supported.

**Adding a command (developers).** All shortcuts live in `core/shortcuts.py`.
1. Add `_cmd("my_id", Category.X, "Ctrl+Alt+M")` to `COMMANDS` (omit the shortcut to leave it unbound). Use `scope=Scope.ACTION` for a menu item, then create it with `self.shortcuts.create_action("my_id", text, self)`.
2. Add `shortcuts.command.my_id` (fr/en/es) in `ui/i18n.py`.
3. Map its function in `MainWindow._shortcut_handlers`.

Tests fail if a command has no translation, no handler, or a default that conflicts with another. A new command with a *default* shortcut must also be added to the expected set in `test_defaults_have_no_extra_shortcut_beyond_legacy_ones`.

## 🧪 Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q -n auto   # parallel; drop -n auto to run serially
python -m ruff check .
```

The suite (about 1,650 tests) covers the project model, timeline, `.kut`
I/O, render plan, color, scopes, audio, UI integration and the FFmpeg export
pipeline, including integration tests with a fake and a real FFmpeg. On a
headless machine, set `QT_QPA_PLATFORM=offscreen`.

## 🛣️ Roadmap

- Split the largest UI modules (`main_window`, `project_panel`, `timeline_panel`)
- Linting and type checking in CI, and a faster test suite
- Hardware-accelerated encoding (the model and extension point are ready)
- Preview and timeline performance on large projects
- Signed installers and automated releases

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
- 🗂️ **Séquences multiples et imbriquées** — plusieurs timelines par projet ; une séquence s'utilise comme un clip dans une autre (rendue une seule fois quel que soit le nombre d'instances, avec ses propres transform, effets, images-clés et audio), une sélection s'imbrique en une étape, double-clic pour ouvrir une séquence imbriquée et navigation par fil d'Ariane (`Master › Scene 01 › Intro`), précédent/suivant et parent. Les cycles sont refusés, les anciens projets à timeline unique s'ouvrent sans changement. Voir [docs/nested-sequences.md](docs/nested-sequences.md).
- ⚡ **Couche de performance** — proxies média pour l’aperçu (l’export utilise toujours les originaux), cache unifié avec budget disque et purge, préchargement intelligent, index de timeline pour des projets de 10 000 clips et qualité d’aperçu *Auto* adaptative. Voir [docs/performance.md](docs/performance.md).
- 🖥️ **Espace de travail** — Panneaux ancrables et espaces de travail enregistrés, préférences, thème sombre et interface en français / anglais / espagnol.
- ⌨️ **Raccourcis clavier** — Lecture, outils, snap, marqueurs et zoom (voir plus bas).

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
- **FFmpeg et ffprobe** accessibles dans le `PATH` — nécessaires pour
  l’import, l’aperçu fidèle, les scopes et l’export.
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
| `+` / `-` / `Ctrl + 0` | Zoom avant / arrière / ajusté |
| `Ctrl + N` / `Ctrl + O` / `Ctrl + S` | Nouveau / ouvrir / enregistrer le projet |
| `Ctrl + Z` / `Ctrl + Maj + Z` | Annuler / rétablir |
| `Ctrl + D` | Dupliquer |
| `Suppr` / `Ctrl + Retour arrière` | Supprimer / supprimer avec ripple |
| `Ctrl + E` | Activer / désactiver le clip |
| `Ctrl + ,` | Préférences |
| `Ctrl + Maj + N` | Créer une séquence à partir de la sélection |
| `Ctrl + Alt + ↓` / `Ctrl + Alt + ↑` | Ouvrir la séquence imbriquée / revenir à la séquence parente |
| `Alt + ←` / `Alt + →` | Séquence précédente / suivante |

Tous les raccourcis se modifient dans **Préférences → Raccourcis** (recherche, détection des conflits, raccourci secondaire, réinitialisation par commande ou globale). Les changements sont appliqués immédiatement et sauvegardés avec vos préférences ; `Ctrl` correspond à `⌘` sur macOS. Les accords en plusieurs étapes, comme `Ctrl+K, B`, sont pris en charge.

**Ajouter une commande (développeurs).** Tous les raccourcis sont dans `core/shortcuts.py`.
1. Ajouter `_cmd("mon_id", Category.X, "Ctrl+Alt+M")` à `COMMANDS` (sans raccourci : la commande reste libre). Pour un élément de menu, ajouter `scope=Scope.ACTION` puis créer l'action avec `self.shortcuts.create_action("mon_id", texte, self)`.
2. Ajouter `shortcuts.command.mon_id` (fr/en/es) dans `ui/i18n.py`.
3. Associer sa fonction dans `MainWindow._shortcut_handlers`.

Les tests échouent si une commande n'a ni traduction, ni fonction, ou si son défaut est en conflit. Une commande avec un raccourci *par défaut* doit aussi être ajoutée à l'ensemble attendu de `test_defaults_have_no_extra_shortcut_beyond_legacy_ones`.

## 🧪 Tests

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q -n auto   # en parallèle ; retirez -n auto pour l’exécution séquentielle
python -m ruff check .
```

La suite (environ 1 650 tests) couvre le modèle de projet, la timeline, les E/S `.kut`, le plan de rendu, la couleur, les scopes, l’audio, l’intégration de l’interface et le pipeline d’export FFmpeg, y compris des tests d’intégration avec un faux et un vrai FFmpeg. Sur une machine sans écran, définissez `QT_QPA_PLATFORM=offscreen`.

## 🛣️ Feuille de route

- Découper les plus gros modules d’interface (`main_window`, `project_panel`, `timeline_panel`)
- Linter et vérification de types en CI, suite de tests plus rapide
- Décodage matériel, aperçu et effets accélérés par le GPU (l’encodage matériel de l’export est en place : voir `docs/hardware-encoding.md`)
- Performances de l’aperçu et de la timeline sur les gros projets
- Installateurs signés et publications automatisées

## 🤝 Contribution

Les contributions et signalements de bugs sont les bienvenus. Créez une branche, faites une modification ciblée, lancez les tests puis ouvrez une pull request.

<p align="center">
  Fait avec ❤️ et PySide6
</p>
