"""Matrice couleur d'un média (:mod:`core.color_matrix`) : la règle, le modèle, le format ``.kut``, le réglage et le verbe de script.

La parité réelle (moniteur = export = miniature, sur des fichiers générés) est dans ``test_color_matrix_parity.py``.
"""

from __future__ import annotations

import json
import math
import time

import pytest

from core import color_matrix as cm
from core import project_io
from core.gpu_frames import resolve_color, yuv_to_rgb_matrix
from core.media_color_ops import MediaColorError, media_color_refusal, set_media_color_matrix
from core.project_io import (
    COLOR_MATRIX_VERSION, CURRENT_VERSION, PATH_MASK_VERSION, SUPPORTED_VERSIONS, load_project, project_payload, save_project,
)
from core.project_model import Clip, MediaAsset, Project, Track
from core.scripting import Session, apply_script, parse_script
from core.scripting.verbs import VERBS
from ui import i18n


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


# --- La règle : taille × étiquette × choix ---------------------------------------------------------------------------------


@pytest.mark.parametrize("width, height, hd", [
    (1280, 720, True), (1280, 719, True), (1279, 720, True), (1279, 719, False),     # les deux limites, à l'unité près
    (1920, 1080, True), (3840, 2160, True), (720, 576, False), (640, 360, False),
    (720, 1280, True), (1080, 1920, True),                                           # portrait : 1280 colonnes ou 720 lignes
    (1280, 1, True), (1, 720, True), (1, 1, False),
    (0, 0, False), (-1, -1, False), (-5000, 4000, True), (10**12, 10**12, True),     # dimensions nulles, négatives, énormes
])
def test_the_size_rule_has_its_limits_at_720_rows_and_1280_columns(width, height, hd):
    assert cm.is_hd(width, height) is hd
    expected = "bt709" if hd else "bt601"
    assert cm.default_for_size(width, height) == expected
    assert cm.effective_matrix("", width, height) == expected                       # inconnu : la taille décide
    assert cm.effective_matrix(cm.LABEL_NONE, width, height) == expected


@pytest.mark.parametrize("bad", [None, "1280", float("nan"), float("inf"), -float("inf"), True, [1280], 1e300])
def test_unreadable_dimensions_are_not_hd(bad):
    assert cm.is_hd(bad, 100) is (bad == 1e300) and cm.is_hd(100, bad) is (bad == 1e300)


@pytest.mark.parametrize("label", ["bt601", "bt709", "bt2020"])
@pytest.mark.parametrize("size", [(640, 360), (1920, 1080)])
def test_a_known_label_is_respected_whatever_the_size(label, size):
    assert cm.effective_matrix(label, *size) == label and cm.effective_matrix(label, *size, "auto") == label


@pytest.mark.parametrize("label", ["", "none", "bt601", "bt709", "bt2020", "other"])
@pytest.mark.parametrize("size", [(640, 360), (1920, 1080)])
@pytest.mark.parametrize("choice", ["bt601", "bt709"])
def test_a_forced_choice_beats_the_label_and_the_size(label, size, choice):
    assert cm.effective_matrix(label, *size, choice) == choice


def test_an_unconvertible_label_stays_in_601_for_the_monitor_like_before():
    assert cm.effective_matrix("other", 1920, 1080) == "bt601"


@pytest.mark.parametrize("name, label", [
    ("bt709", "bt709"), ("BT709 ", "bt709"), ("bt470bg", "bt601"), ("smpte170m", "bt601"), ("bt2020nc", "bt2020"),
    ("bt2020c", "bt2020"), ("unknown", "none"), ("unspecified", "none"), ("", "none"), ("N/A", "none"), (None, "none"),
    (7, "none"), (["bt709"], "none"), ("smpte240m", "other"), ("gbr", "other"), ("ycgco", "other"), ("x" * 5000, "other"),
], ids=lambda v: repr(v)[:20])
def test_ffprobe_labels_are_normalised_and_hostile_values_never_raise(name, label):
    assert cm.label_from_ffprobe(name) == label


def test_what_ffmpeg_must_be_told_is_only_what_it_would_not_do_alone():
    f = cm.ffmpeg_override
    assert f("none", 1920, 1080) == "bt709" and f("", 1280, 720) == "bt709"            # le seul changement pour un non étiqueté
    assert f("none", 640, 360) == "" and f("", 1279, 719) == ""                         # SD : FFmpeg lit déjà du 601
    for label in ("bt601", "bt709", "bt2020", "other"):
        assert f(label, 1920, 1080) == "" and f(label, 320, 240) == ""                   # étiqueté : l'étiquette suffit
    assert f("none", 1920, 1080, "bt601") == "bt470bg" and f("bt601", 1920, 1080, "bt709") == "bt709"
    assert f("bt709", 640, 360, "bt709") == "" and f("bt601", 640, 360, "bt601") == ""   # le choix égale l'étiquette : rien à dire
    assert f("other", 640, 360, "bt709") == "bt709"
    assert cm.setparams_filter("bt709") == "setparams=colorspace=bt709" and cm.setparams_filter("") == ""
    assert cm.setparams_filter("bt709:range=pc") == "" and cm.setparams_filter("rm -rf") == ""    # jamais un texte inconnu


def test_choices_are_validated_strictly():
    assert [cm.normalize_choice(v) for v in cm.MATRIX_CHOICES] == ["auto", "bt601", "bt709"]
    for bad in ("", "BT709", "bt2020", "auto ", None, 3, ["auto"], "x" * 10_000):
        with pytest.raises(ValueError) as error:
            cm.normalize_choice(bad)
        assert len(str(error.value)) < 200                                              # le message ne recopie pas l'entrée


def test_cache_token_is_empty_when_the_reading_did_not_change_so_old_keys_stay_valid():
    assert cm.cache_token("none", 640, 360) == "" and cm.cache_token("bt709", 1920, 1080) == ""
    assert cm.cache_token("none", 1920, 1080) == f"m{cm.RULE_VERSION}-bt709"
    assert cm.cache_token("none", 1920, 1080, "bt601") != cm.cache_token("none", 1920, 1080)


# --- Les fonctions partagées avec le moniteur ------------------------------------------------------------------------------


def test_resolve_color_and_the_matrix_keep_their_old_signature_and_default_to_601_without_dimensions():
    assert resolve_color("", "") == ("bt601", "video") and resolve_color("bt709", "full") == ("bt709", "full")
    assert resolve_color("", "", 1920, 1080) == ("bt709", "video")                        # inconnu : la taille de la source
    assert resolve_color("", "", 640, 360) == ("bt601", "video")
    assert resolve_color("bt601", "", 1920, 1080) == ("bt601", "video")                    # étiqueté : respecté
    assert resolve_color("bt601", "", 1920, 1080, "bt709") == ("bt709", "video")           # forcé : gagne
    assert resolve_color("weird", "weird", 1920, 1080) == ("bt709", "video")               # étiquette inconnue de Qt = inconnue
    assert yuv_to_rgb_matrix() == yuv_to_rgb_matrix("", "") == yuv_to_rgb_matrix("bt601", "video")
    assert yuv_to_rgb_matrix("", "", 1920, 1080) == yuv_to_rgb_matrix("bt709", "video") != yuv_to_rgb_matrix()
    assert yuv_to_rgb_matrix("", "full", 1920, 1080) == yuv_to_rgb_matrix("bt709", "full")  # la plage inconnue reste limitée


def test_the_plan_of_a_frame_reads_the_matrix_the_rule_gives():
    """``plan_frame`` et la référence numpy passent la taille de la source et le choix : un HD non étiqueté a la matrice 709."""
    from core.gpu_composite import CompositeFrame, CompositeLayer, VideoSource, plan_frame

    layer = CompositeLayer(source="m", matrix=(1.0, 0.0, 0.0, 1.0, 0.0, 0.0), fit=(0.0, 0.0, 64.0, 36.0))

    def uniform_of(source):
        plan = plan_frame(CompositeFrame(64, 36, 1.0, (layer,), (source,)))
        return plan

    sd = uniform_of(VideoSource("m", "yuv420p", 64, 36))
    hd = uniform_of(VideoSource("m", "yuv420p", 1920, 1080))
    forced = uniform_of(VideoSource("m", "yuv420p", 64, 36, color_choice="bt709"))
    assert sd != hd and forced != sd                                                       # la matrice change le plan (uniformes)
    # … et c'est bien la matrice attendue : le plan est identique octet pour octet à celui d'une source étiquetée.
    assert hd == uniform_of(VideoSource("m", "yuv420p", 1920, 1080, colorspace="bt709"))
    assert sd == uniform_of(VideoSource("m", "yuv420p", 64, 36, colorspace="bt601"))
    assert forced == uniform_of(VideoSource("m", "yuv420p", 64, 36, colorspace="bt709"))
    assert uniform_of(VideoSource("m", "yuv420p", 1920, 1080, colorspace="bt601")) != hd      # étiqueté 601 en HD : respecté


# --- Le modèle média -------------------------------------------------------------------------------------------------------


def _asset(**extra) -> MediaAsset:
    return MediaAsset("a", "/x/a.mp4", "a", 4.0, 1920, 1080, 25.0, "video", **extra)


def test_a_media_has_auto_by_default_and_refuses_an_unknown_choice():
    assert _asset().color_matrix == "auto" and _asset().color_space == ""
    assert _asset(color_matrix="bt709").color_matrix == "bt709"
    for bad in ("", "BT709", "bt2020", None, 1):
        with pytest.raises(ValueError, match="Espace colorimétrique"):
            _asset(color_matrix=bad)


def test_the_probed_label_is_a_runtime_value_that_never_changes_equality_and_is_cleaned():
    assert _asset(color_space="bt709") == _asset() and _asset(color_space="nonsense").color_space == ""
    assert _asset(color_space=12).color_space == ""


def test_importing_a_file_records_its_label(tmp_path):
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg absent")
    from core.media_probe import probe_color_label, probe_media

    def make(name, size, tags):
        path = tmp_path / name
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:r=10:d=1", "-vf",
                        f"format=yuv420p,setparams={tags}", "-c:v", "ffv1", str(path)], check=True, timeout=60)
        return str(path)

    none = make("none.mkv", "1280x720", "colorspace=unknown:range=unknown:color_primaries=unknown:color_trc=unknown")
    tagged = make("tagged.mkv", "1280x720", "colorspace=bt709:range=tv")
    sd601 = make("sd601.mkv", "320x240", "colorspace=smpte170m:range=tv")
    assert [probe_media(p).color_space for p in (none, tagged, sd601)] == ["none", "bt709", "bt601"]
    assert [probe_color_label(p) for p in (none, tagged, sd601)] == ["none", "bt709", "bt601"]
    assert probe_color_label(str(tmp_path / "absent.mkv")) == "" and probe_color_label("") == ""
    junk = tmp_path / "junk.mkv"
    junk.write_bytes(b"\x00" * 4096)
    assert probe_color_label(str(junk)) == ""                                              # fichier hostile : pas d'étiquette, pas d'erreur


def test_the_lazy_probe_is_memorised_per_file_and_date(tmp_path, monkeypatch):
    from core import media_probe

    calls = []
    path = tmp_path / "v.bin"
    path.write_bytes(b"x")

    class Done:
        returncode = 0
        stdout = json.dumps({"streams": [{"color_space": "bt709"}]})

    monkeypatch.setattr(media_probe, "supervised_run", lambda *a, **k: calls.append(1) or Done())
    monkeypatch.setattr(media_probe, "find_media_tool", lambda name: "/bin/ffprobe")
    media_probe._COLOR_LABELS.clear()
    media_probe._COLOR_FAILURES.clear()
    assert [media_probe.probe_color_label(str(path)) for _ in range(3)] == ["bt709"] * 3 and len(calls) == 1
    path.write_bytes(b"xx")                                                                # fichier modifié : nouvelle sonde
    media_probe.probe_color_label(str(path))
    assert len(calls) == 2


@pytest.mark.parametrize("stdout", ["", "not json", "[]", "null", '{"streams": null}', '{"streams": [1]}', '{"streams": []}',
                                    '{"streams": [{"color_space": ["x"]}]}', "{" * 100_000, " " * 200_000],
                         ids=lambda v: repr(v)[:20])
def test_a_hostile_probe_output_never_raises(stdout, tmp_path, monkeypatch):
    from core import media_probe

    path = tmp_path / "v.bin"
    path.write_bytes(b"x")

    class Done:
        returncode = 0

    done = Done()
    done.stdout = stdout
    monkeypatch.setattr(media_probe, "supervised_run", lambda *a, **k: done)
    monkeypatch.setattr(media_probe, "find_media_tool", lambda name: "/bin/ffprobe")
    media_probe._COLOR_LABELS.clear()
    media_probe._COLOR_FAILURES.clear()
    assert media_probe.probe_color_label(str(path)) in ("", "none")


def test_a_probe_that_times_out_or_cannot_start_gives_no_label(tmp_path, monkeypatch):
    import subprocess

    from core import media_probe

    path = tmp_path / "v.bin"
    path.write_bytes(b"x")
    monkeypatch.setattr(media_probe, "find_media_tool", lambda name: "/bin/ffprobe")
    media_probe._COLOR_LABELS.clear()
    media_probe._COLOR_FAILURES.clear()
    for error in (subprocess.TimeoutExpired("ffprobe", 10), OSError("introuvable")):
        def boom(*a, error=error, **k):
            raise error

        monkeypatch.setattr(media_probe, "supervised_run", boom)
        assert media_probe.probe_color_label(str(path)) == ""


def test_only_an_auto_video_with_an_unknown_label_is_probed_whatever_its_size(monkeypatch):
    """Un SD étiqueté BT.709 rouvert (étiquette non écrite dans le projet) doit être lu en 709, comme l'export le fait."""
    from core import media_probe

    calls = []
    monkeypatch.setattr(media_probe, "probe_color_label", lambda path: calls.append(path) or "bt709")
    forced = MediaAsset("a", "/x/a.mp4", "a", 1.0, 1920, 1080, 25.0, "video", color_matrix="bt601")
    cm.asset_effective_matrix(forced)                                                                        # forcé : jamais
    cm.asset_effective_matrix(MediaAsset("a", "/x/a.mp4", "a", 1.0, 640, 360, 25.0, "video", color_space="none"))   # connu
    assert calls == []
    sd = MediaAsset("a", "/x/a.mp4", "a", 1.0, 640, 360, 25.0, "video")
    assert cm.asset_effective_matrix(sd) == "bt709" and calls == ["/x/a.mp4"] and sd.color_space == "bt709"
    assert cm.asset_effective_matrix(sd) == "bt709" and len(calls) == 1                                       # mémorisée sur le média
    assert cm.asset_effective_matrix(MediaAsset("a", "", "a", 1.0, 0, 0, 0.0, "audio", True)) == ""
    assert cm.asset_ffmpeg_override(None) == "" and cm.asset_effective_matrix(None) == ""
    image = MediaAsset("i", "/x/i.png", "i", 1.0, 1920, 1080, 1.0, "image")
    assert cm.asset_ffmpeg_override(image) == "" and calls == ["/x/a.mp4"]                                    # une photo n'a pas de matrice YUV


@pytest.mark.real_probe_threads
def test_the_interface_thread_never_waits_for_ffprobe_and_gets_the_label_once_warmed(tmp_path, monkeypatch):
    import threading

    from core import media_probe

    path = tmp_path / "v.bin"
    path.write_bytes(b"x")
    release, started = threading.Event(), threading.Event()

    def slow_probe(target):
        started.set()
        release.wait(5)
        media_probe._COLOR_LABELS[media_probe._color_key(target)] = "bt601"
        return "bt601"

    media_probe._COLOR_LABELS.clear()
    media_probe._COLOR_FAILURES.clear()
    monkeypatch.setattr(media_probe, "probe_color_label", slow_probe)
    asset = MediaAsset("a", str(path), "a", 1.0, 1920, 1080, 25.0, "video")
    begin = time.monotonic()
    assert cm.asset_effective_matrix(asset, probe=False, warm=True) == "bt709"        # pas encore connu : « non étiqueté »
    assert time.monotonic() - begin < 1.0 and started.wait(2)                          # … sans attendre, la sonde part en fond
    assert media_probe.warm_color_label(str(path)) is False                            # une seule à la fois par fichier
    release.set()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not media_probe.peek_color_label(str(path)):
        time.sleep(0.01)
    assert cm.asset_effective_matrix(asset, probe=False) == "bt601"                    # l'appel suivant la connaît


@pytest.mark.real_probe_threads
def test_opening_a_project_warms_the_labels_in_one_background_batch(tmp_path, monkeypatch):
    import threading

    from core import media_probe

    paths = []
    for name in ("a", "b", "a"):
        file = tmp_path / f"{name}.bin"
        file.write_bytes(b"x")
        paths.append(str(file))
    media_probe._COLOR_LABELS.clear()
    media_probe._COLOR_FAILURES.clear()
    seen, done = [], threading.Event()
    monkeypatch.setattr(media_probe, "probe_color_label", lambda p: (seen.append(p), len(seen) == 2 and done.set()) and "")
    assert media_probe.warm_color_labels(paths) is True and done.wait(3)
    assert seen == paths[:2] and media_probe.warm_color_labels([]) is False


# --- Le plan de rendu --------------------------------------------------------------------------------------------------


def _project(asset: MediaAsset | None = None, *, locked: bool = False) -> Project:
    asset = asset or _asset(color_space="none")
    clip = Clip("c", "a", "V1", 0.0, 0.0, 2.0)
    return Project("p", 1920, 1080, 25.0, media_assets=[asset], tracks=[Track("V1", "V1", "video", clips=[clip], locked=locked)])


def test_the_render_plan_carries_the_override_and_the_graph_applies_it_before_the_conversion():
    from core.export_engine import ExportEngine
    from core.render_plan import build_render_plan

    def graph_of(asset):
        plan = build_render_plan(_project(asset))
        return plan, ExportEngine._build_filter_complex(plan, 1920, 1080, 25, None)[0]

    plan, graph = graph_of(_asset(color_space="none"))
    assert plan.video_layers[0].color_override == "bt709"
    assert "setparams=colorspace=bt709,scale=1920:1080" in graph and graph.index("format=rgba", graph.index("setparams=colorspace=bt709")) > 0
    plan, graph = graph_of(_asset(color_space="bt709"))                                    # étiqueté : le graphe d'avant, mot pour mot
    assert plan.video_layers[0].color_override == "" and "setparams=colorspace=bt709,scale" not in graph
    plan, graph = graph_of(_asset(color_space="none", color_matrix="bt601"))
    assert plan.video_layers[0].color_override == "bt470bg" and "setparams=colorspace=bt470bg,scale=" in graph
    plan, graph = graph_of(MediaAsset("a", "/x/a.mp4", "a", 4.0, 640, 360, 25.0, "video", color_space="none"))
    assert plan.video_layers[0].color_override == "" and "colorspace=bt709,scale" not in graph and "colorspace=bt470bg" not in graph


def test_a_photo_and_a_nested_sequence_never_get_a_matrix():
    from core.export_engine import layer_color_filter
    from core.render_plan import RenderLayer

    layer = RenderLayer("c", "a", "V1", 0, "/x/a.png", 0.0, 1.0, 0.0, 1.0, color_override="bt709", still=True)
    assert layer_color_filter(layer) == ""
    video = RenderLayer("c", "a", "V1", 0, "/x/a.mp4", 0.0, 1.0, 0.0, 1.0, color_override="bt709")
    assert layer_color_filter(video) == "setparams=colorspace=bt709" and layer_color_filter(video, source="nested") == ""


def test_a_retimed_clip_prepares_its_frames_with_the_same_matrix():
    from core.retime_layers import _conform
    from core.render_plan import RenderLayer

    layer = RenderLayer("c", "a", "V1", 0, "/x/a.mp4", 0.0, 1.0, 0.0, 1.0, color_override="bt709")
    plain = RenderLayer("c", "a", "V1", 0, "/x/a.mp4", 0.0, 1.0, 0.0, 1.0)
    assert _conform(layer, 1920, 1080, None).startswith("setparams=colorspace=bt709,scale=1920:1080")
    assert "setparams" not in _conform(plain, 1920, 1080, None)
    assert _conform(layer, 2000, 1200, (2000, 1200)) == "setparams=colorspace=bt709,scale=2000:1200"


def test_the_lut_baking_and_the_cache_key_follow_the_rule_version(monkeypatch):
    from core import gpu_grade
    from core.color_grading import ColorGrade

    grade = ColorGrade(exposure=0.4)
    key = gpu_grade.lut_key(grade, colorspace="bt709")
    monkeypatch.setattr(gpu_grade, "RULE_VERSION", cm.RULE_VERSION + 1)               # une LUT cuite avec une autre règle
    assert gpu_grade.lut_key(grade, colorspace="bt709") != key
    command = gpu_grade.bake_command("ffmpeg", "hue=s=1.1", domain=gpu_grade.DOMAIN_YUV, colorspace="bt709")
    assert "setparams=colorspace=bt709:range=tv," in command[command.index("-vf") + 1]


def test_the_lut_of_a_hd_untagged_monitor_source_is_baked_in_709():
    """Le moniteur donne à la cuisson la matrice que la source lit réellement (:func:`resolve_color`), pas l'étiquette de Qt (vide)."""
    from ui.gpu_preview import GpuPreviewWidget  # noqa: F401 - import seul : la cuisson passe par ``resolve_color``

    assert resolve_color("", "video", 1920, 1080)[0] == "bt709" and resolve_color("", "video", 640, 360)[0] == "bt601"


# --- Le format .kut ------------------------------------------------------------------------------------------------------


def _saved(project: Project, tmp_path):
    path = tmp_path / "p.kut"
    save_project(project, str(path))
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_the_versions_and_what_this_build_reads():
    assert (CURRENT_VERSION, PATH_MASK_VERSION, COLOR_MATRIX_VERSION) == (17, 18, 19)
    assert {17, 18, 19} <= SUPPORTED_VERSIONS


def test_a_project_that_keeps_auto_is_byte_for_byte_what_it_was_and_still_version_17(tmp_path):
    path, raw = _saved(_project(), tmp_path)
    text = path.read_text(encoding="utf-8")
    assert raw["version"] == 17 and "color_matrix" not in text and "color_space" not in text
    assert list(raw["project"]["media_assets"][0]) == ["id", "path", "name", "duration", "width", "height", "fps", "media_type", "has_audio"]


def test_a_choice_makes_the_file_version_19_round_trips_and_is_idempotent(tmp_path):
    project = _project(_asset(color_matrix="bt709", color_space="none"))
    path, raw = _saved(project, tmp_path)
    assert raw["version"] == 19 and raw["project"]["media_assets"][0]["color_matrix"] == "bt709"
    assert "color_space" not in path.read_text(encoding="utf-8")                          # l'étiquette sondée n'est jamais écrite
    restored = load_project(str(path))
    asset = restored.media_assets[0]
    assert asset.color_matrix == "bt709" and asset.color_space == ""                      # l'étiquette se sondera à la demande
    again = tmp_path / "again.kut"
    save_project(restored, str(again))
    assert again.read_text(encoding="utf-8") == path.read_text(encoding="utf-8")


def test_going_back_to_auto_brings_the_file_back_to_17(tmp_path):
    project = _project(_asset(color_matrix="bt601"))
    assert _saved(project, tmp_path)[1]["version"] == 19
    project.media_assets[0].color_matrix = "auto"
    assert _saved(project, tmp_path)[1]["version"] == 17


def test_a_curve_and_a_choice_together_are_version_19(tmp_path):
    from core.compositing import Compositing, Mask, MaskShape

    project = _project(_asset(color_matrix="bt709"))
    project.tracks[0].clips[0].compositing = Compositing(masks=(Mask(shape=MaskShape.PATH, id="curve001"),))
    assert _saved(project, tmp_path)[1]["version"] == 19


def test_an_older_build_refuses_a_file_that_uses_the_choice_instead_of_losing_it(tmp_path, monkeypatch):
    path, _ = _saved(_project(_asset(color_matrix="bt601")), tmp_path)
    monkeypatch.setattr(project_io, "SUPPORTED_VERSIONS", SUPPORTED_VERSIONS - {COLOR_MATRIX_VERSION})
    with pytest.raises(ValueError, match="Version de fichier Kut-Studio non supportée.*19"):
        load_project(str(path))


def test_an_old_file_without_the_field_loads_as_auto(tmp_path):
    path, raw = _saved(_project(), tmp_path)
    assert "color_matrix" not in raw["project"]["media_assets"][0]
    assert load_project(str(path)).media_assets[0].color_matrix == "auto"


@pytest.mark.parametrize("bad", ["", "BT709", "bt2020", None, 7, ["bt709"], {"a": 1}, "x" * 100_000], ids=lambda v: repr(v)[:20])
def test_a_hand_edited_unknown_choice_refuses_the_file_with_a_short_message(tmp_path, bad):
    path, raw = _saved(_project(), tmp_path)
    raw["version"] = 19
    raw["project"]["media_assets"][0]["color_matrix"] = bad
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError) as error:
        load_project(str(path))
    assert "Espace colorimétrique" in str(error.value) and len(str(error.value)) < 300


def test_the_autosave_payload_follows_the_same_rule(tmp_path):
    assert project_payload(_project())["version"] == 17
    assert project_payload(_project(_asset(color_matrix="bt709")))["version"] == 19
    path = tmp_path / "auto.kut"
    project_io.write_project_payload(project_payload(_project(_asset(color_matrix="bt709"))), str(path))
    assert load_project(str(path)).media_assets[0].color_matrix == "bt709"


def test_the_cli_reads_a_version_19_file(tmp_path, capsys):
    from core import cli

    path, _ = _saved(_project(_asset(color_matrix="bt709")), tmp_path)
    assert cli.run(["info", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["format_version"] == 19
    assert cli.run(["validate", str(path)]) == 0


def test_a_history_copy_keeps_the_choice():
    from copy import deepcopy

    project = _project(_asset(color_matrix="bt601", color_space="bt709"))
    copy = deepcopy(project)
    assert copy.media_assets[0].color_matrix == "bt601" and copy.media_assets[0] == project.media_assets[0]


# --- Le réglage (cœur) : verrous, genre de média, valeur -----------------------------------------------------------------------


def test_setting_the_choice_changes_it_once_and_reports_whether_it_changed():
    project = _project()
    assert set_media_color_matrix(project, "a", "bt709") is True and project.media_assets[0].color_matrix == "bt709"
    assert set_media_color_matrix(project, "a", "bt709") is False
    assert set_media_color_matrix(project, "a", "auto") is True


@pytest.mark.parametrize("bad", ["", "BT709", "bt2020", None, 3])
def test_an_unknown_choice_is_refused_and_nothing_changes(bad):
    project = _project()
    with pytest.raises(MediaColorError, match="Espace colorimétrique"):
        set_media_color_matrix(project, "a", bad)
    assert project.media_assets[0].color_matrix == "auto"


def test_an_unknown_media_and_a_non_video_media_are_refused():
    project = _project()
    with pytest.raises(KeyError):
        set_media_color_matrix(project, "nope", "bt709")
    project.media_assets.append(MediaAsset("s", "/x/s.wav", "s", 1.0, 0, 0, 0.0, "audio", True))
    project.media_assets.append(MediaAsset("i", "/x/i.png", "i", 1.0, 100, 100, 1.0, "image"))
    for asset_id in ("s", "i"):
        assert "n'est pas une vidéo" in media_color_refusal(project, asset_id)
        with pytest.raises(MediaColorError, match="pas une vidéo"):
            set_media_color_matrix(project, asset_id, "bt709")
        assert project.media_assets[-1].color_matrix == "auto"


def test_a_media_read_by_a_locked_track_or_a_locked_layer_is_refused_in_every_sequence():
    project = _project(locked=True)
    assert "verrouillés" in media_color_refusal(project, "a")
    with pytest.raises(MediaColorError, match="verrouillés"):
        set_media_color_matrix(project, "a", "bt709")
    assert project.media_assets[0].color_matrix == "auto"
    project.tracks[0].locked = False
    assert media_color_refusal(project, "a") == ""
    from core.sequences import create_sequence

    other = create_sequence(project, "Autre")
    other.tracks[0].locked = True
    other.tracks[0].clips.append(Clip("d", "a", other.tracks[0].id, 0.0, 0.0, 1.0))
    assert project.active_sequence is not other and "verrouillés" in media_color_refusal(project, "a")      # une autre séquence compte
    other.tracks[0].locked = False
    assert media_color_refusal(project, "a") == ""
    project.tracks[0].clips[0].graphic = type("G", (), {"locked": True})()                                       # calque verrouillé
    assert "verrouillés" in media_color_refusal(project, "a")


def test_the_refusal_names_a_few_clips_not_all_of_them():
    project = _project(locked=True)
    project.tracks[0].clips = [Clip(f"c{i:04d}", "a", "V1", float(i), 0.0, 1.0) for i in range(2000)]
    text = media_color_refusal(project, "a")
    assert "c0000" in text and "+1997" in text and len(text) < 200


# --- Le verbe de script -----------------------------------------------------------------------------------------------------


def _script(*ops: dict) -> dict:
    return {"kut_script": 1, "ops": list(ops)}


def _run(project: Project, *ops: dict):
    session = Session(project)
    return session, apply_script(session, parse_script(_script(*ops)))


def test_the_verb_is_in_the_table_the_schema_and_the_docs():
    from pathlib import Path

    from core.scripting.schema import json_schema

    assert "set_media_color_matrix" in VERBS
    schema = json.dumps(json_schema())
    assert '"bt601"' in schema and '"bt709"' in schema and '"auto"' in schema
    assert "`set_media_color_matrix`" in (Path(__file__).resolve().parent.parent / "docs" / "scripting.md").read_text(encoding="utf-8")


def test_the_verb_sets_the_choice_by_media_or_by_clip_and_undoes_in_one_step(tmp_path):
    project = _project()
    session, report = _run(project, {"op": "set_media_color_matrix", "asset": "a", "matrix": "bt709"})
    assert report.ok, report.error and project.media_assets[0].color_matrix == "bt709"
    assert _saved(project, tmp_path)[1]["version"] == 19
    assert session.undo() and project.media_assets[0].color_matrix == "auto"
    assert _saved(project, tmp_path)[1]["version"] == 17
    session, report = _run(project, {"op": "set_media_color_matrix", "clip": "c", "matrix": "bt601"})
    assert report.ok, report.error and project.media_assets[0].color_matrix == "bt601"


def test_the_verb_by_name_and_in_one_transaction_with_other_verbs():
    project = _project()
    project.media_assets[0].name = "plan"
    session, report = _run(project, {"op": "set_media_color_matrix", "asset": "plan", "matrix": "bt709"},
                           {"op": "set_clip_enabled", "clip": "c", "enabled": False})
    assert report.ok and project.media_assets[0].color_matrix == "bt709" and not project.tracks[0].clips[0].enabled
    assert session.undo()                                                                 # une seule étape pour tout le script
    assert project.media_assets[0].color_matrix == "auto" and project.tracks[0].clips[0].enabled


@pytest.mark.parametrize("op", [
    {"op": "set_media_color_matrix", "asset": "a", "matrix": "bt2020"},
    {"op": "set_media_color_matrix", "asset": "a", "matrix": "BT709"},
    {"op": "set_media_color_matrix", "asset": "a", "matrix": 709},
    {"op": "set_media_color_matrix", "asset": "a"},
    {"op": "set_media_color_matrix", "matrix": "bt709"},
    {"op": "set_media_color_matrix", "asset": "a", "clip": "c", "matrix": "bt709"},
    {"op": "set_media_color_matrix", "asset": "nope", "matrix": "bt709"},
    {"op": "set_media_color_matrix", "clip": "nope", "matrix": "bt709"},
])
def test_the_verb_refuses_a_bad_script_and_leaves_the_project_untouched(op):
    from core.scripting import ScriptInvalid

    project = _project()
    try:
        session, report = _run(project, op)
    except ScriptInvalid:                                                                    # refus à la lecture (enum strict, manquant)
        assert project.media_assets[0].color_matrix == "auto"
        return
    assert not report.ok and project.media_assets[0].color_matrix == "auto"


def test_the_verb_refuses_a_media_read_by_a_locked_track_and_a_non_video():
    project = _project(locked=True)
    for op in ({"op": "set_media_color_matrix", "asset": "a", "matrix": "bt709"},
               {"op": "set_media_color_matrix", "clip": "c", "matrix": "bt709"}):
        session, report = _run(project, op)
        assert not report.ok and "verrouillés" in report.error and project.media_assets[0].color_matrix == "auto"
    audio = _project()
    audio.media_assets.append(MediaAsset("s", "/x/s.wav", "s", 1.0, 0, 0, 0.0, "audio", True))
    session, report = _run(audio, {"op": "set_media_color_matrix", "asset": "s", "matrix": "bt709"})
    assert not report.ok and "pas une vidéo" in report.error


def test_the_cli_reaches_the_verb(tmp_path, capsys):
    from core import cli

    save_project(_project(), str(tmp_path / "p.kut"))
    script = tmp_path / "s.json"
    script.write_text(json.dumps(_script({"op": "set_media_color_matrix", "asset": "a", "matrix": "bt709"})), encoding="utf-8")
    assert cli.run(["validate", str(script)]) == 0
    out = tmp_path / "out.kut"
    assert cli.run(["apply", str(script), str(tmp_path / "p.kut"), "-o", str(out)]) == 0
    capsys.readouterr()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["version"] == 19 and payload["project"]["media_assets"][0]["color_matrix"] == "bt709"
    locked = tmp_path / "locked.kut"
    save_project(_project(locked=True), str(locked))
    assert cli.run(["apply", str(script), str(locked), "-o", str(tmp_path / "no.kut")]) != 0
    capsys.readouterr()
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(_script({"op": "set_media_color_matrix", "asset": "a", "matrix": "nope"})), encoding="utf-8")
    assert cli.run(["validate", str(bad)]) != 0


def test_numbers_do_not_leak_through_the_helpers():
    """Garde de forme : les helpers n'avalent pas de NaN comme des dimensions (le projet n'en contient jamais, mais un fichier le peut)."""
    assert cm.default_for_size(math.nan, math.nan) == "bt601" and cm.default_for_size(math.inf, 0) == "bt601"


def test_a_failed_probe_is_remembered_for_a_short_time_then_retried(tmp_path, monkeypatch):
    """Le moniteur appelle la sonde depuis le fil de l'interface : un ffprobe qui expire n'est pas relancé à chaque synchronisation."""
    import subprocess

    from core import media_probe

    path = tmp_path / "v.bin"
    path.write_bytes(b"x")
    calls, now = [], [100.0]

    def run(*args, **kwargs):
        calls.append(kwargs["timeout"])
        raise subprocess.TimeoutExpired("ffprobe", kwargs["timeout"])

    monkeypatch.setattr(media_probe, "supervised_run", run)
    monkeypatch.setattr(media_probe, "find_media_tool", lambda name: "/bin/ffprobe")
    monkeypatch.setattr(media_probe, "_clock", lambda: now[0])
    media_probe._COLOR_LABELS.clear()
    media_probe._COLOR_FAILURES.clear()
    assert [media_probe.probe_color_label(str(path)) for _ in range(5)] == [""] * 5
    assert len(calls) == 1 and calls[0] <= 3                                   # un seul essai, et court
    now[0] += media_probe._COLOR_FAILURE_TTL - 1
    media_probe.probe_color_label(str(path))
    assert len(calls) == 1
    now[0] += 2                                                                # expiré : on réessaie
    media_probe.probe_color_label(str(path))
    assert len(calls) == 2


def test_other_failures_are_remembered_too_and_a_later_success_replaces_them(tmp_path, monkeypatch):
    from core import media_probe

    path = tmp_path / "v.bin"
    path.write_bytes(b"x")
    calls, now = [], [0.0]

    class Done:
        def __init__(self, code, out):
            self.returncode, self.stdout = code, out

    outputs = [Done(1, ""), Done(0, "x" * 70000), Done(0, json.dumps({"streams": [{"color_space": "bt709"}]}))]
    monkeypatch.setattr(media_probe, "supervised_run", lambda *a, **k: calls.append(1) or outputs[len(calls) - 1])
    monkeypatch.setattr(media_probe, "find_media_tool", lambda name: "/bin/ffprobe")
    monkeypatch.setattr(media_probe, "_clock", lambda: now[0])
    media_probe._COLOR_LABELS.clear()
    media_probe._COLOR_FAILURES.clear()
    for expected_calls in (1, 2, 3):
        assert [media_probe.probe_color_label(str(path)) for _ in range(3)][0] == ("bt709" if expected_calls == 3 else "")
        assert len(calls) == expected_calls
        now[0] += media_probe._COLOR_FAILURE_TTL + 1
    assert media_probe.probe_color_label(str(path)) == "bt709" and len(calls) == 3        # succès mémorisé


def test_a_missing_ffprobe_is_not_searched_again_at_every_call(tmp_path, monkeypatch):
    from core import media_probe

    path = tmp_path / "v.bin"
    path.write_bytes(b"x")
    searched = []
    monkeypatch.setattr(media_probe, "find_media_tool", lambda name: searched.append(name) or None)
    media_probe._COLOR_LABELS.clear()
    media_probe._COLOR_FAILURES.clear()
    for _ in range(4):
        assert media_probe.probe_color_label(str(path)) == ""
    assert len(searched) == 1


def test_a_reopened_sd_media_tagged_709_is_read_in_709_by_the_monitor_and_the_export(tmp_path):
    """Régression : l'étiquette n'est pas dans le .kut ; la sonde paresseuse ne couvrait que le HD, donc un 640×480 BT.709 rouvert
    était lu en 601 par le moniteur alors que FFmpeg respectait l'étiquette à l'export."""
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg absent")
    from core.export_engine import ExportEngine
    from core.render_plan import build_render_plan

    path = tmp_path / "sd709.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "smptebars=s=640x480:r=10:d=1", "-vf",
                    "format=yuv444p,setparams=colorspace=bt709:range=tv", "-c:v", "ffv1", str(path)], check=True, timeout=60)
    project = _project(MediaAsset("a", str(path), "a", 1.0, 640, 480, 10.0, "video"))
    project.width, project.height = 640, 480
    saved = tmp_path / "p.kut"
    save_project(project, str(saved))
    reopened = load_project(str(saved))
    asset = reopened.media_assets[0]
    assert asset.color_space == ""                                                    # rien dans le fichier
    assert cm.asset_effective_matrix(asset) == "bt709" and asset.color_space == "bt709"
    graph = ExportEngine._build_filter_complex(build_render_plan(reopened), 640, 480, 10, None)[0]
    assert "setparams=colorspace=bt709,scale" not in graph                           # l'export laisse l'étiquette : FFmpeg lit 709


def test_a_paused_faithful_segment_applies_the_matrix_even_when_the_monitor_is_on_the_cpu():
    """Le moniteur CPU (vidéo Qt) lit le flux selon ses métadonnées en lecture ; en pause, le segment fidèle (graphe de l'export)
    porte le ``setparams`` : même mécanisme que les effets, que le moniteur CPU ne sait pas montrer non plus."""
    from core.filter_graph import build_filter_complex
    from core.render_plan import build_render_plan

    plan = build_render_plan(_project(_asset(color_space="none", color_matrix="bt601")))
    graph = build_filter_complex(plan, 1920, 1080, 25)[0]
    assert "setparams=colorspace=bt470bg,scale=" in graph


@pytest.mark.real_probe_threads
def test_shutdown_stops_background_probes_and_nothing_starts_afterwards(tmp_path, monkeypatch):
    import threading

    from core import media_probe

    path = tmp_path / "v.bin"
    path.write_bytes(b"x")
    media_probe._COLOR_LABELS.clear()
    media_probe._COLOR_FAILURES.clear()
    gate, spawned = threading.Event(), []
    monkeypatch.setattr(media_probe, "find_media_tool", lambda name: "/bin/ffprobe")

    def run(*a, **k):
        spawned.append(1)
        gate.wait(5)
        raise OSError("fin")

    monkeypatch.setattr(media_probe, "supervised_run", run)
    try:
        assert media_probe.warm_color_label(str(path)) is True
        deadline = time.monotonic() + 3
        while not spawned and time.monotonic() < deadline:
            time.sleep(0.01)
        gate.set()
        assert media_probe.shutdown_color_probes(3.0) is True                      # le fil en vol est attendu
        assert not any(t.is_alive() for t in media_probe._THREADS)
        other = tmp_path / "w.bin"
        other.write_bytes(b"y")
        assert media_probe.warm_color_label(str(other)) is False                  # fermé : rien ne démarre
        assert media_probe.probe_color_label(str(other)) == "" and len(spawned) == 1
    finally:
        media_probe.reset_color_probes()
