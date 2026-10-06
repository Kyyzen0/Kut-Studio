"""Templates de projet « Night » : un montage social complet à remplir — plans, titres, musique, SFX, look.

Un template construit un **projet ordinaire** sur la toile choisie (9:16, 4:5, 1:1…) : des emplacements de média
calés sur la grille rythmique (:mod:`core.template_slots`), des titres animés, un lit musical synthétisé et ses repères,
des SFX sur les cuts, le ducking, un calque d'effets « Night Look » et du grain. Tout se retouche ensuite comme
n'importe quel montage ; rien n'est verrouillé.

La mise en page est dessinée sur une maquette 1080×1920 (l'edit F1 de Singapour, dont les valeurs viennent) puis mise à
l'échelle du cadre réel comme un média ajusté. Les textes affichés viennent de l'interface (traduits) ; à défaut, les
textes anglais de :data:`TEXT_DEFAULTS`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from .audio_automation import duck_music_under_voice
from .beat_grid import BeatGrid
from .effects_library import builtin_presets
from .graphics import GraphicOverlay, GraphicType, ShapeKind, add_graphic_clip, graphic_defaults, update_graphic
from .impact_fx import add_flash, apply_impact_zoom
from .ken_burns import apply_ken_burns
from .leaderboard import Leaderboard, LeaderboardRow, build_leaderboard
from .project_model import Clip, Project
from .sfx_placement import place_sfx, place_sfx_on_cuts, sfx_asset
from .social_formats import create_social_project
from .text_animations import apply_text_animation
from .timeline_editing import add_marker
from .timeline_operations import add_clip_to_track, find_clip
from .visual_effects import ClipTransform

NIGHT_PALETTE: dict[str, str] = {
    "ink": "#05060A", "blue": "#22B8FF", "blue_deep": "#1673FF", "red": "#FF2433", "steel": "#9FB3C8",
    "white": "#FFFFFF",
}
"""Palette « Night » (données de rendu des templates) : noir, bleu électrique, rouge course, gris acier."""

DESIGN_W, DESIGN_H = 1080, 1920
MAX_TEXT_W = 960
"""Largeur maximale d'un titre (pixels de la maquette) : un texte plus long (une traduction…) est réduit pour tenir."""
FONT, FONT_ALT = "Anton", "Saira ExtraCondensed"
MUSIC_BED = "beat_bed_120"
ON_CUTS = ("whoosh", "whoosh_long", "whoosh_short")

TEXT_DEFAULTS: dict[str, str] = {
    "this_weekend": "This weekend,", "night_race": "night falls on the track", "gp_is": "The Grand Prix is",
    "this_weekend_caps": "THIS WEEKEND", "event_line": "DATE  ·  CIRCUIT  ·  NIGHT RACE",
    "first_time": "For the first time ever,", "hosts_a": "the city hosts a", "sprint": "SPRINT 🔥",
    "toughest": "The toughest street circuit", "of_the_year": "of the year:", "punch_1": "2 hours of racing,",
    "punch_2": "brutal heat,", "punch_3": "zero margin for error", "leader_1": "And the leader is",
    "leader_2": "out in front", "leader_3": "ahead of the whole grid 😱", "standings_note": "Standings after the last race",
    "race_on": "Race on", "sunday": "SUNDAY", "question": "live or highlights?", "live": "LIVE",
    "highlights": "HIGHLIGHTS", "comment": "Tell us in the comments", "city_lights": "CITY LIGHTS",
    "karaoke_line": "the city never sleeps tonight", "follow": "Follow for more nights",
    "standings_title": "STANDINGS", "row_name": "NAME", "row_unit": "PTS", "cue_hook": "Hook",
    "cue_drop": "Drop", "cue_outro": "Outro",
}
"""Textes par défaut (clés ``template.text.<clé>`` côté interface)."""


@dataclass(frozen=True)
class ProjectTemplate:
    """Un template : identifiant stable (clés ``template.<id>.*``), tempo, durée et construction."""

    id: str
    bpm: float
    duration: float
    build: Callable[[_Builder], None]
    poster: float = 0.0
    """Instant montré en vignette (titres et lignes déjà entrés)."""


class _Builder:
    """Outils de construction sur la toile cible (maquette 1080×1920 mise à l'échelle)."""

    def __init__(self, project: Project, texts: Mapping[str, str]) -> None:
        self.project = project
        self.texts = texts
        self.u = min(project.width / DESIGN_W, project.height / DESIGN_H)

    def text(self, key: str) -> str:
        return str(self.texts.get(key) or TEXT_DEFAULTS[key])

    def frac(self, x_px: float, y_px: float) -> tuple[float, float]:
        return x_px * self.u / self.project.width, y_px * self.u / self.project.height

    # -- plans -------------------------------------------------------------------------------------------------------

    def slots(self, cuts: list[tuple[float, float]], *, punch: Mapping[int, float] | None = None) -> list[Clip]:
        """Emplacements bord à bord sur V1 (``01``, ``02``…), cadrage « remplir », zoom d'impact à l'entrée."""
        track = next(track for track in self.project.tracks if track.id == "V1")
        clips = []
        for index, (start, end) in enumerate(cuts):
            clip = Clip(id=f"slot-{index + 1:02d}", asset_id="", track_id="V1", timeline_start=start, source_in=0.0,
                        source_out=end - start, label=f"{index + 1:02d}", template_slot=f"slot-{index + 1:02d}",
                        transform=ClipTransform(fill=True))
            if punch is not None:
                apply_impact_zoom(clip, strength=punch.get(index, 0.08))
            track.clips.append(clip)
            clips.append(clip)
        return clips

    # -- titres ------------------------------------------------------------------------------------------------------

    def graphic_text(self, text: str, size: int, color: str, *, font: str = FONT, stroke: int = 3,
                     background: str | None = None) -> GraphicOverlay:
        from .mograph_raster import measure_text

        u = self.u
        graphic = GraphicOverlay(
            type=GraphicType.TEXT, text=text, fill_color=color, stroke_color=NIGHT_PALETTE["ink"],
            stroke_width=round(stroke * u), stroke_position="outside", shadow_color="#000000E0", shadow_offset_x=0,
            shadow_offset_y=max(1, round(5 * u)), shadow_blur=14.0 * u, font_family=font,
            font_size=max(8, round(size * u)), autosize=True, align_h="center", align_v="center",
            background_enabled=background is not None, background_color=background or "#00000000",
            background_padding=round((26 if background else 16) * u), background_radius=22.0 * u if background else 0.0,
        )
        width, height = measure_text(graphic)
        limit = MAX_TEXT_W * u
        if width > limit:                                 # réduit pour tenir dans la largeur du cadre
            graphic = replace(graphic, font_size=max(8, int(graphic.font_size * limit / width)))
            width, height = measure_text(graphic)
        return replace(graphic, width=int(width + 2 * graphic.stroke_width), height=int(height + 2 * graphic.stroke_width))

    def title(self, key: str, start: float, end: float, y_px: float, size: int, color: str = "#FFFFFF", *,
              x_px: float = 0.0, delay: float = 0.0, animation: str = "pop_in", **style) -> Clip:
        graphic = self.graphic_text(self.text(key), size, color, **style)
        return self.place(graphic, start + delay, end, x_px, y_px, animation)

    def place(self, graphic: GraphicOverlay, start: float, end: float, x_px: float, y_px: float,
              animation: str | None = "pop_in") -> Clip:
        clip = add_graphic_clip(self.project, graphic.type.value, timeline_start=start, duration=end - start,
                                graphic=graphic)
        clip.label = graphic.text or clip.label
        x, y = self.frac(x_px, y_px)
        clip.transform = ClipTransform(position_x=x, position_y=y)
        if animation:
            apply_text_animation(clip, animation)
        return clip

    def dim(self, start: float, end: float, opacity: float) -> Clip:
        """Voile noir plein cadre : les titres restent lisibles sur n'importe quel plan."""
        graphic = GraphicOverlay(type=GraphicType.SHAPE, shape=ShapeKind.RECTANGLE, width=self.project.width,
                                 height=self.project.height, fill_color="#000000", stroke_width=0,
                                 shadow_color="#00000000")
        clip = add_graphic_clip(self.project, "shape", timeline_start=start, duration=end - start, graphic=graphic)
        clip.transform = ClipTransform(opacity=opacity)
        return clip

    # -- look, lumière, son ------------------------------------------------------------------------------------------

    def night_look(self, start: float, end: float) -> Clip:
        preset = next(preset for preset in builtin_presets() if preset.id == "night_look")
        clip = add_graphic_clip(self.project, "adjustment", timeline_start=start, duration=end - start)
        clip.effects = list(preset.effects)
        clip.label = preset.name
        return clip

    def light(self, kind: str, start: float, end: float, **fields) -> Clip:
        graphic = replace(graphic_defaults("light", project_width=self.project.width,
                                           project_height=self.project.height), light_kind=kind, **fields)
        return add_graphic_clip(self.project, "light", timeline_start=start, duration=end - start, graphic=graphic)

    def music(self, length: float) -> None:
        """Le lit rythmique synthétisé, bout à bout jusqu'à ``length`` (une boucle se remplace par sa musique)."""
        asset = sfx_asset(self.project, MUSIC_BED)
        at = 0.0
        while at < length - 1e-6:
            clip = find_clip(self.project, add_clip_to_track(self.project, asset.id, "A1", at).id)
            clip.source_out = min(asset.duration, length - at)
            at += clip.duration

    def cue(self, at: float, key: str) -> None:
        add_marker(self.project, at, self.text(key), category="music_cue")

    def finish(self, length: float) -> None:
        """Commun à tous les templates : SFX sur les cuts, ducking sous la voix, grain par-dessus tout."""
        if len([clip for track in self.project.tracks if track.id == "V1" for clip in track.clips]) > 1:
            place_sfx_on_cuts(self.project, "V1", list(ON_CUTS))
        duck_music_under_voice(self.project, "social_punchy")
        self.light("grain", 0.0, length)


# --- Sections partagées -----------------------------------------------------------------------------------------------


def _cta(b: _Builder, start: float, end: float) -> None:
    """Appel aux commentaires : question, 👇 qui rebondit, pastilles LIVE / HIGHLIGHTS."""
    b.title("race_on", start, end, -576, 80)
    b.title("sunday", start, end, -413, 160, NIGHT_PALETTE["red"], delay=0.5, stroke=4)
    b.title("question", start, end, -230, 92, delay=1.0)
    arrow = b.place(b.graphic_text("👇", 150, "#FFFFFF", stroke=0), start + 1.5, end, 0, -48)
    apply_text_animation(arrow, "bounce", start=0.25)
    live = b.graphic_text(b.text("live"), 64, "#FFFFFF", stroke=0, background=NIGHT_PALETTE["red"])
    highlights = b.graphic_text(b.text("highlights"), 64, "#FFFFFF", stroke=0, background=NIGHT_PALETTE["blue_deep"])
    gap = 40 * b.u
    total = live.width + gap + highlights.width
    left = -total / 2 / b.u                                        # en pixels de la maquette
    b.place(live, start + 2.0, end, left + live.width / 2 / b.u, 144)
    b.place(highlights, start + 2.25, end, left + (live.width + gap + highlights.width / 2) / b.u, 144)


def _rows(b: _Builder, values: tuple[int, ...]) -> tuple[LeaderboardRow, ...]:
    colors = (NIGHT_PALETTE["blue"], NIGHT_PALETTE["white"], NIGHT_PALETTE["red"], NIGHT_PALETTE["steel"])
    return tuple(LeaderboardRow(str(rank + 1), f"{b.text('row_name')} {rank + 1}",
                                f"{points} {b.text('row_unit')}", colors[min(rank, 3)])
                 for rank, points in enumerate(values))


# --- Templates ----------------------------------------------------------------------------------------------------


NIGHT_RACE_CUTS = (0.0, 2.0, 3.0, 5.0, 6.5, 9.0, 11.0, 13.0, 15.0, 16.0, 18.5, 20.0, 22.0, 23.0, 25.0, 27.0, 29.0,
                   30.0, 32.5, 35.0)
"""Les 19 plans de l'edit F1 : un plan toutes les 1 à 2,5 s, cuts sur la grille de 120 BPM."""
NIGHT_RACE_WARPS = (1, 8, 16)


def _night_race(b: _Builder) -> None:
    length = NIGHT_RACE_CUTS[-1]
    cuts = list(zip(NIGHT_RACE_CUTS, NIGHT_RACE_CUTS[1:]))
    b.slots(cuts, punch={index: 0.14 for index in NIGHT_RACE_WARPS})
    b.night_look(0.0, length)
    b.light("leak", 0.0, 2.0)
    for index in NIGHT_RACE_WARPS:
        add_flash(b.project, cuts[index][0])
    blue, red, steel = NIGHT_PALETTE["blue"], NIGHT_PALETTE["red"], NIGHT_PALETTE["steel"]
    b.title("this_weekend", 0.0, 2.0, -499, 74, delay=0.05)
    b.title("night_race", 0.0, 2.0, -355, 96, blue, delay=0.4)
    b.title("gp_is", 3.0, 8.0, -480, 82)
    b.title("this_weekend_caps", 3.0, 8.0, -336, 112, red, delay=0.5)
    b.title("event_line", 3.0, 8.0, -202, 40, blue, delay=1.0, font=FONT_ALT, stroke=2)
    b.title("first_time", 9.0, 15.0, -538, 66)
    b.title("hosts_a", 9.0, 15.0, -432, 66, delay=0.5)
    b.title("sprint", 9.0, 15.0, -259, 170, red, delay=1.0, stroke=4)
    b.title("toughest", 16.0, 22.0, -576, 62)
    b.title("of_the_year", 16.0, 22.0, -480, 62, delay=0.25)
    b.title("punch_1", 16.0, 22.0, -317, 92, blue, delay=1.5)
    b.title("punch_2", 16.0, 22.0, -182, 92, red, delay=2.5)
    b.title("punch_3", 16.0, 22.0, -48, 84, delay=3.5)
    b.dim(23.0, 29.0, 0.38)
    b.title("leader_1", 23.0, 29.0, -614, 80)
    b.title("leader_2", 23.0, 29.0, -509, 80, delay=0.4)
    b.title("leader_3", 23.0, 29.0, -394, 52, delay=0.9)
    build_leaderboard(b.project, Leaderboard(_rows(b, (302, 236, 199)), start=23.0, duration=6.0, delay=1.5))
    b.title("standings_note", 23.0, 29.0, 240, 36, steel, delay=3.0, font=FONT_ALT, stroke=2)
    _cta(b, 30.0, 35.0)
    b.music(length)
    for at, key in ((0.0, "cue_hook"), (14.0, "cue_drop"), (30.0, "cue_outro")):
        b.cue(at, key)
    for at in (10.0, 18.5, 31.0):
        place_sfx(b.project, "impact", at)
    b.finish(length)


def _city_lights(b: _Builder) -> None:
    length = 16.0
    cuts = [(2.0 * k, 2.0 * (k + 1)) for k in range(8)]
    apply_ken_burns(b.slots(cuts))
    b.night_look(0.0, length)
    for start, _end in cuts[1:]:
        b.light("leak", max(0.0, start - 0.4), start + 0.4, light_seed=int(start * 10))
    title = b.title("city_lights", 0.3, 4.0, -420, 140, NIGHT_PALETTE["blue"])
    for name, value in (("glow_color", NIGHT_PALETTE["blue"]), ("glow_radius", 26.0 * b.u), ("glow_strength", 1.2)):
        update_graphic(title, name, value)
    line = b.title("karaoke_line", 4.0, 14.0, 520, 64, animation="karaoke")
    update_graphic(line, "highlight_color", NIGHT_PALETTE["blue"])
    b.title("follow", 14.0, 16.0, 0, 72, NIGHT_PALETTE["white"])
    b.music(length)
    b.cue(0.0, "cue_hook")
    b.cue(14.0, "cue_outro")
    b.finish(length)


def _leaderboard(b: _Builder) -> None:
    length = 8.0
    b.slots([(0.0, length)])
    b.night_look(0.0, length)
    b.dim(0.0, length, 0.45)
    b.title("standings_title", 0.0, length, -560, 110, NIGHT_PALETTE["blue"])
    build_leaderboard(b.project, Leaderboard(_rows(b, (302, 236, 199, 178, 150)), start=0.0, duration=length,
                                             top=-0.17, delay=0.6))
    b.title("standings_note", 0.0, length, 520, 36, NIGHT_PALETTE["steel"], delay=2.0, font=FONT_ALT, stroke=2)
    b.music(length)
    b.finish(length)


def _cta_comments(b: _Builder) -> None:
    length = 6.0
    b.slots([(0.0, length)])
    b.night_look(0.0, length)
    b.dim(0.0, length, 0.3)
    _cta(b, 0.0, length)
    b.title("comment", 0.0, length, 330, 48, NIGHT_PALETTE["steel"], delay=2.6, font=FONT_ALT, stroke=2)
    b.music(length)
    b.finish(length)


TEMPLATES: tuple[ProjectTemplate, ...] = (
    ProjectTemplate("night_race", 120.0, 35.0, _night_race, poster=13.0),
    ProjectTemplate("city_lights", 120.0, 16.0, _city_lights, poster=6.0),
    ProjectTemplate("leaderboard", 120.0, 8.0, _leaderboard, poster=4.0),
    ProjectTemplate("cta_comments", 120.0, 6.0, _cta_comments, poster=3.0),
)


def get_template(template_id: str) -> ProjectTemplate:
    for template in TEMPLATES:
        if template.id == template_id:
            return template
    raise KeyError(f"Template inconnu : {template_id!r}.")


def create_from_template(template_id: str, format_id: str = "vertical", fps: float = 30, *, name: str = "",
                         track_names: Mapping[str, str] | None = None, texts: Mapping[str, str] | None = None) -> Project:
    """Projet social construit par le template sur la toile du format (emplacements vides, prêts à remplir)."""
    template = get_template(template_id)
    project = create_social_project(format_id, fps, name=name, track_names=track_names)
    project.active_sequence.beat_grid = BeatGrid(template.bpm)
    template.build(_Builder(project, texts or {}))
    for track in project.tracks:
        track.clips.sort(key=lambda clip: (clip.timeline_start, clip.id))
    return project


__all__ = [
    "NIGHT_PALETTE", "ProjectTemplate", "TEMPLATES", "TEXT_DEFAULTS", "create_from_template", "get_template",
]
