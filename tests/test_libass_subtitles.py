"""Les sous-titres sont réellement incrustés par libass, avec le constructeur de filtre de l'application.

Ces tests ne lisent pas la commande FFmpeg : ils l'exécutent. Le graphe vient de
:meth:`core.export_engine.ExportEngine.build_frame_command` (celui de l'export et de l'aperçu,
filtre ``subtitles=`` compris), appliqué à une vidéo ``lavfi`` unie ; l'image rendue est relue
pixel par pixel. Sans sous-titre elle reste uniforme ; avec, le texte clair change les pixels de
la zone que le style par défaut lui réserve (bas, centré).

Ils sont marqués ``libass`` : sautés avec la raison sur un FFmpeg sans libass (celui de Homebrew,
du runner macOS de la CI principale), **échoués** avec ``KUT_STUDIO_REQUIRE_LIBASS=1`` (job
``macos-libass`` de la CI : un job vert y veut dire « libass testé »). Voir ``docs/ci-libass.md``.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import replace
from pathlib import Path

import ffmpeg_caps
import pytest
from PySide6.QtGui import QImage

from core.export_engine import (
    ExportEngine,
    ExportFormat,
    ExportPreset,
    ExportRequest,
    _ffmpeg_command_prefix,
)
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import RenderPlan, build_render_plan
from core.text_style import TextAlignment, TextStyle, default_text_style

pytestmark = pytest.mark.libass

W, H, FPS = 640, 360, 25
DURATION = 3.0
BACKGROUND = "0x203040"
"""Fond uni et sombre : le texte (blanc, contour noir) en diffère partout."""
CUE_START, CUE_DURATION = 0.5, 2.0
TEXT = "Bonjour le monde"

UNIFORM_TOLERANCE = 3
"""Écart de gris toléré dans une image sans sous-titre (arrondis du décodage H.264 / RVB)."""
CHANGED_THRESHOLD = 30
"""Un pixel est « touché par le texte » quand son gris s'écarte du fond d'au moins autant."""
MINIMUM_TEXT_PIXELS = 150
"""Seize caractères à la taille du style par défaut touchent des milliers de pixels (≈ 2 900 mesurés avec
ffmpeg-full 9.0.2 sur un cadre 640×360) : le seuil écarte le bruit sans dépendre d'une police précise
(libass retombe sur la police du système quand « DejaVu Sans » manque)."""
MINIMUM_ANY_TEXT_PIXELS = 20
"""Seuil du test ASS : il prouve que libass a lu le fichier et dessiné quelque chose, pas où ni comment."""


def _generate_source(path: Path) -> Path:
    """Vidéo ``lavfi`` unie, générée par le FFmpeg de l'application."""
    subprocess.run(
        [*_ffmpeg_command_prefix(), "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=c={BACKGROUND}:s={W}x{H}:r={FPS}:d={DURATION}", "-c:v", "libx264", "-crf", "10",
         "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True, timeout=120,
    )
    return path


def _plan(source: Path, *, style: TextStyle | None = None) -> RenderPlan:
    """Plan de rendu réel : la vidéo unie et un sous-titre construit comme celui d'un projet."""
    video = MediaAsset(id="v", path=str(source), name="fond", duration=DURATION, width=W, height=H,
                       fps=float(FPS), media_type="video", has_audio=False)
    subtitles = MediaAsset(id="s", path="", name="Sous-titres", duration=DURATION, width=0, height=0, fps=0.0,
                           media_type="subtitle", has_audio=False)
    clip = Clip(id="cue", asset_id="s", track_id="S1", timeline_start=CUE_START, source_in=0.0,
                source_out=CUE_DURATION, text=TEXT, text_style=style or default_text_style())
    project = Project(
        name="libass", width=W, height=H, fps=float(FPS), media_assets=[video, subtitles],
        tracks=[
            Track(id="V1", name="V1", type="video",
                  clips=[Clip(id="bg", asset_id="v", track_id="V1", timeline_start=0.0, source_in=0.0,
                              source_out=DURATION)]),
            Track(id="S1", name="S1", type="subtitle", clips=[clip]),
        ],
    )
    plan = build_render_plan(project)
    assert [cue.text for cue in plan.subtitle_cues] == [TEXT]
    assert plan.subtitle_cues[0].start == pytest.approx(CUE_START)
    assert plan.subtitle_cues[0].end == pytest.approx(CUE_START + CUE_DURATION)
    return plan


def _render_image(plan: RenderPlan, tmp_path: Path, playhead: float) -> tuple[QImage, list[str], str]:
    """Rend l'image à ``playhead`` avec le graphe de l'application ; renvoie (image, commande, fichier SRT/ASS)."""
    engine = ExportEngine()
    request = ExportRequest(render_plan=plan, output_path=str(tmp_path / "inutilise.mp4"),
                            format=ExportFormat.MP4_H264, preset=ExportPreset("T", (W, H), 28, "64k"), fps=FPS)
    command = engine.build_frame_command(request, playhead)
    temporary = engine.take_temporary_files()
    try:
        # Marge large : le premier rendu de libass construit le cache de polices (fontconfig), lent sur un
        # runner neuf.
        completed = subprocess.run(command, capture_output=True, timeout=240, check=False)
    finally:
        for path in temporary:
            try:
                os.remove(path)
            except OSError:
                pass
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")[-800:]
    image = QImage()
    assert image.loadFromData(completed.stdout), "PNG illisible : FFmpeg n'a rien rendu"
    assert (image.width(), image.height()) == (W, H)
    return image, command, next((path for path in temporary), "")


def _render(plan: RenderPlan, tmp_path: Path, playhead: float) -> tuple[list[bytes], list[str], str]:
    """Comme :func:`_render_image`, avec l'image en lignes de gris."""
    image, command, subtitle_file = _render_image(plan, tmp_path, playhead)
    gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
    stride, data = gray.bytesPerLine(), bytes(gray.constBits())
    return [data[y * stride:y * stride + W] for y in range(H)], command, subtitle_file


def _background(rows: list[bytes]) -> int:
    return rows[0][0]    # le coin haut gauche n'est jamais touché par un sous-titre


def _changed(rows: list[bytes]) -> list[tuple[int, int]]:
    """Pixels (x, y) dont le gris s'écarte du fond de plus de ``CHANGED_THRESHOLD``."""
    background = _background(rows)
    return [(x, y) for y, row in enumerate(rows) for x, value in enumerate(row)
            if abs(value - background) >= CHANGED_THRESHOLD]


def _assert_uniform(rows: list[bytes]) -> None:
    background = _background(rows)
    worst = max(abs(value - background) for row in rows for value in row)
    assert worst <= UNIFORM_TOLERANCE, f"image censée être unie, écart maximal {worst} niveaux"


def test_the_ffmpeg_of_the_application_is_built_with_libass() -> None:
    """Preuve que le FFmpeg de l'application utilise libass : configuration, filtre, détection de l'application."""
    status = ffmpeg_caps.libass_status()
    if ffmpeg_caps.require_libass_requested():
        # Preuve exigée là où la couverture libass doit être acquise (job dédié). Ailleurs, le filtre suffit : une
        # build exotique dont ``-buildconf`` est tronqué ne doit pas faire échouer la matrice principale.
        assert status.configured, f"{status.command} : ffmpeg -buildconf ne contient pas --enable-libass"
    assert status.has_filter, f"{status.command} : le filtre « subtitles » est absent de ffmpeg -filters"

    from core.filter_graph import ffmpeg_supports_subtitles

    # ``ensure_libass`` a oublié le cache de détection : l'application relit ``ffmpeg -filters`` ici.
    assert ffmpeg_supports_subtitles() is True
    assert status.command == tuple(_ffmpeg_command_prefix())


def test_a_flat_frame_stays_flat_and_the_subtitle_burns_light_text_at_the_bottom_centre(tmp_path) -> None:
    plan = _plan(_generate_source(tmp_path / "fond.mp4"))
    playhead = CUE_START + CUE_DURATION / 2

    control_rows, _, _ = _render(replace(plan, subtitle_cues=(), subtitle_styles=()), tmp_path, playhead)
    _assert_uniform(control_rows)

    rows, command, subtitle_file = _render(plan, tmp_path, playhead)
    assert "subtitles=filename=" in command[command.index("-filter_complex") + 1]
    assert subtitle_file.endswith(".srt"), f"style par défaut : un SRT était attendu, pas {subtitle_file!r}"
    assert _background(rows) == pytest.approx(_background(control_rows), abs=UNIFORM_TOLERANCE)

    changed = _changed(rows)
    assert len(changed) >= MINIMUM_TEXT_PIXELS, f"seulement {len(changed)} pixels touchés : aucun texte rendu ?"
    assert sum(1 for row in rows for value in row if value >= 200) >= MINIMUM_TEXT_PIXELS // 2, "texte clair attendu"
    xs, ys = [x for x, _ in changed], [y for _, y in changed]
    # Style par défaut : alignement bas centre. Le texte tient dans le tiers bas et reste centré.
    assert min(ys) >= H * 0.55, f"texte trop haut (y min {min(ys)} sur {H})"
    assert max(ys) < H
    assert W * 0.35 <= (min(xs) + max(xs)) / 2 <= W * 0.65, f"texte décentré (x {min(xs)}..{max(xs)} sur {W})"


@pytest.mark.parametrize("playhead", [0.1, 2.9], ids=["avant", "après"])
def test_no_text_is_burned_outside_the_cue(tmp_path, playhead) -> None:
    rows, command, _ = _render(_plan(_generate_source(tmp_path / "fond.mp4")), tmp_path, playhead)
    # Le filtre est bien dans le graphe (sinon l'image unie ne prouverait rien) : c'est la durée du cue qui masque le texte.
    assert "subtitles=filename=" in command[command.index("-filter_complex") + 1]
    _assert_uniform(rows)


def test_an_ass_file_with_a_custom_style_is_rendered_by_libass(tmp_path) -> None:
    """Un style non standard passe par un fichier ASS : libass le lit, et ``force_style`` ne l'écrase plus.

    Avant le correctif, ``force_style`` (22 pt, blanc, bas centré) était ajouté aussi aux fichiers ASS : un clip
    en 48 pt jaune « haut centré » ressortait en minuscule, en bas (145 pixels touchés, mesurés avec
    ffmpeg-full 9.0.2, au lieu de plusieurs milliers). Le style du clip doit se retrouver à l'image : le texte
    en **haut** du cadre, **jaune**, et nettement plus gros que le style par défaut.
    """
    style = TextStyle(font_size=48.0, color="#ffcc00", alignment=TextAlignment.TOP_CENTER)
    plan = _plan(_generate_source(tmp_path / "fond.mp4"), style=style)
    image, command, subtitle_file = _render_image(plan, tmp_path, CUE_START + CUE_DURATION / 2)
    graph = command[command.index("-filter_complex") + 1]
    assert "subtitles=filename=" in graph
    assert "force_style" not in graph, "un ASS porte son style : force_style l'écraserait"
    assert subtitle_file.endswith(".ass"), f"style personnalisé : un ASS était attendu, pas {subtitle_file!r}"

    gray = image.convertToFormat(QImage.Format.Format_Grayscale8)
    stride, data = gray.bytesPerLine(), bytes(gray.constBits())
    rows = [data[y * stride:y * stride + W] for y in range(H)]
    touched = _changed(rows)
    assert len(touched) >= MINIMUM_TEXT_PIXELS, f"texte trop petit ou absent : {len(touched)} pixels touchés"
    assert max(y for _x, y in touched) < H // 2, "style « haut centré » : le texte doit rester dans la moitié haute"
    assert not [y for _x, y in touched if y >= H // 2]
    # Couleur : au cœur des lettres le jaune (#ffcc00) domine, le bleu en est très en retrait (le blanc par
    # défaut aurait les trois composantes proches).
    yellow = [image.pixelColor(x, y) for x, y in touched if image.pixelColor(x, y).red() > 200]
    assert yellow, "aucun pixel clair : le texte n'a pas la couleur du style"
    core = [c for c in yellow if c.green() > 150]
    assert core and sum(1 for c in core if c.blue() < 90) / len(core) > 0.5, "le texte n'est pas jaune"
