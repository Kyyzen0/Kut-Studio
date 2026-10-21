"""Lisibilité sur un écran de portable (MacBook Air 13" : 1280x800, 1440x900) : fenêtre et panneaux ne se marchent pas dessus.

Hors écran, rapides, déterministes (aucun seuil de temps, aucune comparaison de pixels). Les assertions sont structurelles :

* la taille minimale de la fenêtre, page par page, tient dans 1280x720 (la zone utilisable d'un Air 13" réglé en 1280x800 est
  de ~1280x690 : le Dock passait par-dessus la timeline quand le minimum la dépassait) ;
* aucun widget visible ne chevauche un frère rangé par le même layout, aucune page ne déborde de son hôte ;
* les listes principales des bibliothèques gardent au moins ``Sizes.library_list_min_height`` (3 lignes) ;
* la timeline garde au moins trois pistes visibles ;
* une géométrie enregistrée sur un grand écran (3000x2000) est bornée à la zone utilisable de l'écran courant.

Les captures avant / après : ``python -m tools.ui_layout_audit --out DIR`` (voir ``docs/ui-layout.md``).
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QScrollArea

from core.window_fit import fit_rect
from core.workspace_state import MIN_HEIGHT, MIN_SIZE, PAGES, FloatingGeometry, PanelId
from tools import ui_audit as audit
from tools.ui_layout_audit import LIBRARY_SECTIONS, SCREENS, usable_area
from ui.design_system import Sizes

#: Plus grande fenêtre minimale acceptée (largeur, hauteur) : la zone utilisable d'un écran 1280x800 est de ~1280x690,
#: 1280x720 est le minimum « raisonnable » de la demande.
MAX_MINIMUM = (1280, 720)
SCREEN_IDS = [f"{w}x{h}" for w, h in SCREENS]
LEVELS = pytest.mark.parametrize("usage_level", audit.USAGE_LEVELS)
PAGE_PARAMS = pytest.mark.parametrize("page", PAGES)


@pytest.fixture(scope="module")
def screen_window(tmp_path_factory):
    """Fenêtre principale à la taille **utilisable** de l'écran demandé, une par écran et par module (niveau changé à chaud)."""
    from ui.main_window_mixins.project_files import ProjectFilesMixin

    patch = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("small-screens")
    patch.setenv("KUT_STUDIO_CONFIG_DIR", str(base / "config"))
    patch.setenv("KUT_STUDIO_CACHE_DIR", str(base / "cache"))
    patch.setenv("KUT_STUDIO_PROXY_DIR", str(base / "proxies"))
    patch.setenv("KUT_STUDIO_HARDWARE_ENCODING", "off")
    patch.setattr(ProjectFilesMixin, "_confirm_discard_changes", lambda self: True)
    windows: dict[tuple[int, int], object] = {}

    def make(screen: tuple[int, int], usage_level: str = "advanced", page: str = "edit"):
        if screen not in windows:
            windows[screen] = audit.make_main_window(*usable_area(screen), scopes=True)
        window = windows[screen]
        window.set_usage_level(usage_level)
        window.switch_page(page)
        window.project_panel.select_section("media")
        audit.settle(window)
        return window

    yield make
    for window in windows.values():
        window.close()
    patch.undo()


def _blocking(findings) -> list[str]:
    return [str(finding) for finding in findings if finding.blocking]


@pytest.mark.parametrize("screen", SCREENS, ids=SCREEN_IDS)
@PAGE_PARAMS
@LEVELS
def test_window_minimum_fits_a_laptop_screen(screen_window, screen, page, usage_level):
    """(c) Le minimum de la fenêtre, page par page, ne dépasse pas 1280x720 : sinon le Dock recouvre la timeline."""
    window = screen_window(screen, usage_level, page)
    hint, minimum = window.minimumSizeHint(), window.minimumSize()
    for name, size in (("minimumSizeHint", hint), ("minimumSize", minimum)):
        assert size.width() <= MAX_MINIMUM[0] and size.height() <= MAX_MINIMUM[1], f"{page} : {name} {size.width()}x{size.height()}"


@pytest.mark.parametrize("screen", SCREENS, ids=SCREEN_IDS)
@PAGE_PARAMS
@LEVELS
def test_nothing_overlaps_on_any_page(screen_window, screen, page, usage_level):
    """(a) Aucun widget visible ne chevauche un frère du même layout, aucun ne sort de son hôte, page par page."""
    window = screen_window(screen, usage_level, page)
    label = f"{screen[0]}x{screen[1]} {usage_level} {page}"
    problems = _blocking(audit.sibling_overlaps(window, label + " ")) + _blocking(audit.parent_overflow(window, label + " "))
    assert not problems, "\n".join(problems[:8])


@pytest.mark.parametrize("screen", SCREENS, ids=SCREEN_IDS)
@pytest.mark.parametrize("section", LIBRARY_SECTIONS)
@LEVELS
def test_no_overlap_in_any_library_panel(screen_window, screen, section, usage_level):
    """(a) Les panneaux de gauche (Médias, Effets, Titres, Audio, Transitions, Calques, Séquences) ne se chevauchent pas."""
    window = screen_window(screen, usage_level)
    window.project_panel.select_section(section)
    audit.settle(window)
    problems = _blocking(audit.sibling_overlaps(window.project_panel, f"{screen} {section} "))
    assert not problems, "\n".join(problems[:8])


@pytest.mark.parametrize("screen", SCREENS, ids=SCREEN_IDS)
def test_nothing_overlaps_when_the_window_is_at_its_smallest(screen_window, screen):
    """(a) À la plus petite taille permise, les panneaux défilent au lieu de se chevaucher (la bannière sur la liste)."""
    window = screen_window(screen)
    for page in PAGES:
        window.switch_page(page)
        window.resize(window.minimumSize())
        for section in LIBRARY_SECTIONS:
            window.project_panel.select_section(section)
            audit.settle(window)
            problems = _blocking(audit.sibling_overlaps(window, f"min {page} {section} "))
            assert not problems, "\n".join(problems[:8])
    window.resize(*usable_area(screen))
    audit.settle(window)


@pytest.mark.parametrize("screen", SCREENS, ids=SCREEN_IDS)
@LEVELS
def test_main_lists_keep_at_least_three_rows(screen_window, screen, usage_level):
    """(b) La liste de préréglages (Transitions, Effets) garde ``library_list_min_height`` : plus de « une ligne et demie »."""
    window = screen_window(screen, usage_level)
    panel = window.project_panel
    for section, view in (("transitions", panel.transition_view), ("effects", panel.effects_view)):
        panel.select_section(section)
        audit.settle(window)
        assert view.scroll_area.height() >= Sizes.library_list_min_height, f"{section} : {view.scroll_area.height()} px"


@pytest.mark.parametrize("screen", SCREENS, ids=SCREEN_IDS)
def test_transitions_panel_has_no_empty_band_between_chips_and_list(screen_window, screen):
    """L'en-tête (recherche + puces) prend sa hauteur naturelle : ~70 px de vide entre les puces et la suite avant."""
    window = screen_window(screen)
    panel = window.project_panel
    panel.select_section("transitions")
    audit.settle(window)
    view = panel.transition_view
    header = view.filter_row.parentWidget()
    below_chips = header.height() - (view.filter_row.geometry().bottom() + 1)
    assert below_chips <= 16, f"{below_chips} px de vide sous les puces de filtre"
    # La bannière est dans la zone défilante, au-dessus des cartes : elle ne peut pas chevaucher la liste.
    assert view.selection_hint.parentWidget().parentWidget() is view.cards_host


@pytest.mark.parametrize("screen", SCREENS, ids=SCREEN_IDS)
@pytest.mark.parametrize("page", ["edit", "composition", "color", "audio"])
def test_timeline_shows_three_tracks_at_its_smallest(screen_window, screen, page):
    """La timeline garde une hauteur minimale utile : au moins trois pistes visibles (ou un défilement vertical clair)."""
    window = screen_window(screen, "advanced", page)
    window.resize(window.minimumSize())
    audit.settle(window)
    try:
        scrolls = [s for s in window.timeline_panel.findChildren(QScrollArea) if s.isVisible()]
        viewport = max(scroll.viewport().height() for scroll in scrolls)
        assert viewport >= 3 * window.timeline_panel.track_height, f"{page} : {viewport} px pour les pistes"
    finally:
        window.resize(*usable_area(screen))
        audit.settle(window)


def test_stacked_bottom_panels_use_their_own_minimum_height():
    """Le mixeur défile : empilé sous la timeline il ne réserve pas ses 320 px de largeur minimale (page Audio > 1000 px)."""
    assert MIN_HEIGHT[PanelId.MIXER] < MIN_SIZE[PanelId.MIXER]
    assert MIN_HEIGHT[PanelId.TIMELINE] >= Sizes.timeline_min_height


# ---------------------------------------------------------------------------
# (d) Géométrie bornée à l'écran courant
# ---------------------------------------------------------------------------


def test_fit_rect_clamps_a_4k_geometry_to_the_laptop_screen():
    """(d) Une géométrie de 3000x2000 enregistrée sur un grand écran est bornée à la zone utilisable, recentrée si hors écran."""
    available = (0, 25, 1440, 790)
    x, y, width, height = fit_rect((100, 100, 3000, 2000), available, frame=40)
    assert width <= 1440 and height <= 790 - 40
    assert x >= 0 and x + width <= 1440
    assert y >= 25 + 40 and y + height <= 25 + 790
    # Entièrement hors de l'écran (autre moniteur débranché) : elle revient dedans.
    x, y, width, height = fit_rect((5000, 4000, 800, 600), available, frame=40)
    assert 0 <= x and x + width <= 1440 and 25 <= y and y + height <= 25 + 790
    # Valeurs absurdes : jamais d'exception ni de fenêtre vide.
    assert fit_rect((0, 0, -5, 0), available)[2:] == (1, 1)


def test_main_window_opens_inside_the_usable_screen(screen_window):
    """(d) La fenêtre d'origine (1480x920) est bornée : elle ne dépasse plus un écran plus petit qu'elle."""
    from PySide6.QtGui import QGuiApplication

    from ui.main_window import MainWindow

    screen_window(SCREENS[0])                               # isole la configuration avant de construire une fenêtre
    window = MainWindow()
    try:
        area = QGuiApplication.primaryScreen().availableGeometry()
        geometry = window.geometry()
        assert geometry.width() <= max(area.width(), window.minimumWidth())
        assert geometry.height() <= max(area.height(), window.minimumHeight())
        assert geometry.left() >= area.left() and geometry.top() >= area.top()
    finally:
        window.close()


def test_a_floating_panel_saved_on_a_4k_screen_is_clamped_to_this_screen(screen_window):
    """(d) Un panneau détaché dont la position et la taille viennent d'un écran 4K revient dans l'écran courant."""
    from PySide6.QtGui import QGuiApplication

    window = screen_window(SCREENS[1])
    workspace = window.workspace
    state = workspace.state.with_panel(PanelId.HISTORY, floating=True, visible=True)
    state = state.with_floating_geometry(PanelId.HISTORY, FloatingGeometry(5000, 4000, 3000, 2000))
    assert state.floating_geometry(PanelId.HISTORY).width == 3000          # la géométrie de test est bien mémorisée
    workspace._state = state
    workspace.float_panel(PanelId.HISTORY)
    try:
        area = QGuiApplication.primaryScreen().availableGeometry()
        geometry = workspace._windows[PanelId.HISTORY].geometry()
        assert geometry.width() <= area.width() and geometry.height() <= area.height()
        assert area.left() <= geometry.left() and geometry.right() <= area.right() + 1
        assert area.top() <= geometry.top() and geometry.bottom() <= area.bottom() + 1
    finally:
        workspace.dock_panel(PanelId.HISTORY)


def test_guarded_stack_hides_the_wrapper_from_its_callers(screen_window):
    """Les pages restent atteignables par ``currentWidget`` / ``widget`` / ``indexOf`` (le reste du code ne voit pas l'enveloppe)."""
    window = screen_window(SCREENS[1])
    panel = window.project_panel
    panel.select_section("effects")
    assert panel.content_stack.currentWidget() is panel.effects_view
    index = panel.content_stack.indexOf(panel.transition_view)
    assert panel.content_stack.widget(index) is panel.transition_view


def test_shrinkable_scroll_area_hint_is_the_height_at_its_real_width(qtbot):
    """Le ``sizeHint`` d'un bloc à retour à la ligne se lit à la largeur réelle, pas à la largeur idéale (étroite, donc trop haute)."""
    from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

    from ui.adaptive_layout import FlowLayout, ShrinkableScrollArea

    content = QWidget()
    outer = QVBoxLayout(content)
    row = QWidget()
    flow = FlowLayout(row, spacing=4)
    for index in range(7):
        flow.addWidget(QPushButton(f"CATEGORIE {index}"))
    outer.addWidget(row)
    area = ShrinkableScrollArea(content, 40)
    qtbot.addWidget(area)
    area.resize(288, 300)
    area.show()
    qtbot.wait(10)
    width = area.viewport().width()
    assert area.sizeHint().height() == content.heightForWidth(width) + 2 * area.frameWidth()
