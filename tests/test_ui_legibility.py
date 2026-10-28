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
SIZES = [(1440, 900), (1440, 790), (1280, 690), (1222, 738)]
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
    """Médias / Audio : au moins une grosse ligne de la liste est **visible** (avant : dossiers puis liste sous le bord, 0 px).

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
    assert visible >= 56, f"{section} : {visible} px de liste visibles"
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


@pytest.mark.parametrize("scale", [1.2, 1.45], ids=["police+20%", "police+45%"])
def test_no_button_is_elided_with_wider_fonts(windows, scale):
    """Polices de repli plus larges (Linux) : aucun bouton des pages Couleur et Audio n'est tronqué (il passe à la ligne)."""
    from qt_cleanup import deliver_deferred_deletes

    window = audit.make_main_window(1280, 690, scopes=False, font_scale=scale)
    try:
        audit.select_first_clip(window)
        problems: list[str] = []
        for name, action in legibility.page_scenarios(window):
            action()
            audit.settle(window)
            problems += [str(f) for f in legibility.elided_buttons(window, f"x{scale} {name} ")]
            if name == "couleur-roues":
                problems += _wheel_problems(window.color_panel, f"x{scale}")
        assert not problems, "\n".join(sorted(set(problems))[:10])
    finally:
        from PySide6.QtWidgets import QApplication

        window.close()
        window.deleteLater()
        deliver_deferred_deletes()
        app = QApplication.instance()
        base = app.property("_kut_audit_base_stylesheet")
        if base is not None:
            app.setStyleSheet(base)                    # la feuille de style agrandie ne doit pas survivre aux autres tests


# ---------------------------------------------------------------------------------------------------------------
# Page Couleur « Air d'abord » : roues entières, bande de nœuds, bande des plans à la place de la timeline
# ---------------------------------------------------------------------------------------------------------------

#: Diamètre minimal d'une roue (px) : en dessous, le palet ne se règle plus à la souris. 1440x900 : ~150 px.
MIN_WHEEL = 120
#: Marge exigée au-delà du seuil sur les fenêtres les plus petites (Linux et Windows ont des polices plus larges que macOS).
SLACK = 8


def _colour_page(window, tab: int = 0, timeline: bool = False):
    window.switch_page("color")
    window.clip_strip.timeline_button.setChecked(timeline)
    window.color_panel.tabs.setCurrentIndex(tab)
    audit.settle(window)
    return window.color_panel


def _wheel_problems(panel, label: str) -> list[str]:
    """Les quatre roues : entières dans la zone visible de l'onglet (sans défiler), disque de ``MIN_WHEEL`` px au moins."""
    problems: list[str] = []
    guard = panel.tabs.currentWidget().parentWidget().parentWidget()
    viewport = guard.viewport()
    if guard.verticalScrollBar().maximum() > 0 or guard.horizontalScrollBar().maximum() > 0:
        problems.append(f"{label} : l'onglet des roues défile")
    for name, wheel in panel.wheels.wheels.items():
        if not _fully_visible(wheel, viewport):
            problems.append(f"{label} {name} : roue coupée {wheel.mapTo(viewport, QPoint(0, 0))} {wheel.size()} dans {viewport.size()}")
        diameter = wheel._disc()[1] * 2
        if diameter < MIN_WHEEL + (SLACK if viewport.window().width() >= 1280 else 0):      # 1222 px : 514 px pour quatre roues, pas plus
            problems.append(f"{label} {name} : disque de {diameter:.0f} px")
    return problems


@pytest.mark.parametrize("size", SIZES, ids=SIZE_IDS)
def test_the_four_wheels_are_whole_and_at_least_120_px(windows, size):
    """Quatre roues côte à côte, entières, jamais défilées ni coupées ; ~150 px sur un 1440x900, 120 px au moins au plus petit."""
    window = windows(size)
    panel = _colour_page(window)
    try:
        assert panel.wheels._columns == 4
        assert not _wheel_problems(panel, f"{size[0]}x{size[1]}"), _wheel_problems(panel, "")
        if size == (1440, 900):
            assert min(w._disc()[1] * 2 for w in panel.wheels.wheels.values()) >= 150 - 2
        for name, cell in panel.wheels.cells.items():                      # le losange d'images clés reste sous la roue
            assert _fully_visible(cell.keys.inner, panel.tabs.currentWidget().parentWidget()), name
    finally:
        window.switch_page("edit")
        audit.settle(window)


@pytest.mark.parametrize("size", SIZES, ids=SIZE_IDS)
def test_the_node_graph_is_a_legible_strip(windows, size):
    """Nœuds : une bande d'environ 88 px (pas 112 ni 400) à l'échelle 1, nœud et bornes lisibles."""
    window = windows(size)
    panel = _colour_page(window)
    try:
        from PySide6.QtCore import Qt

        assert panel.splitter.orientation() == Qt.Vertical
        assert 84 <= panel.nodes.height() <= 100, panel.nodes.height()
        assert panel.nodes.transform().m11() >= 0.9, panel.nodes.transform().m11()          # jamais réduit à une vignette
        assert panel.tabs.height() >= 280, panel.tabs.height()                              # le reste va aux roues
    finally:
        window.switch_page("edit")
        audit.settle(window)


def test_the_strip_replaces_the_timeline_and_the_panel_gets_the_height(windows):
    """1440x900 : bande des plans de ~150 px à la place de la timeline, colonne de droite d'au moins 640 px de haut."""
    window = windows((1440, 900))
    panel = _colour_page(window)
    try:
        assert not window.timeline_panel.isVisible() and window.clip_strip.isVisible()
        assert 146 <= window.clip_strip.height() <= 160, window.clip_strip.height()
        assert panel.height() >= 640, panel.height()
    finally:
        window.switch_page("edit")
        audit.settle(window)


@pytest.mark.parametrize("size", SIZES, ids=SIZE_IDS)
def test_show_timeline_button_toggles_and_is_idempotent(windows, size):
    """« Afficher la timeline » rouvre la timeline sous la page ; recliquer la replie ; la répéter ne change rien."""
    from core.workspace_state import PanelId

    window = windows(size)
    panel = _colour_page(window)
    button = window.clip_strip.timeline_button
    try:
        assert button.text() == "Afficher la timeline" and button.isCheckable() and not button.isChecked()
        assert button.width() >= button.sizeHint().width() - 1                       # libellé entier, jamais élidé
        button.setChecked(True)
        audit.settle(window)
        assert window.timeline_panel.isVisible() and window.clip_strip.isVisible()
        assert window.height() <= size[1] and window.minimumHeight() <= MAX_MINIMUM_HEIGHT
        assert not legibility.overlapping_siblings(window)
        button.setChecked(True)                                                       # idempotent
        window.workspace.set_panel_visible(PanelId.TIMELINE, True)                    # idem par le gestionnaire de panneaux
        audit.settle(window)
        assert window.timeline_panel.isVisible() and button.isChecked()
        button.setChecked(False)
        audit.settle(window)
        assert not window.timeline_panel.isVisible() and window.clip_strip.isVisible() and not button.isChecked()
        button.setChecked(False)
        audit.settle(window)
        assert not window.timeline_panel.isVisible()
        assert panel.height() >= (640 if size == (1440, 900) else 400)
    finally:
        button.setChecked(False)
        window.switch_page("edit")
        audit.settle(window)


def test_the_button_follows_the_timeline_opened_elsewhere(windows):
    """La timeline rouverte par un autre chemin (menu Fenêtre) coche le bouton, sans l'émettre en retour."""
    from core.workspace_state import PanelId

    window = windows((1440, 900))
    _colour_page(window)
    button = window.clip_strip.timeline_button
    try:
        window.workspace.set_panel_visible(PanelId.TIMELINE, True)
        audit.settle(window)
        assert button.isChecked()
        window.workspace.set_panel_visible(PanelId.TIMELINE, False)
        audit.settle(window)
        assert not button.isChecked()
    finally:
        window.switch_page("edit")
        audit.settle(window)


def test_the_timeline_choice_is_a_session_choice():
    """La page Couleur démarre sans timeline, et ne l'enregistre jamais : ni d'origine, ni dans une disposition ancienne."""
    from core.workspace_state import (
        PAGE_COLOR,
        PAGE_EDIT,
        DockArea,
        PanelId,
        color_page_session_state,
        page_default_state,
    )

    state = page_default_state(PAGE_COLOR)
    assert not state.is_visible(PanelId.TIMELINE) and state.is_visible(PanelId.CLIPS)
    assert state.area_of(PanelId.CLIPS) is DockArea.BOTTOM
    assert page_default_state(PAGE_EDIT).is_visible(PanelId.TIMELINE)                  # le Montage ne change pas
    old = state.with_panel(PanelId.TIMELINE, visible=True)                            # disposition d'avant : timeline + bande empilées
    assert not color_page_session_state(old).is_visible(PanelId.TIMELINE)
    assert color_page_session_state(old).is_visible(PanelId.CLIPS)


@pytest.mark.parametrize("size", SIZES, ids=SIZE_IDS)
def test_qualifier_tab_has_titles_bars_fields_and_whole_buttons(windows, size):
    """Qualificateur : par plage un titre, la barre de couleur et trois champs de 34 px sous leur étiquette ; boutons entiers."""
    window = windows(size)
    panel = _colour_page(window, tab=1)
    try:
        editor = panel.qualifier
        guard = editor.parentWidget().parentWidget()
        for channel in ("hue", "sat", "lum"):
            assert editor.channel_checks[channel].text() and editor.bars[channel].height() >= 12
        for name, label in editor.field_labels.items():
            spin = editor.spins[name]
            assert label.geometry().bottom() <= spin.geometry().top() + 1, name              # l'étiquette est AU-DESSUS
            assert label.geometry().intersected(spin.geometry()).isEmpty(), name
            assert spin.height() >= 34, (name, spin.height())
            assert label.width() >= label.fontMetrics().horizontalAdvance(label.text()), name         # étiquette entière
        for button in (editor.pick_button, editor.highlight_button):
            assert button.isVisible() and button.width() >= button.sizeHint().width() - 1, button.text()
        assert guard.horizontalScrollBar().maximum() == 0
        problems = [str(f) for f in legibility.legibility_findings(window, "q ") if f.blocking]
        assert not problems, problems[:5]
    finally:
        panel.tabs.setCurrentIndex(0)
        window.switch_page("edit")
        audit.settle(window)


def test_library_list_keeps_a_height_margin_with_taller_fonts():
    """Police +20 % (plus haute que celle de Windows / Linux) : la liste audio garde plus que le plancher de 56 px.

    L'en-tête, la recherche et les onglets Audio resserrent leurs marges verticales dans une colonne basse (``COMPACT_HEIGHT``) :
    la marge ne dépend plus d'un réglage au pixel par plateforme (51 px sur macOS, 53 sur Linux, 54 sur Windows avant).
    """
    from PySide6.QtWidgets import QApplication

    from qt_cleanup import deliver_deferred_deletes

    window = audit.make_main_window(1280, 690, scopes=False, font_scale=1.2)
    try:
        audit.select_first_clip(window)
        for _name, action in legibility.page_scenarios(window):
            action()
            audit.settle(window)
        window.switch_page("edit")
        audit.settle(window)
        window.switch_page("audio")
        window.project_panel.select_section("audio")
        audit.settle(window)
        panel = window.project_panel
        viewport = panel.findChild(QScrollArea, "scrollGuard").viewport()
        assert _visible_height(panel.bin_audios, viewport) >= 60
    finally:
        window.close()
        window.deleteLater()
        deliver_deferred_deletes()
        app = QApplication.instance()
        base = app.property("_kut_audit_base_stylesheet")
        if base is not None:
            app.setStyleSheet(base)
