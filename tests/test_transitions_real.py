"""Chaque transition rendue par le vrai FFmpeg : le filtre ``xfade`` demandé existe, s'exécute, et se voit.

Avant, six types (pixelize, radial, cercles, lissés) étaient repliés en silence sur ``fade`` : le nom du filtre était
« correct » pour les tests de forme, mais l'image au milieu de la transition était celle d'un simple fondu.
"""

from __future__ import annotations

import subprocess

import numpy as np
import pytest
from render_probe import lavfi_video, needs_ffmpeg, render_frame

from core.export_engine import ExportEngine, _ffmpeg_transition_name, input_arguments
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import RenderTransition, build_render_plan
from core.transitions import TransitionType, add_transition

W, H = 96, 54
REPAIRED = [
    TransitionType.PIXELIZE,
    TransitionType.RADIAL,
    TransitionType.CIRCLE_OPEN,
    TransitionType.CIRCLE_CLOSE,
    TransitionType.SMOOTH_LEFT,
    TransitionType.SMOOTH_RIGHT,
]
DURATION = 1.0
# Les clips se touchent à 2 s ; la transition dure 1 s : son milieu est à ~1,5 s de la sortie (fondu centré sur la jonction).


def _project(tmp_path, transition_type: TransitionType) -> Project:
    """Deux clips de 2 s aux motifs différents (une transition se voit dans l'espace, pas sur deux aplats)."""
    first = lavfi_video(
        tmp_path / "a.mp4", "testsrc2=alpha=0", size=(W, H), seconds=2.0
    )
    second = lavfi_video(
        tmp_path / "b.mp4", "rgbtestsrc=complement=0", size=(W, H), seconds=2.0
    )
    assets = [
        MediaAsset("ma", str(first), "a", 2.0, W, H, 25.0, "video"),
        MediaAsset("mb", str(second), "b", 2.0, W, H, 25.0, "video"),
    ]
    clips = [Clip("a", "ma", "V1", 0.0, 0.0, 2.0), Clip("b", "mb", "V1", 2.0, 0.0, 2.0)]
    project = Project(
        "p",
        width=W,
        height=H,
        fps=25.0,
        media_assets=assets,
        tracks=[Track("V1", "V1", "video", clips=clips)],
    )
    add_transition(project, "a", "b", transition_type, DURATION)
    return project


def _middle(tmp_path, transition_type: TransitionType) -> np.ndarray:
    plan = build_render_plan(_project(tmp_path, transition_type))
    # Le milieu de la transition, d'après le plan lui-même (et non une durée supposée).
    return render_frame(plan, W, H, 1.5).astype(int)


@needs_ffmpeg
@pytest.mark.parametrize("transition_type", list(TransitionType), ids=lambda t: t.value)
def test_every_transition_exports_with_its_own_xfade(tmp_path, transition_type):
    project = _project(tmp_path, transition_type)
    plan = build_render_plan(project)
    graph, video, audio, inputs = ExportEngine._build_filter_complex(
        plan, W, H, 25, None
    )
    name = _ffmpeg_transition_name(
        RenderTransition("t", "a", "b", transition_type, DURATION)
    )
    assert f"xfade=transition={name}:" in graph
    command = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for path in inputs:
        command += input_arguments(path)
    out = tmp_path / "out.mp4"
    command += [
        "-filter_complex",
        graph,
        "-map",
        f"[{video}]",
        "-map",
        f"[{audio}]",
        "-pix_fmt",
        "yuv420p",
        str(out),
    ]
    completed = subprocess.run(command, capture_output=True, timeout=180)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    assert out.stat().st_size > 0


@needs_ffmpeg
@pytest.mark.parametrize("transition_type", REPAIRED, ids=lambda t: t.value)
def test_a_repaired_transition_differs_from_a_plain_crossfade_at_its_middle(
    tmp_path, transition_type
):
    """Avant la réparation, ces six types rendaient exactement l'image d'un fondu enchaîné."""
    (tmp_path / "fade").mkdir()
    (tmp_path / "own").mkdir()
    fade = _middle(tmp_path / "fade", TransitionType.CROSSFADE)
    own = _middle(tmp_path / "own", transition_type)
    assert float(np.abs(own - fade).mean()) > 4.0, (
        f"{transition_type.value} ressemble à un fondu"
    )


def _red_to_blue(tmp_path, transition_type: TransitionType) -> np.ndarray:
    """Image à 40 % d'une transition du rouge (ancien) au bleu (nouveau) : le bleu montre où le nouveau clip apparaît."""
    first = lavfi_video(tmp_path / "a.mp4", "color=c=red", size=(W, H), seconds=2.0)
    second = lavfi_video(tmp_path / "b.mp4", "color=c=blue", size=(W, H), seconds=2.0)
    assets = [
        MediaAsset("ma", str(first), "a", 2.0, W, H, 25.0, "video"),
        MediaAsset("mb", str(second), "b", 2.0, W, H, 25.0, "video"),
    ]
    clips = [Clip("a", "ma", "V1", 0.0, 0.0, 2.0), Clip("b", "mb", "V1", 2.0, 0.0, 2.0)]
    project = Project(
        "p",
        width=W,
        height=H,
        fps=25.0,
        media_assets=assets,
        tracks=[Track("V1", "V1", "video", clips=clips)],
    )
    add_transition(project, "a", "b", transition_type, DURATION)
    frame = render_frame(build_render_plan(project), W, H, 1.4)
    return frame[..., 2].astype(int) > frame[..., 0].astype(
        int
    )  # True : pixel plutôt bleu (nouveau clip)


@needs_ffmpeg
@pytest.mark.parametrize(
    ("transition_type", "region_new", "region_old"),
    [
        # wipe_tl / diag_tl : le nouveau clip vient du coin opposé (bas droit) ; le coin nommé est le dernier à changer.
        (TransitionType.WIPE_TL, "br", "tl"),
        (TransitionType.DIAG_TL, "br", "tl"),
        # horz_open : bande horizontale au centre ; horz_close : les bords haut et bas d'abord.
        (TransitionType.HORZ_OPEN, "center_row", "top"),
        (TransitionType.HORZ_CLOSE, "top", "center_row"),
        # vert_open : bande verticale au centre ; vert_close : les bords gauche et droit d'abord.
        (TransitionType.VERT_OPEN, "center_col", "left"),
        (TransitionType.VERT_CLOSE, "left", "center_col"),
    ],
    ids=lambda v: v.value if isinstance(v, TransitionType) else str(v),
)
def test_the_new_clip_appears_where_the_description_says(
    tmp_path, transition_type, region_new, region_old
):
    blue = _red_to_blue(tmp_path, transition_type)
    h, w = blue.shape
    regions = {
        "tl": blue[: h // 6, : w // 6],
        "br": blue[-(h // 6) :, -(w // 6) :],
        "top": blue[: h // 8, :],
        "left": blue[:, : w // 8],
        "center_row": blue[h // 2 - h // 16 : h // 2 + h // 16, :],
        "center_col": blue[:, w // 2 - w // 16 : w // 2 + w // 16],
    }
    assert regions[region_new].mean() > 0.9, (
        f"{transition_type.value}: {region_new} devrait déjà être bleu"
    )
    assert regions[region_old].mean() < 0.1, (
        f"{transition_type.value}: {region_old} devrait encore être rouge"
    )
