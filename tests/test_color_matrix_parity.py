"""Parité réelle de la matrice couleur d'un média : moniteur GPU (référence numpy) = export FFmpeg = miniature.

Une barre de couleurs saturées en 4:4:4 sans perte (aucune chroma à rééchantillonner) est encodée non étiquetée, étiquetée
BT.601 ou BT.709, en SD et en HD. Pour chaque cas, la matrice attendue vient de la règle (:mod:`core.color_matrix`) ; l'image du
moniteur (``reference_frame`` sur une source dont la matrice est celle que l'interface donne), celle du graphe de l'export
(``render_frame``) et la miniature (``extract_thumbnail``) doivent être la même image, à 1 niveau près, et **différer** de
l'image de l'autre matrice (sinon le test ne prouve rien).
"""

from __future__ import annotations

import io
import shutil
import subprocess

import numpy as np
import pytest

from core.color_matrix import asset_effective_matrix, asset_ffmpeg_override
from core.gpu_composite import CompositeFrame, CompositeLayer, VideoSource, reference_frame
from core.media_previews import extract_thumbnail
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from render_probe import render_frame

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="FFmpeg absent")

FPS = 25
TOLERANCE = 1                                    # niveaux 8 bits, par canal, entre deux chemins de rendu
SAMPLE_ROW = 0.3                                 # hauteur (part) où l'on lit les sept barres de couleur


def _encode(path, size, *, label: str = ""):
    """Barres SMPTE 4:4:4 sans perte ; ``label`` : ``""`` (aucune étiquette), ``bt709`` ou ``smpte170m``."""
    # ``smptebars`` pose lui-même ``bt470bg`` : l'étiquette est écrasée explicitement dans les deux cas.
    tags = f"colorspace={label}:range=tv" if label else "colorspace=unknown:range=unknown:color_primaries=unknown:color_trc=unknown"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"smptebars=s={size[0]}x{size[1]}:r={FPS}:d=1",
         "-vf", f"format=yuv444p,setparams={tags}", "-c:v", "ffv1", str(path)], check=True, timeout=60)
    return str(path)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    folder = tmp_path_factory.mktemp("matrix")
    return {
        "sd_untagged": (_encode(folder / "sd.mkv", (640, 360)), (640, 360)),
        "hd_untagged": (_encode(folder / "hd.mkv", (1280, 720)), (1280, 720)),
        "fhd_untagged": (_encode(folder / "fhd.mkv", (1920, 1080)), (1920, 1080)),
        "hd_bt601": (_encode(folder / "hd601.mkv", (1280, 720), label="smpte170m"), (1280, 720)),
        "sd_bt709": (_encode(folder / "sd709.mkv", (640, 360), label="bt709"), (640, 360)),
    }


def _asset(entry, choice: str = "auto", probed: bool = True) -> MediaAsset:
    path, (width, height) = entry
    asset = MediaAsset(id="a", path=path, name="m", duration=1.0, width=width, height=height, fps=float(FPS),
                       media_type="video", color_matrix=choice)
    if probed:
        from core.media_probe import probe_media

        asset.color_space = probe_media(path).color_space        # ce que l'import lit
    return asset


def _codes(asset: MediaAsset) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", asset.path, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt",
                          "yuv444p", "-"], capture_output=True, check=True, timeout=60).stdout
    planes = np.frombuffer(raw, np.uint8).reshape(3, asset.height, asset.width)
    return np.stack(planes, axis=-1) / 255.0


def _monitor(asset: MediaAsset, matrix: str | None = None) -> np.ndarray:
    """L'image du moniteur : la source porte la matrice que l'interface lui donne (:meth:`_monitor_color_matrix`) ; avec
    ``matrix``, celle-là impose (le contre-exemple), sans choix."""
    choice = asset.color_matrix if matrix is None else "auto"
    matrix = asset_effective_matrix(asset) if matrix is None else matrix
    source = VideoSource("main", "yuv444p", asset.width, asset.height, colorspace=matrix, color_choice=choice)
    layer = CompositeLayer(source="main", matrix=(1.0, 0.0, 0.0, 1.0, 0.0, 0.0), fit=(0.0, 0.0, float(asset.width), float(asset.height)))
    frame = CompositeFrame(asset.width, asset.height, 1.0, (layer,), (source,))
    return reference_frame(frame, {"main": _codes(asset)}) * 255.0


def _project(asset: MediaAsset) -> Project:
    clip = Clip(id="c", asset_id="a", track_id="V1", timeline_start=0.0, source_in=0.0, source_out=1.0)
    return Project(name="p", width=asset.width, height=asset.height, fps=float(FPS), media_assets=[asset],
                   tracks=[Track(id="V1", name="V1", type="video", clips=[clip])])


def _export(asset: MediaAsset) -> np.ndarray:
    return render_frame(build_render_plan(_project(asset)), asset.width, asset.height, 0.0).astype(np.float64)


def _thumbnail(asset: MediaAsset) -> np.ndarray:
    from PySide6.QtGui import QImage

    data = extract_thumbnail(asset.path, 0.0, 320, asset)
    assert data
    image = QImage.fromData(data).convertToFormat(QImage.Format.Format_RGB888)
    rows = np.frombuffer(image.constBits(), np.uint8).reshape(image.height(), image.bytesPerLine())
    return rows[:, : image.width() * 3].reshape(image.height(), image.width(), 3).astype(np.float64)


def _bars(image: np.ndarray) -> np.ndarray:
    """La couleur au centre des sept barres de la rangée du haut, quelle que soit la taille de l'image."""
    height, width = image.shape[:2]
    row = int(height * SAMPLE_ROW)
    return np.array([image[row, int((i + 0.5) * width / 7)] for i in range(7)])


def _gap(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(_bars(a) - _bars(b)).max())


# (nom du média, choix) → matrice effective attendue
CASES = [
    ("sd_untagged", "auto", "bt601"),                 # SD non étiqueté : comme avant
    ("hd_untagged", "auto", "bt709"),                 # 1280×720 : la limite basse de la HD
    ("fhd_untagged", "auto", "bt709"),
    ("hd_bt601", "auto", "bt601"),                    # l'étiquette est respectée, même en HD
    ("sd_bt709", "auto", "bt709"),                    # … et en SD
    ("hd_untagged", "bt601", "bt601"),                # le choix de l'utilisateur gagne sur la règle
    ("sd_untagged", "bt709", "bt709"),
    ("hd_bt601", "bt709", "bt709"),                   # … et sur l'étiquette
]


@pytest.mark.parametrize("name, choice, expected", CASES)
def test_monitor_export_and_thumbnail_show_the_same_image(media, name, choice, expected):
    asset = _asset(media[name], choice)
    assert asset_effective_matrix(asset) == expected
    other = "bt709" if expected == "bt601" else "bt601"
    monitor, exported, thumb = _monitor(asset), _export(asset), _thumbnail(asset)
    wrong = _monitor(asset, other)
    # La mire discrimine les matrices : sinon l'accord ne prouverait rien.
    assert _gap(monitor, wrong) >= 8, "les deux matrices donnent la même image : test sans valeur"
    assert _gap(monitor, exported) <= TOLERANCE, (name, choice, _gap(monitor, exported))
    assert float(np.abs(monitor - exported).max()) <= TOLERANCE                          # et l'image entière, pas seulement les barres
    assert _gap(monitor, thumb) <= TOLERANCE, (name, choice, _gap(monitor, thumb))
    assert _gap(exported, wrong) >= 8                                  # l'export n'a pas pris l'autre matrice


def test_ffmpeg_is_told_only_what_it_would_not_do_alone(media):
    assert asset_ffmpeg_override(_asset(media["sd_untagged"])) == ""            # FFmpeg lit déjà le BT.601
    assert asset_ffmpeg_override(_asset(media["hd_untagged"])) == "bt709"       # le seul cas qui change pour l'export
    assert asset_ffmpeg_override(_asset(media["hd_bt601"])) == ""               # étiqueté : l'étiquette suffit
    assert asset_ffmpeg_override(_asset(media["sd_bt709"])) == ""
    assert asset_ffmpeg_override(_asset(media["hd_untagged"], "bt601")) == "bt470bg"
    assert asset_ffmpeg_override(_asset(media["hd_bt601"], "bt709")) == "bt709"


def test_a_project_opened_from_a_file_probes_the_label_when_it_is_needed(media):
    """Un projet rechargé n'a pas l'étiquette (valeur d'exécution) : l'HD la sonde à la demande, la SD n'a pas besoin de la sonde."""
    fresh = _asset(media["hd_bt601"], probed=False)
    assert fresh.color_space == ""
    assert asset_effective_matrix(fresh) == "bt601" and fresh.color_space == "bt601"     # sondée, mémorisée
    assert asset_effective_matrix(_asset(media["hd_untagged"], probed=False)) == "bt709"
    assert asset_effective_matrix(_asset(media["sd_untagged"], probed=False)) == "bt601"


def test_an_old_thumbnail_is_not_served_again_after_the_rule_changed(media):
    """Même fichier, même instant : la clé de la miniature d'un HD non étiqueté change (règle 2), celle d'un SD ne change pas."""
    from core.media_previews import thumbnail_cache_key

    hd, sd = _asset(media["hd_untagged"]), _asset(media["sd_untagged"])
    path_hd, path_sd = media["hd_untagged"][0], media["sd_untagged"][0]
    assert thumbnail_cache_key(path_hd, 0.0, 160) != thumbnail_cache_key(path_hd, 0.0, 160, hd)
    assert thumbnail_cache_key(path_sd, 0.0, 160) == thumbnail_cache_key(path_sd, 0.0, 160, sd)
    assert thumbnail_cache_key(path_hd, 0.0, 160, hd) != thumbnail_cache_key(path_hd, 0.0, 160, _asset(media["hd_untagged"], "bt601"))


def test_the_faithful_preview_fingerprint_follows_the_matrix(media):
    from core.filter_graph import fingerprint_plan

    def key(choice):
        asset = _asset(media["hd_untagged"], choice)
        plan = build_render_plan(_project(asset))
        return fingerprint_plan(plan, width=asset.width, height=asset.height, fps=FPS, quality="high")

    assert key("auto") != key("bt601") and key("bt601") != key("bt709")
    assert key("auto") == key("auto")


def test_png_thumbnail_decodes(media):
    """Garde du helper de test : la vignette est un vrai PNG de la taille demandée."""
    asset = _asset(media["hd_untagged"])
    data = extract_thumbnail(asset.path, 0.0, 160, asset)
    assert data and data[:8] == b"\x89PNG\r\n\x1a\n" and len(io.BytesIO(data).getvalue()) > 100
