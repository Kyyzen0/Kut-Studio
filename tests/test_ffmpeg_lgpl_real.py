"""Tests réels contre le FFmpeg **LGPL** embarqué pour le Mac App Store (ADR-0013, étape 2 ; ``packaging/ffmpeg-lgpl/``).

Ils lancent le FFmpeg construit par ``packaging/ffmpeg-lgpl/build.sh`` (sans ``--enable-gpl``, sans x264 / x265), désigné par
``KUT_STUDIO_FFMPEG_DIR`` comme le fait ``build.py`` : l'application, ``core/tool_paths.py`` et le lanceur unique le trouvent
sans rien changer. **Sautés avec la raison** quand la variable manque, quand le dossier n'a pas de ``ffmpeg``, ou quand ce
FFmpeg est une construction GPL (celui de Homebrew) ; **échoués** avec ``KUT_STUDIO_REQUIRE_FFMPEG_LGPL=1`` (un job vert dit alors
« le FFmpeg LGPL a été testé »).

    export KUT_STUDIO_FFMPEG_DIR=packaging/ffmpeg-lgpl/_build/prefix/bin
    python -m pytest tests/test_ffmpeg_lgpl_real.py

Quatre choses sont prouvées : la construction est bien LGPL et ne lie que le système ; un export de bout en bout (effets animés,
étalonnage animé par ``sendcmd`` + ``lut``, sous-titres libass, audio AAC, H.264 par VideoToolbox) réussit avec le lanceur de
l'application ; les images des filtres déterministes sont celles d'un FFmpeg de référence (``KUT_STUDIO_FFMPEG_REFERENCE`` ou le
``ffmpeg`` du ``PATH``) ; aucun appel ne dépend du ``PATH``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from core.animation import InterpolationType, Keyframe
from core.color_grading import ColorGrade
from core.effects_model import EffectType, create_effect, fx_property_id
from core.export_engine import (
    ExportEngine,
    ExportFormat,
    ExportPreset,
    ExportRequest,
    _ffmpeg_command_prefix,
    decoder_threads,
    input_arguments,
)
from core.export_runner import run_export
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.timeline_operations import add_subtitle_clip
from core.tool_paths import find_media_tool
from tools import ffmpeg_license_audit

REQUIRE_VARIABLE = "KUT_STUDIO_REQUIRE_FFMPEG_LGPL"
W, H, FPS, SECONDS = 160, 90, 25, 3


def _lgpl_ffmpeg() -> tuple[str | None, str]:
    """``(chemin, raison du saut)`` du FFmpeg LGPL désigné par ``KUT_STUDIO_FFMPEG_DIR``."""
    directory = os.environ.get("KUT_STUDIO_FFMPEG_DIR")
    if not directory:
        return None, "KUT_STUDIO_FFMPEG_DIR absent : FFmpeg LGPL non construit (packaging/ffmpeg-lgpl/build.sh)"
    path = Path(directory).expanduser() / "ffmpeg"
    if not path.is_file():
        return None, f"pas de ffmpeg dans {directory}"
    version = subprocess.run([str(path), "-hide_banner", "-version"], capture_output=True, text=True, timeout=60).stdout
    if "--enable-gpl" in version:
        return None, f"{path} est une construction GPL, pas l'artefact LGPL"
    return str(path.resolve()), ""


LGPL, SKIP_REASON = _lgpl_ffmpeg()
if LGPL is None and os.environ.get(REQUIRE_VARIABLE, "").lower() in {"1", "true", "yes", "on"}:
    pytest.fail(f"{REQUIRE_VARIABLE}=1 mais {SKIP_REASON}", pytrace=False)

pytestmark = pytest.mark.skipif(LGPL is None, reason=SKIP_REASON or "FFmpeg LGPL indisponible")


@pytest.fixture(autouse=True)
def _hardware_encoding_on(monkeypatch):
    """``conftest.py`` coupe l'encodage matériel pour les tests ; ici VideoToolbox est le seul encodeur H.264 de la construction."""
    monkeypatch.delenv("KUT_STUDIO_HARDWARE_ENCODING", raising=False)


def _reference() -> str | None:
    """FFmpeg de comparaison (n'importe quelle construction, GPL comprise) : jamais l'artefact lui-même."""
    candidate = os.environ.get("KUT_STUDIO_FFMPEG_REFERENCE") or shutil.which("ffmpeg")
    if candidate and LGPL and Path(candidate).resolve() != Path(LGPL).resolve():
        return candidate
    return None


def _run(command: list[str], **options) -> subprocess.CompletedProcess:
    done = subprocess.run(command, capture_output=True, timeout=300, **options)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")[-1500:]
    return done


# ---------------------------------------------------------------------------
# 1. La construction
# ---------------------------------------------------------------------------


def test_the_build_is_lgpl_with_the_expected_features():
    assert LGPL is not None
    licence = _run([LGPL, "-hide_banner", "-L"]).stdout.decode()
    version = _run([LGPL, "-hide_banner", "-version"]).stdout.decode()
    assert "GNU Lesser General Public" in licence and "GNU General Public" not in licence.replace("GNU Lesser General Public", "")
    for forbidden in ("--enable-gpl", "--enable-nonfree", "--enable-libx264", "--enable-libx265", "--enable-version3"):
        assert forbidden not in version
    for wanted in ("--enable-libass", "--enable-libdav1d", "--enable-videotoolbox", "--enable-audiotoolbox"):
        assert wanted in version
    encoders = _run([LGPL, "-hide_banner", "-encoders"]).stdout.decode()
    for present in ("h264_videotoolbox", "hevc_videotoolbox", "prores_ks", "aac_at", "pcm_s16le", "png", "ffv1"):
        assert f" {present} " in encoders, present
    for absent in ("libx264", "libx265"):
        assert f" {absent} " not in encoders, absent


def test_every_filter_the_application_uses_exists_in_this_build():
    """``tools.ffmpeg_license_audit`` : aucun filtre GPL dans le code, aucun filtre employé absent de cette construction."""
    assert ffmpeg_license_audit.main(["--ffmpeg", str(LGPL)]) == 0


def test_the_codec_table_has_no_missing_non_gpl_item_the_application_needs_in_the_store_path():
    """Seuls manquent les éléments du banc de décodage matériel (``libvpx-vp9`` / ``libsvtav1`` fabriquent des échantillons de test)."""
    from tools.ffmpeg_capabilities import table

    _text, missing = table(str(LGPL))
    assert {item.name for item in missing} <= {"libvpx-vp9", "libsvtav1"}


@pytest.mark.skipif(shutil.which("otool") is None, reason="otool absent")
@pytest.mark.parametrize("tool", ["ffmpeg", "ffprobe"])
def test_the_binaries_link_only_system_libraries(tool):
    assert LGPL is not None
    binary = str(Path(LGPL).with_name(tool))
    lines = _run(["otool", "-L", binary]).stdout.decode().splitlines()[1:]
    paths = [line.split()[0] for line in lines if line.strip()]
    assert paths and all(path.startswith(("/usr/lib/", "/System/Library/")) for path in paths), paths


AV1_SAMPLE_BASE64 = (
    "AAAAIGZ0eXBpc29tAAACAGlzb21hdjAxaXNvMm1wNDEAAAAIZnJlZQAABGdtZGF0CgsCAAAFFX+MSvkAQDLCBxAAloAQQIIB94Da"
    "yLbaS76YY4SVkoFiIZue+K+WCCFLZKQCfF2JyHp65Lp670hDT56L7zxplYvr9xTkK5mB2BwcIH2sRWXvarwE9UnPKz1Xmjsig4gi"
    "wTpYnoe9m8hDiAf0RHO0nIyLIUHavgztlNGkgIVcaskBZddgT9CejCInb4jzRl0Ks4ETgdSdQQdixmXLGMsM2XmcjsZxsOrEByU+"
    "r9NoS+eqEBXoV5xgzMRyhLsJ3ZUqwCsFhT93RwureH9/UTb71WSB1vLd/FKfmu6LYO5gnuYQJh+POzVkJjSEnFyEbNxY4HTVC3uD"
    "H4b+PhOs+DcAdBuAWARNkI6Dwde8vmu1KF/3ioOBOcHSgiNSX78Rs6yUJBYOZBfMY+Zv013TB2npNTP0psAThv7goLqsoRj7SaMH"
    "WFfg81CzlTHB8GhKoYwdsH/r3J+i34Qrbw8ZgnO+pUjvKJRJNGaUdIksHETHaLlAEUz56akni+ddloddPLH2iwTw9ojEOPOlyXGj"
    "6qkTEv2o03dkC6eVkhfi4gkLcEySgUiBhc/OzsEcg6mvj/6a3XHdL75pV7w8DUD4wN8yVqU6GDww6JIWrNQ0HQ/264b/pyj8YyCE"
    "HZYkuOvc3lwbaewYpQILhquLXDahgucJPk0m+uHl3Qimde4zR4w0dX+UJEH3t5yUfQxumg6dbt9bxOQY2IegH4C34Qahld/8wFtZ"
    "G07TtQb8cL7oTR80MioXqH3pt6lsG3izmzclSL5gWx0opK+E3Jv/kHAKTJTvlot4xdcbF+o7bSi5GuiRAO8vvX8UvZSZBKMsS3LQ"
    "5mb67RdAnOtTq2rQ2Eb5d5F40ZRlhuMRRbSF7Wcy9UAKGTvqDeZb8V819lzSSuJJ8iaotq6OSILNEUTxjotFvgC0b3L/4sf3cbdu"
    "l18dV5D3+tbtZkGBc80UdpNIqrFuul6ciTUut393L4a29oB6CWz2W4bDR9+hxL72Ivg0NuMfbFaRVVHRrjikVqXZeXCcZ6ZB9Owz"
    "MrClTTt256E5mbfd5Qs3oJfkkpZWzn2zDnHWSwkMoFPI6hmyFRa8zty7W9kDyEKTar2TKEKiGhI7dzLEK2bkOouzlgxXeskgSAJN"
    "9nyRjItTzQpoZq3lh/fstCKmo4qRmAfUO8sG80vD0dSo0MdIOsctlS4JdBiRU7I9clmdMocs7guZYrSy4W99Lipx+p42meDNoCQX"
    "jqMlNbhUqJ977L1rQeBl5d8szkUWkpPzX9jiY1PbT1qPpe0wHY597lmHLCLlFHEqTUOcMnUoAgBAAAAxTwAAAQLxAACz8WKo7fQz"
    "8VekiG8aXDjgq2JThMDZZheYZFerqOoDaLzsb1F3311ce/xHftBX9ZZEMOAf/9ouUO6Cin+mpzAxg/nXeQn8aDJzllseuzQyQjmT"
    "lms5YW1+XPrgAAbsojaG2gGWHYAyETACAAAASSMYAAAEQAMAAJGQGgGYAAADMm1vb3YAAABsbXZoZAAAAAAAAAAAAAAAAAAAA+gA"
    "AAJYAAEAAAEAAAAAAAAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    "AAAAAAAAAAIAAAJddHJhawAAAFx0a2hkAAAAAwAAAAAAAAAAAAAAAQAAAAAAAAJYAAAAAAAAAAAAAAAAAAAAAAABAAAAAAAAAAAA"
    "AAAAAAAAAQAAAAAAAAAAAAAAAAAAQAAAAABAAAAAJAAAAAAAJGVkdHMAAAAcZWxzdAAAAAAAAAABAAACWAAAAAAAAQAAAAAB1W1k"
    "aWEAAAAgbWRoZAAAAAAAAAAAAAAAAAAAKAAAABgAVcQAAAAAAC1oZGxyAAAAAAAAAAB2aWRlAAAAAAAAAAAAAAAAVmlkZW9IYW5k"
    "bGVyAAAAAYBtaW5mAAAAFHZtaGQAAAABAAAAAAAAAAAAAAAkZGluZgAAABxkcmVmAAAAAAAAAAEAAAAMdXJsIAAAAAEAAAFAc3Ri"
    "bAAAAK1zdHNkAAAAAAAAAAEAAACdYXYwMQAAAAAAAAABAAAAAAAAAAAAAAAAAAAAAABAACQASAAAAEgAAAAAAAAAARZMYXZjNjMu"
    "MS4xMDIgbGlic3Z0YXYxAAAAAAAAAAAAABj//wAAABlhdjFDgQAMAAoLAgAABRV/jAL4AEAAAAAKZmllbAEAAAAAEHBhc3AAAAAB"
    "AAAAAQAAABRidHJ0AAAAAAAAOkgAADpIAAAAGHN0dHMAAAAAAAAAAQAAAAMAAAgAAAAAFHN0c3MAAAAAAAAAAQAAAAEAAAAPc2R0"
    "cAAAAAAgGBAAAAAcc3RzYwAAAAAAAAABAAAAAQAAAAMAAAABAAAAIHN0c3oAAAAAAAAAAAAAAAMAAAPSAAAAigAAAAMAAAAUc3Rj"
    "bwAAAAAAAAABAAAAMAAAAGF1ZHRhAAAAWW1ldGEAAAAAAAAAIWhkbHIAAAAAAAAAAG1kaXJhcHBsAAAAAAAAAAAAAAAALGlsc3QA"
    "AAAkqXRvbwAAABxkYXRhAAAAAQAAAABMYXZmNjMuMS4xMDI="
)
"""Un AV1 minuscule (64×36, 3 images, 2 Ko), encodé hors dépôt avec SVT-AV1 : l'artefact n'a aucun encodeur AV1, seulement ``libdav1d``."""


def test_av1_decodes_in_software_with_libdav1d(tmp_path):
    import base64

    sample = tmp_path / "sample.mp4"
    sample.write_bytes(base64.b64decode("".join(AV1_SAMPLE_BASE64.split())))
    assert LGPL is not None
    assert " libdav1d " in _run([LGPL, "-hide_banner", "-decoders"]).stdout.decode()
    for decoder in ("libdav1d", None):          # explicite, puis le choix par défaut de FFmpeg
        command = [LGPL, "-v", "error", *(["-c:v", decoder] if decoder else []), "-i", str(sample), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
        data = _run(command).stdout
        assert len(data) == 3 * 64 * 36 * 3 and max(data) > 0, f"{decoder or 'défaut'} : 3 images attendues"


# ---------------------------------------------------------------------------
# 2. Média et projet de test (tout est fabriqué par le FFmpeg LGPL lui-même : pas de x264)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict[str, str]:
    assert LGPL is not None
    folder = tmp_path_factory.mktemp("lgpl")
    sources: dict[str, str] = {}
    # Une vidéo H.264 faite par VideoToolbox (donc décodée ensuite par le décodeur H.264 natif) et un son AAC.
    scene = folder / "scene.mp4"
    _run([LGPL, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={FPS}:d={SECONDS}", "-f", "lavfi", "-i",
          f"sine=frequency=440:r=48000:d={SECONDS}", "-c:v", "h264_videotoolbox", "-b:v", "2M", "-pix_fmt", "yuv420p", "-c:a", "aac",
          "-shortest", str(scene)])
    sources["scene"] = str(scene)
    # Fond uni pour les sous-titres (le texte se voit par les pixels qui s'écartent du fond).
    flat = folder / "flat.mp4"
    _run([LGPL, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x203040:s=320x180:r={FPS}:d={SECONDS}", "-c:v", "mpeg4",
          "-q:v", "2", "-pix_fmt", "yuv420p", str(flat)])
    sources["flat"] = str(flat)
    return sources


def _animated_clip(clip_id: str = "c") -> Clip:
    blur = create_effect(EffectType.BLUR)
    vignette = create_effect(EffectType.VIGNETTE)
    sepia = create_effect(EffectType.SEPIA)
    clip = Clip(clip_id, "scene", "V1", 0.0, 0.0, float(SECONDS), effects=[blur, vignette, sepia])
    clip.color_grade = ColorGrade(exposure=0.1, contrast=0.2, saturation=1.2, temperature=10.0)
    clip.animation = [
        Keyframe(fx_property_id(blur.id, "intensity"), 0.0, 1.0, InterpolationType.LINEAR),
        Keyframe(fx_property_id(blur.id, "intensity"), 2.0, 5.0, InterpolationType.LINEAR),
        Keyframe("color.contrast", 0.0, -0.3, InterpolationType.LINEAR),
        Keyframe("color.contrast", 2.5, 0.4, InterpolationType.EASE_IN_OUT),
        Keyframe("color.saturation", 0.0, 0.4, InterpolationType.LINEAR),
        Keyframe("color.saturation", 2.5, 1.6, InterpolationType.LINEAR),
    ]
    return clip


def _project(media: dict[str, str], *, subtitles: bool = False) -> Project:
    assets = [MediaAsset("scene", media["scene"], "scene", float(SECONDS), W, H, float(FPS), "video", has_audio=True)]
    project = Project("lgpl", width=W, height=H, fps=float(FPS), media_assets=assets,
                      tracks=[Track("V1", "V1", "video", clips=[_animated_clip()])])
    if subtitles:
        project.tracks.append(Track("S1", "S1", "subtitle"))
        add_subtitle_clip(project, "Bonjour le monde", 0.5, 2.0, "S1")
    return project


def _raw_frames(ffmpeg: str, project: Project, tmp_path: Path, tag: str) -> np.ndarray:
    """Images RGB du graphe de l'export (sans encodeur), rendues par ``ffmpeg``."""
    plan = build_render_plan(project)
    graph, video, audio, inputs = ExportEngine._build_filter_complex(plan, W, H, float(FPS), None)
    graph += f";[{video}]format=rgb24[probe];[{audio}]anullsink"
    command = [ffmpeg, "-y", "-v", "error", "-nostdin"]
    for item in inputs:
        command += input_arguments(item, threads=decoder_threads(inputs))
    command += ["-filter_complex", graph, "-map", "[probe]", "-f", "rawvideo", "-"]
    data = _run(command).stdout
    assert len(data) % (W * H * 3) == 0 and data
    return np.frombuffer(data, dtype=np.uint8).reshape(-1, H, W, 3)


# ---------------------------------------------------------------------------
# 3. Export de bout en bout avec le lanceur de l'application
# ---------------------------------------------------------------------------


def _export(project: Project, output: Path, *, hardware: str = "videotoolbox"):
    request = ExportRequest(render_plan=build_render_plan(project), output_path=str(output), format=ExportFormat.MP4_H264,
                            preset=ExportPreset(name="lgpl", resolution=(W, H), crf=23, audio_bitrate="96k"), fps=float(FPS),
                            hardware=hardware)
    return run_export(request)


def _probe(path: Path) -> dict:
    ffprobe = str(Path(LGPL).with_name("ffprobe"))
    out = _run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]).stdout
    return json.loads(out)


def test_the_application_resolves_the_lgpl_ffmpeg_not_the_path(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    assert _ffmpeg_command_prefix()[0] == LGPL
    assert find_media_tool("ffmpeg") == LGPL
    assert find_media_tool("ffprobe") == str(Path(LGPL).with_name("ffprobe"))


def test_an_end_to_end_export_with_animated_effects_grade_audio_and_subtitles(media, tmp_path):
    """H.264 par VideoToolbox, AAC, effets et étalonnage animés (``sendcmd`` + ``lut``), sous-titres libass : un seul fichier."""
    project = _project(media, subtitles=True)
    result = _export(project, tmp_path / "out.mp4")
    assert result.ok, result.message
    assert result.encoder is not None and result.encoder.encoder == "h264_videotoolbox"
    info = _probe(tmp_path / "out.mp4")
    codecs = {stream["codec_type"]: stream["codec_name"] for stream in info["streams"]}
    assert codecs == {"video": "h264", "audio": "aac"}
    assert float(info["format"]["duration"]) == pytest.approx(SECONDS, abs=0.2)


def test_the_export_does_not_depend_on_the_path(media, tmp_path, monkeypatch):
    """Même export, ``PATH`` vidé : aucun appel ne va chercher un outil ailleurs que dans le dossier désigné."""
    monkeypatch.setenv("PATH", "/nonexistent")
    result = _export(_project(media), tmp_path / "nopath.mp4")
    assert result.ok, result.message


def test_explicit_cpu_h264_fails_clearly_since_libx264_is_not_in_a_lgpl_build(media, tmp_path):
    """Étape 3 de l'ADR : le CPU H.264 (libx264) n'existe pas ici ; l'échec est signalé, jamais masqué ni silencieux."""
    result = _export(_project(media), tmp_path / "cpu.mp4", hardware="cpu")
    assert not result.ok and not (tmp_path / "cpu.mp4").exists()


@pytest.mark.libass
def test_libass_draws_the_subtitle_of_the_application(media, tmp_path):
    """Les sous-titres sont dessinés (polices du système par CoreText) : des pixels clairs apparaissent sur un fond uni."""
    assets = [MediaAsset("flat", media["flat"], "flat", float(SECONDS), 320, 180, float(FPS), "video", has_audio=False)]
    project = Project("sub", width=320, height=180, fps=float(FPS), media_assets=assets,
                      tracks=[Track("V1", "V1", "video", clips=[Clip("bg", "flat", "V1", 0.0, 0.0, float(SECONDS))])])
    project.tracks.append(Track("S1", "S1", "subtitle"))
    add_subtitle_clip(project, "Bonjour le monde", 0.5, 2.0, "S1")
    request = ExportRequest(render_plan=build_render_plan(project), output_path=str(tmp_path / "sub.mp4"), format=ExportFormat.MP4_H264,
                            preset=ExportPreset(name="lgpl", resolution=(320, 180), crf=23, audio_bitrate="96k"), fps=float(FPS),
                            hardware="videotoolbox")
    assert run_export(request).ok
    with_text = _run([LGPL, "-v", "error", "-ss", "1.0", "-i", str(tmp_path / "sub.mp4"), "-frames:v", "1", "-vf", "format=gray",
                      "-f", "rawvideo", "-"]).stdout
    without = _run([LGPL, "-v", "error", "-ss", "2.8", "-i", str(tmp_path / "sub.mp4"), "-frames:v", "1", "-vf", "format=gray",
                    "-f", "rawvideo", "-"]).stdout
    background = np.frombuffer(without, dtype=np.uint8).astype(int)
    drawn = np.frombuffer(with_text, dtype=np.uint8).astype(int)
    assert np.abs(background - np.median(background)).max() <= 12, "l'image hors sous-titre devrait être unie"
    assert (np.abs(drawn - np.median(background)) > 60).sum() > 100, "libass n'a rien dessiné"


# ---------------------------------------------------------------------------
# 4. Comparaison avec une référence sur les filtres déterministes
# ---------------------------------------------------------------------------


@pytest.mark.skipif(_reference() is None, reason="aucun FFmpeg de référence distinct (KUT_STUDIO_FFMPEG_REFERENCE ou ffmpeg du PATH)")
def test_deterministic_filters_match_the_reference_build(media, tmp_path):
    """Effets (flou, vignette, sépia) et étalonnage animés, sur la même source : mêmes images à une unité de niveau près.

    La référence peut être une autre version de FFmpeg (Homebrew 9.x) : on tolère un écart d'arrondi, pas un filtre différent."""
    reference = _reference()
    assert reference is not None and LGPL is not None
    project = _project(media)
    ours = _raw_frames(LGPL, project, tmp_path, "lgpl").astype(int)
    theirs = _raw_frames(reference, project, tmp_path, "ref").astype(int)
    assert ours.shape == theirs.shape
    difference = np.abs(ours - theirs)
    assert difference.max() <= 3, f"écart maximal {difference.max()} niveaux"
    assert difference.mean() < 0.1
    # La preuve que le test voit quelque chose : l'animation change bien les images au fil du temps.
    assert np.abs(ours[0] - ours[-1]).mean() > 1.0
