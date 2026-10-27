"""Lisibilité sur un portable (MacBook Air 13") avec un clip sélectionné : pages Couleur et Audio.

Les gardes de ``test_ui_small_screens`` ouvrent la fenêtre **sans clip sélectionné** : ils n'ont vu ni la page Couleur qui
relevait le minimum de la fenêtre à 974 px (plus que l'écran : le Dock passait par-dessus), ni les étiquettes superposées du
qualifieur (une grille), ni le mixeur coupé, ni les boutons tronqués. Ces tests jouent les pages avec un clip et vérifient :

* le minimum de la fenêtre et sa taille réelle ne dépassent pas la zone utilisable ;
* ``tools.ui_legibility`` ne trouve ni chevauchement, ni contenu coupé, ni bouton tronqué ;
* le mixeur montre, au minimum de la fenêtre, le fader et le vumètre de sa première piste (lignes compactes) ;
* la liste de la bibliothèque a des lignes visibles, pas seulement une hauteur sous le bord.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QScrollArea

from tools import ui_audit as audit
from tools import ui_legibility as legibility

#: Fenêtres jouées : l'Air réglé en 1440x900 (zone utilisable ~1440x790), l'Air réglé en 1280x800, la fenêtre réelle de la capture.
SIZES = [(1440, 790), (1280, 690), (1222, 738)]
SIZE_IDS = [f"{w}x{h}" for w, h in SIZES]
MAX_MINIMUM_HEIGHT = 720          # comme test_ui_small_screens.MAX_MINIMUM


@pytest.fixture(scope="module")
def windows(tmp_path_factory):
    from qt_cleanup import deliver_deferred_deletes
    from ui.main_window_mixins.project_files import ProjectFilesMixin

    patch = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("legibility")
    patch.setenv("KUT_STUDIO_CONFIG_DIR", str(base / "config"))
    patch.setenv("KUT_STUDIO_CACHE_DIR", str(base / "cache"))
    patch.setenv("KUT_STUDIO_PROXY_DIR", str(base / "proxies"))
    patch.setenv("KUT_STUDIO_HARDWARE_ENCODING", "off")
    patch.setattr(ProjectFilesMixin, "_confirm_discard_changes", lambda self: True)
    made: dict[tuple[int, int], object] = {}

    def make(size: tuple[int, int]):
        if size not in made:
            window = audit.make_main_window(*size, scopes=False)
            audit.select_first_clip(window)           # un clip sélectionné : c'est ce qui manquait aux gardes d'origine
            made[size] = window
        window = made[size]
        window.resize(*size)
        audit.settle(window)
        return window

    yield make
    try:
        for window in list(made.values()):
            window.close()
            window.deleteLater()
            deliver_deferred_deletes()
        made.clear()
    finally:
        patch.undo()


@pytest.mark.parametrize("size", SIZES, ids=SIZE_IDS)
@pytest.mark.parametrize("page", ["color", "audio", "edit", "composition"])
def test_a_selected_clip_does_not_raise_the_window_minimum(windows, size, page):
    """Avec un clip, le minimum de la page Couleur était de 974 px : la fenêtre dépassait l'écran et passait sous le Dock."""
    window = windows(size)
    window.switch_page(page)
    audit.settle(window)
    assert window.minimumHeight() <= MAX_MINIMUM_HEIGHT, f"{page} : minimum {window.minimumHeight()} px"
    assert window.height() <= size[1], f"{page} : la fenêtre est passée de {size[1]} à {window.height()} px"


@pytest.mark.parametrize("size", SIZES, ids=SIZE_IDS)
def test_color_and_audio_pages_are_legible(windows, size):
    """Aucun chevauchement (grille et retour à la ligne compris), aucun conteneur coupé, aucun bouton tronqué."""
    window = windows(size)
    problems: list[str] = []
    for name, action in legibility.page_scenarios(window):
        action()
        audit.settle(window)
        problems += [str(f) for f in legibility.legibility_findings(window, f"{size[0]}x{size[1]} {name} ") if f.blocking]
    window.switch_page("edit")
    audit.settle(window)
    assert not problems, "\n".join(sorted(set(problems))[:10])


def test_qualifier_labels_sit_above_their_fields_and_the_tab_scrolls(windows):
    """Qualifieur : l'étiquette est au-dessus de son champ (jamais dessous), et un onglet trop haut défile."""
    window = windows((1222, 738))
    window.switch_page("color")
    panel = window.color_panel
    panel.tabs.setCurrentIndex(1)
    audit.settle(window)
    editor = panel.qualifier
    for name, label in editor.field_labels.items():
        spin = editor.spins[name]
        assert label.geometry().bottom() <= spin.geometry().top() + 1, name
    assert panel.tabs.currentWidget() is editor
    assert isinstance(editor.parentWidget().parentWidget(), QScrollArea)          # l'onglet est enveloppé dans une zone défilante
    assert panel.tabs.minimumSizeHint().height() <= 80
    panel.tabs.setCurrentIndex(0)


def _fully_visible(widget, viewport) -> bool:
    top_left = widget.mapTo(viewport, QPoint(0, 0))
    return viewport.rect().contains(QRect(top_left, widget.size()))


@pytest.mark.parametrize("size", SIZES, ids=SIZE_IDS)
def test_mixer_shows_fader_and_meter_of_the_first_track_at_the_window_minimum(windows, size):
    """À la plus petite fenêtre, le mixeur montre le fader et le vumètre de A1 (avant : seuls le nom et « FX »)."""
    window = windows(size)
    window.switch_page("audio")
    window.resize(window.minimumSize())
    audit.settle(window)
    try:
        panel = window.mixer_panel
        assert panel.isVisible() and panel.strip_count() >= 2
        strip = panel.strip_for("A1")
        assert strip is not None and panel.is_compact()
        viewport = panel.scroll.viewport()
        for part in (strip.name_label, strip.inserts_button, strip.volume_slider, strip.meter, strip.mute_button):
            assert _fully_visible(part, viewport), f"{type(part).__name__} coupé : {part.mapTo(viewport, QPoint(0, 0))} {part.size()}"
        second = panel.strip_for("A2")
        assert second is not None and _fully_visible(second.volume_slider, viewport), "deux pistes lisibles sans défiler"
    finally:
        window.resize(*size)
        audit.settle(window)


def test_mixer_switches_between_columns_and_rows_with_its_height(qtbot):
    """Panneau haut : tranches verticales (fader vertical) ; bas : une ligne par piste (fader horizontal) ; mêmes signaux."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from core.project_model import Project, Track
    from ui.mixer_panel import MixerPanel

    project = Project(name="m")
    project.tracks += [Track("A1", "A1", "audio"), Track("A2", "A2", "audio")]
    panel = MixerPanel()
    qtbot.addWidget(panel)
    panel.set_project(project)
    panel.resize(900, 520)
    panel.show()
    qtbot.wait(30)
    assert not panel.is_compact()
    assert panel.strip_for("A1").volume_slider.orientation() == Qt.Vertical
    panel.resize(900, 180)
    qtbot.waitUntil(panel.is_compact, timeout=2000)
    QApplication.processEvents()
    strip = panel.strip_for("A1")
    assert strip is not None and strip.volume_slider.orientation() == Qt.Horizontal and strip.meter.orientation == Qt.Horizontal
    seen = []
    panel.volume_changed.connect(lambda track_id, value: seen.append((track_id, value)))
    strip.volume_slider.setValue(-6)
    assert seen == [("A1", -6.0)]
    panel.resize(900, 520)
    qtbot.waitUntil(lambda: not panel.is_compact(), timeout=2000)
    assert panel.strip_for("A1").volume_slider.orientation() == Qt.Vertical


def _visible_height(widget, host_viewport) -> int:
    """Hauteur du widget réellement visible dans la zone défilante qui le contient."""
    top = widget.mapTo(host_viewport, QPoint(0, 0)).y()
    return max(0, min(top + widget.height(), host_viewport.height()) - max(top, 0))


@pytest.mark.parametrize("size", SIZES, ids=SIZE_IDS)
@pytest.mark.parametrize("section", ["media", "audio"])
def test_library_list_has_visible_rows(windows, size, section):
    """Médias / Audio : au moins une ligne et demie de la liste est **visible** (avant : dossiers puis liste sous le bord, 0 px).

    Le plancher est celui de l'Air réglé en 1280x800 (colonne de ~215 px : en-tête, recherche, onglets Fichiers / Effets
    audio / SFX et boutons d'action en prennent plus de la moitié) ; la liste défile pour le reste.
    """
    window = windows(size)
    window.switch_page("audio")
    window.project_panel.select_section(section)
    audit.settle(window)
    panel = window.project_panel
    view = (panel.bin_videos if section == "media" else panel.bin_audios)
    viewport = panel.findChild(QScrollArea, "scrollGuard").viewport() if panel.findChild(QScrollArea, "scrollGuard") else panel
    visible = _visible_height(view, viewport)
    assert visible >= 64, f"{section} : {visible} px de liste visibles"
    window.project_panel.select_section("media")


def test_library_browse_fold_follows_height_then_the_user_choice(windows):
    """Le bloc des dossiers se replie quand la colonne est basse ; le bouton rouvre, et ce choix l'emporte ensuite."""
    window = windows((1222, 738))
    window.switch_page("edit")
    panel = window.project_panel
    panel.select_section("media")
    audit.settle(window)
    fold = panel.browse_fold
    assert not fold.expanded                         # colonne basse : replié tout seul
    panel.browse_toggle.click()
    assert fold.expanded
    audit.settle(window)
    window.resize(1222, 740)
    audit.settle(window)
    assert fold.expanded                             # le choix de l'utilisateur est respecté
    panel.browse_toggle.click()
    assert not fold.expanded
