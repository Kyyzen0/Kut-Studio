"""Changer de thème en direct doit donner la même interface qu'un démarrage dans ce thème.

Avant ``ui/theming.py``, un passage du sombre au clair ne rattrapait que la feuille de style globale : tout widget qui avait construit
un style local avec les couleurs du thème d'origine y restait, et la moitié de l'interface montrait du texte sombre sur fond sombre.

La preuve est **objective** : on capture la fenêtre après un basculement en direct, on la compare pixel à pixel à une fenêtre démarrée
dans le thème visé. Les deux passent par ``_apply_settings`` (même re-traduction), seul le chemin du thème diffère. Un test ne dit pas
que le thème est beau, seulement qu'il est le même dans les deux chemins.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtGui import QImage

import ui.main_window as main_window_module
from tools import ui_audit as audit
from tools.capture_ui import choose_theme
from ui.theme import THEMES
from ui.theming import ambiguities, color_map, remap_stylesheet

SIZE = (1440, 900)
AUDIO_BAND = (845, 900)
"""Lignes de la piste audio : la hauteur des barres de la forme d'onde dépend de la hauteur du clip au moment de la peinture (un
effet de mise en page de quelques pixels), pas du thème : sa couleur est vérifiée à part, elle est identique."""
REGIONS = {
    "bandeau du haut": (0, 75, 0, 1440),
    "rail latéral": (75, 640, 0, 52),
    "bibliothèque": (75, 640, 52, 340),
    "inspecteur": (75, 640, 1072, 1440),
    "barre d'outils de la timeline": (640, 740, 0, 1440),
    "en-têtes de pistes": (740, 900, 0, 272),
    "clips vidéo": (740, 845, 272, 1440),
}


def _colours(pixels: np.ndarray) -> set[tuple[int, ...]]:
    return {tuple(int(v) for v in colour) for colour in np.unique(pixels.reshape(-1, 3), axis=0)}


def _significant_colours(pixels: np.ndarray, minimum: float = 0.01) -> set[tuple[int, ...]]:
    """Couleurs qui couvrent au moins ``minimum`` de la zone (l'antialiasing des bords reste en dessous)."""
    colours, counts = np.unique(pixels.reshape(-1, 3), axis=0, return_counts=True)
    return {tuple(int(v) for v in colour) for colour, count in zip(colours, counts) if count >= minimum * counts.sum()}


def _pixels(image: QImage) -> np.ndarray:
    image = image.convertToFormat(QImage.Format_RGBA8888)
    data = np.frombuffer(image.constBits(), np.uint8)
    return data.reshape(image.height(), image.bytesPerLine() // 4, 4)[:, : image.width()].astype(int)


_PATCH = pytest.MonkeyPatch()
"""Pose les variables d'environnement de la configuration jetable ; ``captures`` le défait à la fin du module."""


def _capture(start: str, end: str) -> np.ndarray:
    """La fenêtre démarrée dans ``start`` puis basculée en ``end`` (ou démarrée directement dans ``end`` si les deux sont égaux).

    Chaque fenêtre part d'une **configuration neuve** : sans cela, la disposition des panneaux laissée par la fenêtre précédente
    (réécrite sur disque à chaque redimensionnement) donnait des colonnes plus larges de quelques pixels à la suivante, et la
    comparaison échouait sur toute machine sans réglages enregistrés, donc en CI, et jamais sur un poste de développement."""
    audit.isolate_user_config(_PATCH)
    choose_theme(start)
    window = audit.make_main_window(*SIZE, scopes=False)
    try:
        audit.select_first_clip(window)
        window._apply_settings(replace(window._settings_snapshot(), theme_mode=end))
        audit.settle(window)
        return _pixels(window.grab().toImage())
    finally:
        window.close()


@pytest.fixture(scope="module")
def captures():
    original = main_window_module.ThemeManager
    patch = pytest.MonkeyPatch()
    patch.setattr(main_window_module, "save_user_settings", lambda settings: None)         # jamais les vrais réglages de l'utilisateur
    try:
        yield {
            "dark→light": (_capture("dark", "light"), _capture("light", "light")),
            "light→dark": (_capture("light", "dark"), _capture("dark", "dark")),
        }
    finally:
        patch.undo()
        _PATCH.undo()
        main_window_module.ThemeManager = getattr(main_window_module, "_capture_original_theme_manager", original)


# ---------------------------------------------------------------------------
# La substitution des couleurs
# ---------------------------------------------------------------------------


def test_the_colour_mapping_between_the_themes_is_unambiguous_in_both_directions():
    """Deux jetons de rôles différents ne partagent jamais la même valeur dans une palette : sinon la re-teinte devinerait mal."""
    assert ambiguities(THEMES["dark"], THEMES["light"]) == {}
    assert ambiguities(THEMES["light"], THEMES["dark"]) == {}


def test_remapping_a_stylesheet_swaps_palette_colours_keeps_alpha_and_leaves_the_rest_alone():
    dark, light = THEMES["dark"], THEMES["light"]
    mapping = color_map(dark, light)
    sheet = f"QWidget {{ background: {dark.panel}; color: {dark.text.lower()}; border: 1px solid {dark.accent}80; }} QLabel {{ color: #123456; }}"
    swapped = remap_stylesheet(sheet, mapping)
    assert light.panel.upper() in swapped.upper() and light.text.upper() in swapped.upper()
    assert f"{light.accent.upper()}80" in swapped.upper()                          # la transparence du widget est conservée
    assert "#123456" in swapped                                                    # une couleur hors palette n'est jamais touchée
    assert dark.panel.upper() not in swapped.upper()


def test_remapping_there_and_back_gives_the_original_stylesheet():
    dark, light = THEMES["dark"], THEMES["light"]
    sheet = " ".join(f"{name}:{getattr(dark, name)};" for name in ("background", "panel", "surface", "text", "muted", "accent", "border", "danger"))
    there = remap_stylesheet(sheet, color_map(dark, light))
    assert there != sheet
    assert remap_stylesheet(there, color_map(light, dark)).upper() == sheet.upper()


# ---------------------------------------------------------------------------
# Le résultat à l'écran
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("direction", ["dark→light", "light→dark"])
def test_a_live_theme_switch_looks_like_a_fresh_start_in_that_theme(captures, direction):
    live, fresh = captures[direction]
    different = np.abs(live - fresh).max(axis=2) > 24
    different[AUDIO_BAND[0]:AUDIO_BAND[1], 272:] = False                       # la forme d'onde : voir ``AUDIO_BAND``
    failures = {name: int(different[y0:y1, x0:x1].sum()) for name, (y0, y1, x0, x1) in REGIONS.items()
                if different[y0:y1, x0:x1].sum() > 0}
    # Le rail, l'inspecteur, la bibliothèque, les en-têtes et les clips sont strictement identiques ; il ne reste, au plus, que
    # quelques pixels d'antialiasing ailleurs (la visionneuse).
    for strict in ("rail latéral", "inspecteur", "barre d'outils de la timeline", "en-têtes de pistes", "clips vidéo"):
        assert failures.get(strict, 0) == 0, f"{direction} : {strict} diffère ({failures})"
    assert different.mean() < 0.003, f"{direction} : {different.mean() * 100:.2f} % de pixels diffèrent ({failures})"


@pytest.mark.parametrize("direction", ["dark→light", "light→dark"])
def test_the_waveform_keeps_the_colours_of_the_target_theme_after_a_live_switch(captures, direction):
    """La hauteur des barres peut varier de quelques pixels ; leur couleur et celle du fond du clip, jamais."""
    live, fresh = captures[direction]
    band = (slice(*AUDIO_BAND), slice(300, 1400), slice(0, 3))

    # Des **présences**, pas un classement : les deux couleurs les plus fréquentes basculaient avec la hauteur des barres
    # (barres claires ≈ 18 % de la bande, fond du clip ≈ 13 % : quelques pixels de moins et le fond passe devant). Une fuite
    # de thème, elle, peint une couleur de l'autre thème, absente de la fenêtre démarrée dans le thème visé.
    significant_live, significant_fresh = _significant_colours(live[band]), _significant_colours(fresh[band])
    assert significant_live <= _colours(fresh[band]), f"{direction} : couleurs absentes d'un démarrage à neuf"
    assert significant_fresh <= _colours(live[band]), f"{direction} : couleurs du thème visé manquantes"
    assert len(significant_fresh) >= 3, "fond du clip, barres et leur teinte claire : la bande n'est pas vide"
