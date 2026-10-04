"""Captures de référence de l'interface Kut-Studio : thèmes, tailles de fenêtre et scènes (outil de développement).

Rejouable à la main, sans écran réel (plateforme Qt ``offscreen``) ::

    python -m tools.capture_ui --out /tmp/shots                        # sombre + clair, 4 tailles, toutes les scènes
    python -m tools.capture_ui --out /tmp/shots --themes dark          # un seul thème
    python -m tools.capture_ui --out /tmp/shots --sizes 1440x900       # une seule taille
    python -m tools.capture_ui --out /tmp/shots --scenes editor,graph  # quelques scènes
    python -m tools.capture_ui --out /tmp/shots --scale 2              # HiDPI simulé (QT_SCALE_FACTOR)
    python -m tools.capture_ui                                         # la capture historique de docs/ (1440 × 900, sombre)

Une capture par ``<sortie>/<thème>/<largeur>x<hauteur>/<scène>.png`` et un ``manifest.json`` qui dit ce qui a été produit.

Scènes (groupes) :

``home``         la fenêtre sans projet : l'état vide, ce que voit un utilisateur qui ouvre l'application ;
``editor``       l'espace de montage (projet de démonstration riche, premier clip sélectionné) et la timeline seule ;
``inspector``    un onglet de l'inspecteur par capture (``inspecteur-clip``, ``-couleur``, ``-effets``, ``-audio``…) ;
``library``      une page de la bibliothèque par capture (médias, effets, transitions, texte…) ;
``windows``      le panneau d'export (file de rendu comprise) et le moniteur Multicam ;
``graph``        l'éditeur de courbes (fenêtre à part), sur une courbe de vitesse ;
``preferences``  la fenêtre des préférences, complète.

Ces captures servent à **regarder** l'interface et à garder une preuve de chaque revue ; elles ne valident pas qu'elle est belle
(voir ``docs/design-qa-final.md`` pour ce qui reste à valider à l'œil). Le thème est choisi **avant** la construction de la
fenêtre, comme au démarrage de l'application (un changement de thème à chaud est testé à part : ``tests/test_theme_live_switch.py``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Le script vit dans tools/ : on remonte à la racine du dépôt pour que ``ui``, ``core`` et ``tests`` soient importables.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

LEGACY_OUTPUT = _ROOT / "docs" / "kut_studio_redesign_1440x900.png"
"""Capture historique de la refonte (voir ``docs/design-qa.md``) : ce que produit l'outil sans argument."""

THEMES: tuple[str, ...] = ("dark", "light")
SIZES: tuple[tuple[int, int], ...] = ((1180, 720), (1280, 720), (1440, 900), (1920, 1080))
"""Les tailles que l'application doit tenir : le minimum de la fenêtre, le portable courant, la référence, le grand écran."""
GROUPS: tuple[str, ...] = ("home", "editor", "inspector", "library", "windows", "graph", "preferences")

_WINDOW_SCENES = ("export", "moniteur-multicam")
"""Parmi les scénarios de ``tools.ui_audit.window_scenarios`` : l'éditeur de courbes a sa propre capture (la fenêtre elle-même)."""


def parse_sizes(text: str) -> list[tuple[int, int]]:
    """``"1440x900,1280x720"`` → ``[(1440, 900), (1280, 720)]``."""
    sizes = []
    for item in text.split(","):
        width, _sep, height = item.strip().lower().partition("x")
        sizes.append((int(width), int(height)))
    return sizes


def choose_theme(theme: str) -> None:
    """Fait démarrer la prochaine ``MainWindow`` dans ``theme`` (``dark`` ou ``light``), comme le réglage enregistré le ferait."""
    import ui.main_window as main_window_module

    original = getattr(main_window_module, "_capture_original_theme_manager", main_window_module.ThemeManager)
    main_window_module._capture_original_theme_manager = original            # type: ignore[attr-defined]
    main_window_module.ThemeManager = lambda requested_mode="dark": original(theme)   # type: ignore[assignment,misc]


def _save(widget, path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    widget.grab().save(str(path))
    return str(path)


def _speed_curve_clip(window) -> str | None:
    """Pose une courbe de vitesse sur un clip vidéo du projet et le retourne (pour que le Graph Editor ait une courbe à montrer)."""
    from core.animation import InterpolationType
    from core.time_ops import add_speed_point

    for track in window.project.tracks:
        if track.type != "video":
            continue
        for clip in track.clips:
            if clip.duration < 1.0:
                continue
            try:
                for moment, value in ((0.0, 1.0), (clip.duration * 0.35, 1.0), (clip.duration * 0.55, 0.3), (clip.duration * 0.85, 2.0)):
                    add_speed_point(window.project, clip.id, moment, value, interpolation=InterpolationType.BEZIER)
            except ValueError:
                continue
            return clip.id
    return None


def _graph_scene(window, audit) -> Callable[[], object]:
    def prepare():
        clip_id = _speed_curve_clip(window)
        if clip_id is not None:
            window._reload_timeline_preserving_selection(clip_id)
        editor = window.open_graph_editor("time.speed" if clip_id else None)
        editor.resize(960, 540)
        audit.settle(window)
        return editor

    return prepare


def scenes_for(window, group: str, audit) -> Iterable[tuple[str, Callable[[], object]]]:
    """Les scènes du groupe ``group`` : ``(nom, action)`` ; l'action retourne le widget à capturer (``None`` : la fenêtre)."""
    if group == "editor":
        audit.select_first_clip(window)
        yield "editor", lambda: None
        yield "timeline", lambda: window.timeline_panel
    elif group == "inspector":
        audit.select_first_clip(window)
        for name, action in audit.inspector_scenarios(window):
            yield name, lambda a=action: (a(), window.properties_panel)[1]
    elif group == "library":
        for name, action in audit.library_scenarios(window):
            yield name, lambda a=action: (a(), window.project_panel)[1]
    elif group == "windows":
        audit.select_first_clip(window)
        for name, action in audit.window_scenarios(window):
            if name in _WINDOW_SCENES:
                yield name, lambda a=action: (a(), None)[1]
    elif group == "graph":
        yield "editeur-de-courbes", _graph_scene(window, audit)


def capture(
    out: Path, themes: Iterable[str] = THEMES, sizes: Iterable[tuple[int, int]] = SIZES, groups: Iterable[str] = GROUPS,
    *, announce: Callable[[str], None] = lambda text: None,
) -> list[dict]:
    """Produit les captures ; retourne la liste de ce qui a été écrit (``thème``, ``taille``, ``scène``, ``fichier``)."""
    from PySide6.QtWidgets import QApplication

    from tools import ui_audit as audit

    written: list[dict] = []
    wanted = tuple(groups)
    for theme in themes:
        choose_theme(theme)
        for width, height in sizes:
            folder = out / theme / f"{width}x{height}"

            def record(scene: str, widget, window) -> None:
                path = _save(widget if widget is not None else window, folder / f"{scene}.png")
                written.append({"theme": theme, "size": f"{width}x{height}", "scene": scene, "file": path})
                announce(f"{theme} {width}x{height} {scene}")

            if "home" in wanted:
                window = audit.make_main_window(width, height, scopes=False, rich=False)
                try:
                    record("home", None, window)
                finally:
                    window.close()
            window = audit.make_main_window(width, height, scopes=True)
            try:
                for group in (item for item in wanted if item not in ("home", "preferences")):
                    for scene, action in scenes_for(window, group, audit):
                        widget = action()
                        audit.settle(window)
                        record(scene, widget, window)
                    if group == "graph" and getattr(window, "graph_editor", None) is not None:
                        window.graph_editor.hide()
                if "preferences" in wanted:
                    dialog = dict(audit.dialog_factories(window))["Préférences complètes"]()
                    try:
                        dialog.resize(min(width - 80, 760), min(height - 80, 640))
                        dialog.show()
                        audit.settle(window)
                        QApplication.processEvents()
                        record("preferences", dialog, window)
                    finally:
                        dialog.close()
            finally:
                window.close()
    return written


def capture_legacy() -> int:
    """La capture historique : 1440 × 900, thème sombre, scopes repliés, premier clip sélectionné (``docs/``)."""
    from tools import ui_audit as audit

    choose_theme("dark")
    window = audit.make_main_window(1440, 900, scopes=False, rich=False)
    try:
        audit.select_first_clip(window)
        window.grab().save(str(LEGACY_OUTPUT))
        print(f"screenshot written: {LEGACY_OUTPUT}", flush=True)
    finally:
        window.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, help="dossier de sortie ; sans lui : la capture historique de docs/")
    parser.add_argument("--themes", default=",".join(THEMES))
    parser.add_argument("--sizes", default=",".join(f"{w}x{h}" for w, h in SIZES))
    parser.add_argument("--scenes", default=",".join(GROUPS), help=f"groupes parmi : {', '.join(GROUPS)}")
    parser.add_argument("--scale", type=float, default=1.0, help="facteur d'échelle HiDPI simulé (QT_SCALE_FACTOR), ex. 2")
    args = parser.parse_args(argv)
    if args.scale != 1.0:
        os.environ["QT_SCALE_FACTOR"] = f"{args.scale:g}"      # avant la création de l'application Qt
    if args.out is None:
        return capture_legacy()
    unknown = [group for group in args.scenes.split(",") if group not in GROUPS]
    if unknown:
        parser.error(f"scènes inconnues : {', '.join(unknown)} (attendu : {', '.join(GROUPS)})")
    themes = [theme.strip() for theme in args.themes.split(",")]
    written = capture(args.out, themes, parse_sizes(args.sizes), args.scenes.split(","), announce=lambda text: print(text, flush=True))
    manifest = args.out / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"scale": args.scale, "captures": written}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(written)} captures dans {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
