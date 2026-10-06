"""Les jetons de design : des échelles cohérentes, des thèmes complets, des contrastes lisibles, et rien d'arbitraire de plus.

Ce que ces tests **garantissent** : les valeurs que le thème promet (échelle d'espacement, rayons, tailles de texte, couleurs
sémantiques distinctes, contrastes minimaux de chaque paire texte / fond dans les deux thèmes) et qu'aucune nouvelle valeur
arbitraire (couleur codée en dur, rayon, taille ou graisse hors échelle) ne s'ajoute aux widgets sans que ce soit voulu.

Ce qu'ils **ne garantissent pas** : que l'interface est belle. Cela se vérifie à l'œil sur les captures de ``tools/capture_ui.py``
(voir ``docs/design-qa-final.md``). Ils ne testent pas non plus le détail du QSS : seulement que la feuille de style n'invente pas
de couleur hors palette.
"""

from __future__ import annotations

import re
from dataclasses import fields
from pathlib import Path

import pytest

from ui.design_system import (
    METRICS,
    ButtonVariant,
    Iconography,
    Radius,
    Spacing,
    StatusKind,
    TextRoles,
    Typography,
    Weights,
)
from ui.theme import THEMES, ThemePalette, global_stylesheet, mix_colors, with_alpha

ROOT = Path(__file__).resolve().parent.parent
THEME_NAMES = ("dark", "light")
HEX = re.compile(r"^#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")

REQUIRED_TOKENS = (
    # surfaces : fond → panneau → panneau relevé → champ → survol → actif
    "background", "panel", "panel_alt", "panel_elevated", "surface", "input_bg", "surface_hover", "surface_active",
    # texte et bordures
    "text", "text_strong", "muted", "muted_strong", "disabled_text", "border", "border_strong", "divider",
    # accent : sélection, action principale, focus
    "accent", "accent_hover", "accent_dark", "on_accent", "focus_ring", "selection",
    # couleurs sémantiques
    "success", "success_dark", "warning", "warning_dark", "danger", "danger_dark", "info", "info_dark",
    # timeline et clips
    "timeline_bg", "ruler_bg", "ruler_line", "playhead", "marker", "track_video", "track_audio", "track_subtitle",
    "clip_text", "clip_video_fill", "clip_audio_fill", "clip_title_fill", "clip_graphic_fill", "clip_nested_fill",
    "clip_broken_fill", "clip_audio_wave", "diamond_filled", "diamond_border", "tooltip_bg",
)


def _rgb(color: str) -> tuple[int, int, int]:
    text = color.lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def luminance(color: str) -> float:
    def channel(value: int) -> float:
        value /= 255
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(part) for part in _rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(foreground: str, background: str) -> float:
    """Rapport de contraste WCAG 2.x entre deux couleurs (1 à 21)."""
    light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


# ---------------------------------------------------------------------------
# Échelles
# ---------------------------------------------------------------------------


def test_the_spacing_scale_is_on_the_four_pixel_grid_and_strictly_increasing():
    scale = Spacing.scale()
    assert scale == (4, 8, 12, 16, 24, 32)
    assert all(value % 4 == 0 for value in scale) and list(scale) == sorted(set(scale))


def test_there_are_four_radii_and_the_old_names_fall_back_on_one_of_them():
    assert Radius.scale() == (4, 6, 8, 12)
    assert {Radius.xs, Radius.sm} == {4} and {Radius.xl, Radius.xxl} == {12}
    assert Radius.pill > 100


def test_the_text_sizes_and_weights_are_few_and_the_roles_use_only_them():
    sizes = {Typography.caption, Typography.micro, Typography.small, Typography.body, Typography.title, Typography.heading}
    assert sizes == {10, 11, 12, 13, 15, 17} and Typography.body_lg == Typography.title
    assert {Weights.regular, Weights.medium, Weights.semibold, Weights.bold} == {400, 500, 600, 700} and Weights.heavy == Weights.bold
    roles = TextRoles.all()
    assert len(roles) == 7 and len({role.name for role in roles}) == 7
    for role in roles:
        assert role.size in sizes and role.weight in (400, 500, 600, 700), role
        assert role.color in {field.name for field in fields(ThemePalette)}, f"le rôle {role.name} nomme un jeton absent : {role.color}"


def test_the_title_roles_stand_out_and_the_metadata_fade_away():
    assert TextRoles.app_title.size > TextRoles.panel_title.size >= TextRoles.label.size > TextRoles.label_secondary.size
    assert TextRoles.panel_title.weight > TextRoles.label.weight and TextRoles.app_title.weight > TextRoles.panel_title.weight
    assert TextRoles.helper.color == TextRoles.meta.color == TextRoles.label_secondary.color == "muted"
    assert TextRoles.meta.size < TextRoles.helper.size < TextRoles.label_secondary.size


def test_the_icon_sizes_the_variants_and_the_states_are_declared_once():
    assert list(Iconography.__dataclass_fields__) == ["xs", "sm", "md", "lg", "xl", "xxl"]
    assert [item.value for item in ButtonVariant] == ["primary", "secondary", "ghost", "destructive"]
    assert [item.value for item in StatusKind] == ["idle", "working", "success", "warning", "error"]
    assert METRICS.spacing is Spacing and METRICS.radius is Radius and METRICS.roles is TextRoles


# ---------------------------------------------------------------------------
# Thèmes : complets, valides, distincts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(THEMES))
def test_every_theme_defines_every_required_token_as_a_valid_colour(name):
    palette = THEMES[name]
    for token in REQUIRED_TOKENS:
        assert hasattr(palette, token), f"{name} : jeton absent {token}"
        assert HEX.match(getattr(palette, token)), f"{name}.{token} n'est pas une couleur : {getattr(palette, token)!r}"
    assert all(HEX.match(color) for color in palette.angle_colors)


def test_the_light_theme_is_not_a_simple_inversion_it_redefines_every_surface_and_text_token():
    dark, light = THEMES["dark"], THEMES["light"]
    must_differ = ("background", "panel", "panel_alt", "surface", "input_bg", "text", "muted", "muted_strong", "disabled_text",
                   "border", "border_strong", "accent", "on_accent", "success", "warning", "danger", "info",
                   "timeline_bg", "ruler_bg", "clip_video_fill", "clip_audio_fill", "diamond_filled")
    assert [token for token in must_differ if getattr(dark, token) == getattr(light, token)] == []
    assert luminance(dark.background) < 0.05 and luminance(light.background) > 0.8                   # un fond sombre, un fond clair
    assert dark.background != "#000000" and dark.panel != dark.background                              # pas de noir pur partout


@pytest.mark.parametrize("name", THEME_NAMES)
def test_the_dark_hierarchy_of_surfaces_is_ordered_and_distinct(name):
    palette = THEMES[name]
    chain = [palette.background, palette.panel, palette.panel_elevated, palette.surface_hover, palette.surface_active]
    assert len(set(chain)) == len(chain), f"{name} : deux niveaux de surface identiques"
    assert palette.input_bg != palette.panel, f"{name} : un champ doit se distinguer de son panneau"


@pytest.mark.parametrize("name", THEME_NAMES)
def test_semantic_colours_track_colours_and_clip_fills_are_distinct(name):
    palette = THEMES[name]
    groups = {
        "états": (palette.success, palette.warning, palette.danger, palette.info),
        "pistes": (palette.track_video, palette.track_audio, palette.track_subtitle),
        "fonds de clips": (palette.clip_video_fill, palette.clip_audio_fill, palette.clip_title_fill, palette.clip_graphic_fill,
                           palette.clip_nested_fill, palette.clip_broken_fill),
        "angles": tuple(palette.angle_colors),
    }
    for label, colors in groups.items():
        assert len({color.upper() for color in colors}) == len(colors), f"{name} : {label} en double"
    assert palette.accent.upper() not in {palette.track_audio.upper(), palette.track_video.upper(), palette.success.upper()}, (
        "l'accent est réservé à la sélection, à l'action principale et au focus : jamais la couleur d'une catégorie ni d'un état")


# ---------------------------------------------------------------------------
# Contrastes (WCAG) : texte ≥ 4,5, texte courant ≥ 7, éléments d'interface ≥ 3, séparateurs visibles
# ---------------------------------------------------------------------------

#: (premier plan, arrière-plan, minimum). Les textes de lecture courante visent 7:1 ; le secondaire 4,5:1 ; un désactivé 3:1.
CONTRASTS = (
    ("text", "background", 7.0), ("text", "panel", 7.0), ("text", "panel_alt", 7.0), ("text", "panel_elevated", 7.0),
    ("text", "surface", 7.0), ("text", "input_bg", 7.0), ("text", "surface_hover", 7.0), ("text", "accent_dark", 7.0),
    ("text", "selection", 7.0), ("text", "tooltip_bg", 7.0),
    ("muted", "background", 4.5), ("muted", "panel", 4.5), ("muted", "surface", 4.5), ("muted", "input_bg", 4.5),
    ("muted", "panel_alt", 4.5), ("muted_strong", "panel", 7.0), ("muted_strong", "background", 7.0),
    ("disabled_text", "panel", 3.0), ("disabled_text", "button_disabled_bg", 3.0), ("disabled_text", "background", 3.0),
    ("accent", "panel", 4.5), ("accent", "background", 4.5), ("accent", "input_bg", 4.5),
    ("on_accent", "accent", 4.5), ("on_accent", "accent_hover", 4.5),
    ("focus_ring", "panel", 3.0), ("focus_ring", "background", 3.0), ("focus_ring", "input_bg", 3.0),
    ("success", "panel", 4.5), ("warning", "panel", 4.5), ("danger", "panel", 4.5), ("info", "panel", 4.5),
    ("success", "success_dark", 4.5), ("warning", "warning_dark", 4.5), ("danger", "danger_dark", 4.5), ("info", "info_dark", 4.5),
    ("border_strong", "panel", 2.0), ("border_strong", "input_bg", 2.0), ("ruler_line", "ruler_bg", 2.0),
    ("playhead", "timeline_bg", 3.0), ("marker", "timeline_bg", 3.0),
    ("clip_text", "clip_video_fill", 4.5), ("clip_text", "clip_audio_fill", 4.5), ("clip_text", "clip_title_fill", 4.5),
    ("clip_text", "clip_graphic_fill", 4.5), ("clip_text", "clip_nested_fill", 4.5), ("clip_text", "clip_broken_fill", 4.5),
    ("diamond_filled", "clip_video_fill", 3.0), ("diamond_filled", "clip_audio_fill", 3.0), ("diamond_filled", "clip_graphic_fill", 3.0),
    ("diamond_border", "diamond_filled", 3.0),
)


@pytest.mark.parametrize("name", THEME_NAMES)
def test_every_important_pair_is_readable_in_both_themes(name):
    palette = THEMES[name]
    failures = []
    for foreground, background, minimum in CONTRASTS:
        ratio = contrast(getattr(palette, foreground), getattr(palette, background))
        if ratio < minimum:
            failures.append(f"{foreground} sur {background} : {ratio:.2f} < {minimum}")
    assert failures == [], f"{name} :\n  " + "\n  ".join(failures)


@pytest.mark.parametrize("name", THEME_NAMES)
def test_the_dimmed_clip_text_and_the_waveform_stay_readable_on_every_clip_fill(name):
    palette = THEMES[name]
    fills = (palette.clip_video_fill, palette.clip_audio_fill, palette.clip_title_fill, palette.clip_graphic_fill,
             palette.clip_nested_fill, palette.clip_broken_fill)
    alpha = int(palette.clip_text_dim[7:9], 16) / 255
    for fill in fills:
        dimmed = mix_colors(fill, palette.clip_text[:7], alpha)
        assert contrast(dimmed, fill) >= 3.5, (name, fill)                       # la durée d'un clip : une métadonnée, lisible
        assert contrast(palette.clip_audio_wave, palette.clip_audio_fill) >= 3.0  # la forme d'onde se voit sur son fond


# ---------------------------------------------------------------------------
# Aides de couleur et couleurs des clips
# ---------------------------------------------------------------------------


def test_mix_colors_and_with_alpha():
    assert mix_colors("#000000", "#FFFFFF", 0.0) == "#000000" and mix_colors("#000000", "#FFFFFF", 1.0) == "#FFFFFF"
    assert mix_colors("#000000", "#FFFFFF", 0.5) == "#808080" and mix_colors("#102030", "#102030", 0.7) == "#102030"
    assert mix_colors("#000000", "#FFFFFF", 5.0) == "#FFFFFF"                      # borné : jamais de canal hors de 0…255
    assert with_alpha("#36E6C3", 0.5) == "#36E6C380" and with_alpha("#36E6C3FF", 0.0) == "#36E6C300"


@pytest.mark.parametrize("name", THEME_NAMES)
def test_the_clip_colour_tells_the_category_never_the_identity(name):
    from types import SimpleNamespace

    from ui.timeline_widgets.clip_style import clip_edge, clip_fill, clip_surface

    palette = THEMES[name]

    def view(track_type="video", **extra):
        return SimpleNamespace(track_type=track_type, is_nested=False, sequence_id="", enabled=True, **extra)

    fills = {kind: clip_fill(view(kind), palette) for kind in ("video", "audio", "subtitle", "graphics")}
    assert len(set(fills.values())) == 4
    assert clip_fill(view(id="a"), palette) == clip_fill(view(id="zzz"), palette)           # deux clips vidéo : même couleur
    nested = SimpleNamespace(track_type="video", is_nested=True, sequence_id="s", enabled=True, nested_status="")
    broken = SimpleNamespace(track_type="video", is_nested=True, sequence_id="s", enabled=True, nested_status="missing")
    assert clip_fill(nested, palette) == palette.clip_nested_fill and clip_fill(broken, palette) == palette.clip_broken_fill
    base = view("video")
    assert luminance(clip_surface(base, palette, selected=True)) > luminance(clip_surface(base, palette)) > 0
    assert luminance(clip_surface(base, palette, hovered=True)) > luminance(clip_surface(base, palette))
    off = SimpleNamespace(track_type="video", is_nested=False, sequence_id="", enabled=False)
    assert abs(luminance(clip_surface(off, palette)) - luminance(palette.timeline_bg)) < abs(
        luminance(clip_surface(base, palette)) - luminance(palette.timeline_bg))             # un clip désactivé s'efface
    assert clip_edge(base, palette, selected=True) == palette.clip_border_selected
    assert clip_edge(base, palette, selected=False) != palette.clip_border_selected


# ---------------------------------------------------------------------------
# La feuille de style n'invente pas de couleur ; les widgets n'ajoutent pas de valeur arbitraire
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", THEME_NAMES)
def test_the_global_stylesheet_uses_only_palette_colours(name):
    palette = THEMES[name]
    known = {value.upper() for field in fields(palette) for value in ([getattr(palette, field.name)]
             if isinstance(getattr(palette, field.name), str) else list(getattr(palette, field.name)))}
    known |= {value[:7].upper() for value in known if len(value) == 9}
    used = {match.upper() for match in re.findall(r"#[0-9A-Fa-f]{6,8}\b", global_stylesheet(palette))}
    assert sorted(used - known) == [], "couleur en dur dans la feuille de style globale (utiliser un jeton de la palette)"


def _ui_sources():
    for path in sorted((ROOT / "ui").rglob("*.py")):
        key = path.relative_to(ROOT).as_posix()
        if key in {"ui/theme.py", "ui/design_system.py"} or key.startswith("ui/i18n"):
            continue
        code = "\n".join(line for line in path.read_text(encoding="utf-8").splitlines() if not line.strip().startswith("#"))
        yield key, code


def test_no_widget_uses_a_radius_a_text_size_or_a_weight_outside_the_scales():
    radius, size, weight = {0, 4, 6, 8, 12}, {10, 11, 12, 13, 15, 17, 22}, {400, 500, 600, 700}
    found = []
    for key, code in _ui_sources():
        found += [f"{key} : border-radius {v}px" for v in re.findall(r"border-radius:\s*(\d+)px", code) if int(v) not in radius]
        found += [f"{key} : font-size {v}px" for v in re.findall(r"font-size:\s*(\d+)px", code) if int(v) not in size]
        found += [f"{key} : font-weight {v}" for v in re.findall(r"font-weight:\s*(\d+)", code) if int(v) not in weight]
    assert found == [], "valeur hors échelle : utiliser Radius / Typography / Weights (ui/design_system.py)\n  " + "\n  ".join(found)


#: Couleurs ``#rrggbb`` en dur par fichier : un **cliquet**. Il ne peut que diminuer ; un nouveau fichier part de zéro.
#: Ce qui reste est de la **donnée**, jamais de l'habillage (l'habillage vient de ``ui/theme.py``) :
#:   compositing_editor : les teintes de clé chroma proposées (vert, bleu) ;
#:   graphics_editor    : les valeurs par défaut des champs de couleur d'un calque (remplissage, contour, ombre, fond) ;
#:   construction       : le fond du projet affiché dans l'inspecteur (une valeur du projet, pas une couleur d'interface) ;
#:   text_style_editor  : les couleurs que l'utilisateur choisit pour un style de texte et leurs valeurs par défaut ;
#:   debug_overlay      : la surcouche de débogage, toujours sombre quel que soit le thème (texte vert sur fond translucide).
HEX_BASELINE: dict[str, int] = {
    "ui/compositing_editor.py": 2,
    "ui/debug_overlay.py": 1,
    "ui/graphics_editor.py": 4,
    "ui/text_style_editor.py": 8,
}


def test_hard_coded_colours_in_widgets_can_only_decrease():
    counts = {key: len(re.findall(r"#[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?\b", code)) for key, code in _ui_sources()}
    over = {key: (count, HEX_BASELINE.get(key, 0)) for key, count in counts.items() if count > HEX_BASELINE.get(key, 0)}
    assert over == {}, f"couleur en dur de plus (fichier : actuel, plafond) : {over} — utiliser un jeton de ui/theme.py"
    stale = {key: (HEX_BASELINE[key], counts.get(key, 0)) for key in HEX_BASELINE if counts.get(key, 0) < HEX_BASELINE[key]}
    assert stale == {}, f"le plafond peut baisser (fichier : plafond, actuel) : {stale} — mettre à jour HEX_BASELINE"
