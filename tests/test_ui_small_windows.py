"""Mises en page aux tailles de fenêtre supportées : 1440 × 900, 1280 × 720 et 1180 × 720 (le minimum).

Les polices diffèrent d'une plateforme à l'autre : aucune comparaison de pixels, aucune taille absolue fragile.
Les assertions sont **structurelles** (voir :mod:`tools.ui_audit`) : un contrôle ne dépasse pas la zone défilante
qui le porte, un panneau ne dépasse pas son hôte, deux widgets frères ne se chevauchent pas, la visionneuse garde
une hauteur utile. Le même audit se rejoue à la main, avec captures : ``python -m tools.ui_audit --out DIR``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QScrollArea

from core.workspace_state import MIN_SIZE, PanelId
from tools import ui_audit as audit
from ui.design_system import Sizes

ROOT = Path(__file__).resolve().parent.parent
SIZE_IDS = [f"{w}x{h}" for w, h in audit.SIZES]
SMALL_SIZES = [size for size in audit.SIZES if size[1] <= 720]


@pytest.fixture(autouse=True)
def _no_global_style_leak():
    """Un test de ce fichier ne modifie jamais la feuille de style **de l'application**.

    ``tools.ui_audit.scale_fonts`` agrandit les polices de l'application et de ses fenêtres vivantes sans pouvoir les
    restaurer : exécutée dans ce processus, elle laissait les fenêtres partagées (``window_at``) coupées pour tous les
    tests suivants du même worker (60 échecs en CI sous Windows, tests de 70 à 140 s au lieu de 4). Les scénarios de
    police tournent donc dans un sous-processus (``_run_in_subprocess``) ; ce garde fait échouer **le test fautif**,
    pas ses voisins.
    """
    app = QApplication.instance()
    before = app.styleSheet() if app is not None else ""
    yield
    app = QApplication.instance()
    if before:   # avant : vide ; le premier test qui crée une fenêtre installe légitimement la feuille de style du thème
        assert app is not None and app.styleSheet() == before, "ce test a modifié la feuille de style globale"


def _isolated_environment(base: Path) -> dict[str, str]:
    return {
        **os.environ, "QT_QPA_PLATFORM": "offscreen",
        "KUT_STUDIO_CONFIG_DIR": str(base / "config"), "KUT_STUDIO_CACHE_DIR": str(base / "cache"),
        "KUT_STUDIO_PROXY_DIR": str(base / "proxies"), "KUT_STUDIO_HARDWARE_ENCODING": "off",
    }


def _run_in_subprocess(arguments: list[str], base: Path, *, timeout: float = 600.0) -> subprocess.CompletedProcess[str]:
    """Interpréteur neuf : la feuille de style globale et les fenêtres de ce processus ne sont pas touchées."""
    return subprocess.run(
        [sys.executable, *arguments], cwd=ROOT, env=_isolated_environment(base), capture_output=True, text=True,
        timeout=timeout, check=False,
    )


@pytest.fixture(scope="module")
def window_at(tmp_path_factory):
    """Fenêtre principale (projet riche, scopes affichés) de la taille demandée, créée une fois par module.

    Les préférences, caches et proxys sont isolés ; la boîte « enregistrer avant de quitter ? » est neutralisée.
    """
    from ui.main_window_mixins.project_files import ProjectFilesMixin

    patch = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("small-windows")
    patch.setenv("KUT_STUDIO_CONFIG_DIR", str(base / "config"))
    patch.setenv("KUT_STUDIO_CACHE_DIR", str(base / "cache"))
    patch.setenv("KUT_STUDIO_PROXY_DIR", str(base / "proxies"))
    patch.setenv("KUT_STUDIO_HARDWARE_ENCODING", "off")
    patch.setattr(ProjectFilesMixin, "_confirm_discard_changes", lambda self: True)
    windows: dict[tuple[int, int], object] = {}

    def make(size: tuple[int, int]):
        if size not in windows:
            windows[size] = audit.make_main_window(*size, scopes=True)
        window = windows[size]
        audit.select_first_clip(window)
        return window

    yield make
    for window in windows.values():
        window.close()
    patch.undo()


def _assert_none(findings, context: str = "") -> None:
    """Échec lisible : chaque constat est nommé (widget, nature, dépassement) dans le message, donc dans le journal de CI."""
    if findings:
        raise AssertionError(f"{context} {[str(f) for f in findings][:8]} | {audit.environment_summary()}".strip())


def _blocking(findings) -> list[str]:
    return [str(f) for f in findings if f.blocking]


# --- inspecteur ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("size", audit.SIZES, ids=SIZE_IDS)
@pytest.mark.parametrize("tab,name", audit.INSPECTOR_TABS, ids=[name for _i, name in audit.INSPECTOR_TABS])
def test_inspector_content_fits_the_viewport_on_every_tab(window_at, size, tab, name):
    """Le défilement horizontal de l'inspecteur est désactivé : ce qui dépasse est coupé, donc inaccessible."""
    window = window_at(size)
    window.properties_panel._select_inspector_tab(tab)
    audit.settle(window)
    scroll = window.properties_panel.scroll_area
    _assert_none(audit.scroll_clipping(scroll), f"onglet {name} coupé à {size} :")
    assert audit.content_min_width_excess(scroll) <= 0


@pytest.mark.parametrize("size", audit.SIZES, ids=SIZE_IDS)
def test_tracking_panel_children_stay_inside_their_parents(window_at, size):
    """Régression : « Réinitialiser » coupé, « Avant ▶▶ » à moitié visible, champs de « Zones » débordants."""
    window = window_at(size)
    window.show_tracking_panel()
    audit.settle(window)
    panel = window.tracking_panel
    assert panel.isVisible()
    _assert_none(audit.parent_overflow(panel), "enfant hors de son parent :")
    viewport_width = window.properties_panel.scroll_area.viewport().width()
    for widget in (panel.add_button, panel.remove_button, panel.reset_button, panel.backward_button,
                   panel.stop_button, panel.forward_button, panel.pattern_w, panel.pattern_h,
                   panel.search_w, panel.search_h, panel.link_button, panel.bake_button, panel.auto_stab):
        if widget.isVisibleTo(panel):
            right = widget.mapTo(window.properties_panel.scroll_area.widget(), widget.rect().topRight()).x()
            assert right <= viewport_width, widget.objectName()


@pytest.mark.parametrize("size", audit.SIZES, ids=SIZE_IDS)
@pytest.mark.parametrize("name", ["clip", "sous-titre", "calque-graphique"])
def test_inspector_groups_of_special_clips_fit(window_at, size, name):
    """Sous-titre (éditeur de style : liste des polices) et calque graphique tiennent aussi."""
    window = window_at(size)
    scenarios = dict(audit.special_clip_scenarios(window))
    if name == "clip":
        audit.select_first_clip(window)
        window.properties_panel._select_inspector_tab(4)
    else:
        scenarios[f"inspecteur-{name}"]()
    audit.settle(window)
    _assert_none(audit.scroll_clipping(window.properties_panel.scroll_area), "inspecteur coupé :")


# --- bibliothèque -------------------------------------------------------------------------------------


@pytest.mark.parametrize("size", audit.SIZES, ids=SIZE_IDS)
def test_library_filters_do_not_overlap_the_folder_list(window_at, size):
    """Régression : « Manquants (4) » / « Gérer les tags… » se superposaient à la liste des dossiers."""
    window = window_at(size)
    panel = window.project_panel
    audit.open_library_section(window, "media")
    browse = panel.library_browse_content.widget()
    _assert_none(audit.sibling_overlaps(browse), "chevauchement (navigateur) :")
    _assert_none(audit.sibling_overlaps(panel), "chevauchement :")
    _assert_none(audit.parent_overflow(panel), "enfant hors de son parent :")


@pytest.mark.parametrize("size", audit.SIZES, ids=SIZE_IDS)
@pytest.mark.parametrize("section", audit.LIBRARY_SECTIONS)
def test_every_library_section_fits_its_column(window_at, size, section):
    """Bibliothèque ouverte par l'API réelle (``select_section``) : rien de coupé, écrasé ou superposé."""
    window = window_at(size)
    audit.open_library_section(window, section)
    _assert_none(_blocking(audit.audit_main_window(window, f"{size} {section}")), f"{size} {section} :")
    audit.open_library_section(window, "media")


def test_library_filter_tabs_wrap_instead_of_being_squeezed(window_at):
    """Régression : sept puces de filtre étaient écrasées à ~30 px de large (libellés illisibles)."""
    window = window_at((1180, 720))
    panel = window.project_panel
    for section, buttons in (
        ("transitions", lambda: panel.transition_view.filter_buttons),
        ("effects", lambda: panel.effects_view.category_buttons),
        ("audio-effects", lambda: [*panel.audio_effects_view.category_buttons, panel.audio_effects_view.favorites_button]),
    ):
        audit.open_library_section(window, section)
        for button in buttons():
            assert button.width() >= button.sizeHint().width(), (section, button.text())
    audit.open_library_section(window, "media")


def test_library_preset_cards_elide_long_names_instead_of_pushing_their_buttons_out(window_at):
    """Régression : la carte d'une transition dont le nom est long sortait de la colonne (étoile coupée)."""
    window = window_at((1180, 720))
    panel = window.project_panel
    audit.open_library_section(window, "transitions")
    scroll = panel.transition_view.findChild(QScrollArea, "transitionsScroll")
    cards = list(panel.transition_view._cards.values())
    assert cards
    for card in cards:
        card._title.setText("Un nom de preset beaucoup trop long pour tenir dans une colonne de 230 px")
    audit.settle(window)
    _assert_none(audit.scroll_clipping(scroll), "zone défilante coupée :")
    audit.open_library_section(window, "media")


def test_filter_chip_bar_fits_the_narrowest_media_column():
    from ui.library_organization_widgets import FilterChipBar

    assert FilterChipBar().minimumWidth() <= MIN_SIZE[PanelId.MEDIA] - 8


# --- squelette de la fenêtre : panneaux, moniteur, timeline ---------------------------------------------------------


@pytest.mark.parametrize("size", audit.SIZES, ids=SIZE_IDS)
def test_panels_stay_inside_their_hosts(window_at, size):
    """Régression : la timeline dépassait de 30 px son hôte, l'inspecteur de 20 px, à 1180 / 1280 × 720."""
    window = window_at(size)
    audit.settle(window)
    _assert_none([f for f in audit.parent_overflow(window) if "panelHost" in f.where or "dockZone" in f.where], "panneau hors de son hôte :")
    for name in ("project_panel", "properties_panel", "timeline_panel", "preview_panel"):
        panel = getattr(window, name)
        host = panel.parentWidget()
        assert panel.height() <= host.height() + 1, name
        assert panel.width() <= host.width() + 1, name


@pytest.mark.parametrize("size", SMALL_SIZES, ids=[f"{w}x{h}" for w, h in SMALL_SIZES])
def test_monitor_keeps_a_useful_height_when_the_window_is_low(window_at, size):
    """À 720 px le moniteur n'avait plus que ~85 px d'image : les scopes cèdent d'abord."""
    window = window_at(size)
    assert window.scopes_panel.isVisible()
    audit.settle(window)
    viewer = window.preview_panel.graphics_view
    assert viewer.height() >= 130, f"image de {viewer.height()} px seulement"
    assert window.scopes_panel.height() >= Sizes.scopes_min_height


def test_layout_at_900px_gives_scopes_their_comfort_height(window_at):
    """La disposition à 900 px ne change pas : les scopes gardent leur hauteur d'avant, la bibliothèque tient."""
    window = window_at((1440, 900))
    audit.settle(window)
    assert window.scopes_panel.height() >= Sizes.scopes_comfort_height
    assert window.preview_panel.graphics_view.height() >= 200


def test_library_keeps_its_natural_height_when_the_window_allows_it():
    """Avant, le minimum de layout de la bibliothèque (549 px) imposait sa hauteur à la rangée du haut ; désormais la
    répartition le décide explicitement, sans que la bibliothèque rogne à 720 px."""
    window = audit.make_main_window(1440, 900)
    try:
        library = window.project_panel
        root = window.workspace._root_splitter
        wanted = min(library.sizeHint().height(), root.height() - root.handleWidth() - Sizes.timeline_min_height)
        assert library.height() >= wanted - 16, f"bibliothèque de {library.height()} px, il en faut {wanted}"
    finally:
        window.close()


def test_scopes_alert_bar_only_takes_room_when_it_carries_an_alert(window_at):
    window = window_at((1280, 720))
    assert not window.scopes_panel._alert_bar.isVisible()


def test_the_offscreen_platform_has_real_fonts(qapp):
    """Sans police (Windows ``offscreen``), chaque caractère devient une boîte carrée : les mesures sont fausses."""
    from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics

    assert QFontDatabase.families(), "aucune police : les largeurs de texte mesurées ne sont pas celles d'une vraie police"
    font = QFont()
    font.setPixelSize(13)
    sample = "État : Aucun clip sélectionné"
    assert QFontMetrics(font).horizontalAdvance(sample) < len(sample) * 13 * 0.8, "texte mesuré avec des glyphes-boîtes"


def test_declared_minimums_agree_with_the_panels():
    """Un panneau plus grand que le minimum de sa zone déborde de son hôte."""
    from ui.properties_panel import PropertiesPanel
    from ui.timeline_panel import TimelinePanel

    assert Sizes.timeline_min_height <= MIN_SIZE[PanelId.TIMELINE]
    assert TimelinePanel(None).minimumHeight() <= MIN_SIZE[PanelId.TIMELINE]
    assert PropertiesPanel(lambda *_a: None).minimumWidth() <= MIN_SIZE[PanelId.INSPECTOR]


# --- l'outil d'audit lui-même ------------------------------------------------------------------------------------


def test_audit_tool_replays_and_saves_one_capture_per_scenario(tmp_path):
    """L'audit se rejoue à la main : ``python -m tools.ui_audit --out DIR`` (ici à 1180 × 720)."""
    findings = audit.run_audit(((1180, 720),), out_dir=tmp_path)
    _assert_none(_blocking(findings))
    captures = sorted(path.name for path in tmp_path.glob("*.png"))
    assert "1180x720-defaut.png" in captures
    assert "1180x720-inspecteur-suivi.png" in captures
    assert any(name.startswith("1180x720-bibliotheque-") for name in captures)


def test_layouts_survive_20_percent_wider_fonts_at_the_smallest_size(tmp_path):
    """Robustesse entre plateformes : les polices de Windows / Linux sont plus larges que celles de macOS.

    Les tests ne comparent aucune taille absolue ; celui-ci simule des polices 20 % plus larges (``scale_fonts``) et exige
    que rien ne soit coupé, débordant ou superposé à 1180 × 720, tous scénarios joués. Il tourne **dans un sous-processus** :
    agrandir les polices modifie la feuille de style globale, ce qui ralentissait (70 s) puis cassait les tests voisins.
    """
    done = _run_in_subprocess(["-m", "tools.ui_audit", "--sizes", "1180x720", "--font-scale", "1.2"], tmp_path)
    assert done.returncode == 0, f"constats bloquants à +20 % de police :\n{done.stdout[-3000:]}\n{done.stderr[-500:]}"


_SCALE_FONTS_SCRIPT = """
import re
from PySide6.QtWidgets import QApplication
from tools import ui_audit as audit

window = audit.make_main_window(1180, 720)
app = QApplication.instance()
base = app.styleSheet()
sizes = lambda sheet: [int(n) for n in re.findall(r"font-size:\\s*(\\d+)px", sheet)]

audit.scale_fonts(window, 1.5)
once = sizes(app.styleSheet())
assert once != sizes(base), "l'agrandissement n'a rien changé"
audit.scale_fonts(window, 1.5)
assert sizes(app.styleSheet()) == once, "l'agrandissement se cumule d'un appel à l'autre"
audit.scale_fonts(window, 1.0)
assert sizes(app.styleSheet()) == sizes(base), "le facteur 1,0 ne rétablit pas la feuille de style d'origine"
print("ok")
"""


def test_audit_font_scale_restarts_from_the_original_stylesheet(tmp_path):
    """Régression de l'outil : l'agrandissement des polices se cumulait d'une fenêtre à l'autre (sous-processus)."""
    done = _run_in_subprocess(["-c", _SCALE_FONTS_SCRIPT], tmp_path)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"{done.stdout[-1500:]}\n{done.stderr[-1500:]}"


def test_graph_editor_can_be_narrower_than_a_small_screen(window_at):
    """Son minimum (913 px) empêchait de la réduire ; la zone d'aide, non tronquée, fixait la largeur."""
    window = window_at((1280, 720))
    editor = window.open_graph_editor()
    audit.settle(window)
    assert editor.minimumSizeHint().width() <= 900, f"largeur minimale de l'éditeur : {editor.minimumSizeHint().width()} px"
    editor.resize(100, 100)
    audit.settle(window)
    assert editor.width() <= 900, f"largeur de l'éditeur réduit au minimum : {editor.width()} px"
    _assert_none(audit.sibling_overlaps(editor), "éditeur de courbes : chevauchement :")
    editor.hide()


def test_export_page_fits_at_the_smallest_size(window_at):
    window = window_at((1180, 720))
    window.show_export()
    audit.settle(window)
    try:
        for scroll in window.export_panel.findChildren(QScrollArea):
            _assert_none(audit.scroll_clipping(scroll), "page d'export coupée :")
        _assert_none(audit.parent_overflow(window.export_panel), "page d'export : enfant hors de son parent :")
    finally:
        window.show_editor()


def test_the_audit_scratch_directory_cleanup_never_decides_the_verdict(monkeypatch):
    """Régression Windows : un fichier momentanément verrouillé (``WinError 32``) à la sortie du dossier jetable faisait
    échouer l'audit sans le moindre constat, le résumé n'étant imprimé qu'après ce bloc."""
    import os

    from tools.ui_audit import scratch_directory

    real_unlink = os.unlink

    def locked(path, *args, **kwargs):
        if os.path.basename(str(path)) == ".blank-960x540.1.tmp.png":
            raise PermissionError(32, "Le processus ne peut pas accéder au fichier car ce fichier est utilisé par un autre processus")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", locked)
    with scratch_directory() as scratch:
        (Path(scratch) / "cache" / "mograph").mkdir(parents=True)
        (Path(scratch) / "cache" / "mograph" / ".blank-960x540.1.tmp.png").write_bytes(b"png")
    # arrivé ici : la sortie du bloc n'a rien levé

