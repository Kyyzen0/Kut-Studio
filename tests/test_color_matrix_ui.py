"""Matrice couleur d'un média, côté interface et chemins annexes : menu de la bibliothèque, historique, verrous, moniteur GPU,
sous-pistes, flux multicam, pipette, décodage d'une image.

La règle est dans ``test_color_matrix.py`` ; la parité des images (moniteur = export = miniature) dans
``test_color_matrix_parity.py``.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMenu

from core.project_model import Clip, MediaAsset, Project, Track
from test_gpu_preview_ui import fake_gpu  # noqa: F401 - fixture partagée
from ui import i18n

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg absent",
)


@pytest.fixture(autouse=True)
def _french(monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_LANG", "fr")
    i18n.reset_for_tests()
    yield
    i18n.reset_for_tests()


def _asset(**extra) -> MediaAsset:
    return MediaAsset(
        "a",
        "/x/a.mp4",
        "a",
        4.0,
        1920,
        1080,
        25.0,
        "video",
        color_space="none",
        **extra,
    )


# --- Menu de la bibliothèque ----------------------------------------------------------------------------------------------


def _builder(**extra):
    from ui.library_organization_widgets import AssetContextMenuBuilder

    return AssetContextMenuBuilder(
        asset_id="a",
        asset_name="a",
        is_missing=False,
        usage_count=1,
        folders=[],
        tags=[],
        assigned_folder_id=None,
        assigned_tag_ids=set(),
        **extra,
    )


def _color_menu(menu: QMenu) -> QMenu | None:
    for action in menu.actions():
        if action.menu() is not None and action.text() == i18n.translate(
            "library.menu.color_matrix"
        ):
            return action.menu()
    return None


def test_the_menu_of_a_video_offers_auto_bt601_bt709_with_the_current_choice_checked(
    qtbot,
):
    builder = _builder(color_matrix="bt601")
    qtbot.addWidget(builder)
    menu = builder.build(builder)
    sub = _color_menu(menu)
    assert sub is not None
    assert [a.text() for a in sub.actions()] == ["Auto", "BT.601", "BT.709"]
    assert [a.isChecked() for a in sub.actions()] == [False, True, False] and all(
        a.isEnabled() for a in sub.actions()
    )
    with qtbot.waitSignal(builder.color_matrix_requested) as signal:
        sub.actions()[2].trigger()
    assert signal.args == ["a", "bt709"]


def test_the_menu_has_no_color_entry_for_a_media_that_is_not_a_video(qtbot):
    builder = _builder()
    qtbot.addWidget(builder)
    menu = builder.build(builder)
    assert _color_menu(menu) is None


def test_the_entries_are_disabled_with_the_reason_when_a_locked_clip_reads_the_media(
    qtbot,
):
    builder = _builder(
        color_matrix="auto",
        color_refusal="Le média est lu par des clips verrouillés ('c').",
    )
    qtbot.addWidget(builder)
    menu = builder.build(builder)
    sub = _color_menu(menu)
    assert sub is not None and not any(a.isEnabled() for a in sub.actions())
    assert all("verrouillés" in a.toolTip() for a in sub.actions())


def test_the_labels_exist_in_the_three_languages():
    for key in (
        "library.menu.color_matrix",
        "library.color_matrix.auto",
        "history.media.color_matrix",
    ):
        for language in ("fr", "en", "es"):
            assert i18n.translate_strict(key, language)
    assert (
        len(i18n.translate_strict("library.menu.color_matrix", "en")) <= 24
    )  # court : police à +20 %


# --- Fenêtre : réglage, historique, verrous --------------------------------------------------------------------------------


def _window_with_clip(qtbot, monkeypatch, *, locked=False, media_type="video"):
    from test_scopes import _window

    window = _window(qtbot, monkeypatch)
    window.project.media_assets[:] = []
    for track in list(window.project.tracks):
        track.clips = []
    if media_type == "video":
        window.project.media_assets.append(_asset())
    else:
        window.project.media_assets.append(
            MediaAsset("a", "/x/a.wav", "a", 4.0, 0, 0, 0.0, "audio", True)
        )
    video = next((t for t in window.project.tracks if t.type == "video"), None)
    if video is None:
        video = Track("V1", "V1", "video")
        window.project.tracks.append(video)
    video.clips.append(Clip("c", "a", video.id, 0.0, 0.0, 2.0))
    video.locked = locked
    return window


def test_choosing_a_color_space_is_one_undoable_history_step(qtbot, monkeypatch):
    window = _window_with_clip(qtbot, monkeypatch)
    window._record_history("Départ")
    window._on_asset_color_matrix_requested("a", "bt709")
    assert window.project.media_assets[0].color_matrix == "bt709"
    window.undo_last()
    assert window.project.media_assets[0].color_matrix == "auto"


def test_choosing_the_same_value_records_nothing(qtbot, monkeypatch):
    window = _window_with_clip(qtbot, monkeypatch)
    recorded = []
    monkeypatch.setattr(window, "_record_history", lambda label: recorded.append(label))
    window._on_asset_color_matrix_requested("a", "auto")
    assert recorded == []
    window._on_asset_color_matrix_requested("a", "bt601")
    assert recorded == [i18n.translate("history.media.color_matrix")]


@pytest.mark.parametrize(
    "scenario", ["locked_track", "audio", "unknown_value", "unknown_media"]
)
def test_a_refused_choice_changes_nothing_records_nothing_and_says_why(
    qtbot, monkeypatch, scenario
):
    window = _window_with_clip(
        qtbot,
        monkeypatch,
        locked=scenario == "locked_track",
        media_type="audio" if scenario == "audio" else "video",
    )
    warnings, recorded = [], []
    monkeypatch.setattr(
        "ui.main_window_mixins.media_color.QMessageBox.warning",
        lambda *a, **k: warnings.append(a[2]),
    )
    monkeypatch.setattr(window, "_record_history", lambda label: recorded.append(label))
    asset_id = "missing" if scenario == "unknown_media" else "a"
    value = "bt2020" if scenario == "unknown_value" else "bt709"
    window._on_asset_color_matrix_requested(asset_id, value)
    assert (
        recorded == []
        and len(warnings) == 1
        and window.project.media_assets[0].color_matrix == "auto"
    )
    assert len(warnings[0]) < 300


def test_the_monitor_gets_the_matrix_of_the_original_media_not_of_its_proxy(
    qtbot, monkeypatch
):
    window = _window_with_clip(qtbot, monkeypatch)
    clip = window.project.tracks[0].clips[0] if window.project.tracks[0].clips else None
    clip = next(c for t in window.project.tracks for c in t.clips)
    top = SimpleNamespace(rendered=False)
    assert window._monitor_color_matrix(top, clip) == (
        "bt709",
        "auto",
    )  # 1920×1080 non étiqueté
    window.project.media_assets[0].color_matrix = "bt601"
    assert window._monitor_color_matrix(top, clip) == ("bt601", "bt601")
    assert window._monitor_color_matrix(SimpleNamespace(rendered=True), clip) == (
        "bt709",
        "bt709",
    )  # morceau déjà rendu : toujours BT.709
    assert window._monitor_color_matrix(top, SimpleNamespace(asset_id="nope")) == (
        "",
        "auto",
    )


# --- Moniteur GPU ---------------------------------------------------------------------------------------------------------


def test_the_panel_hands_the_matrix_to_the_gpu_view_even_when_it_is_created_later(
    qtbot, fake_gpu
):
    from test_gpu_preview_ui import _panel

    panel = _panel(qtbot)
    panel.set_source_color("bt709", "bt709")  # avant la création du moniteur
    assert panel.enable_gpu("metal")
    view = fake_gpu.instances[0]
    assert view.colors == {"main": ("bt709", "bt709")}
    panel.set_source_color("bt601", "auto")
    assert view.colors["main"] == ("bt601", "auto")
    panel.set_source_color("")  # inconnu : la source décodée décide
    assert view.colors["main"] == ("", "auto")


def test_a_library_preview_forgets_the_matrix_of_the_last_timeline_clip(
    qtbot, fake_gpu, monkeypatch
):
    from test_gpu_preview_ui import _panel

    panel = _panel(qtbot)
    # Le média fictif ne doit pas être ouvert : un vrai lecteur sur un chemin absent journalise « Could not open media » depuis
    # un fil de Qt Multimedia, ce qui fait mourir un worker xdist (segfault observé sous macOS ; voir multicam_stubs).
    monkeypatch.setattr(panel.player, "setSource", lambda *_a, **_k: None)
    monkeypatch.setattr(panel.player, "play", lambda *_a, **_k: None)
    panel.enable_gpu("metal")
    panel.set_source_color("bt709", "auto")
    panel.load_video("/x/other.mp4")
    assert panel._gpu_main_color == ("", "auto")


def test_the_real_view_overrides_only_what_the_panel_knows_and_resends_the_displayed_image():
    from core.gpu_composite import VideoSource
    from ui.gpu_preview import GpuPreviewWidget, PendingFrame

    fake = SimpleNamespace(
        _color_overrides={},
        _latest={"main": PendingFrame(object(), 1)},
        _pending={},
        update=lambda: None,
    )
    source = VideoSource("main", "yuv420p", 1920, 1080, "", "video")
    assert (
        GpuPreviewWidget._colored(fake, "main", source) is source
    )  # pas d'entrée : l'image décodée décide
    GpuPreviewWidget.set_source_color(fake, "main", "bt709", "auto")
    colored = GpuPreviewWidget._colored(fake, "main", source)
    assert (colored.colorspace, colored.color_choice) == (
        "bt709",
        "auto",
    ) and "main" in fake._pending
    fake._pending.clear()
    GpuPreviewWidget.set_source_color(
        fake, "main", "bt709", "auto"
    )  # inchangé : pas de renvoi
    assert fake._pending == {}
    grain = VideoSource("g", "grain", 8, 8, "7", "3")
    assert GpuPreviewWidget._colored(fake, "g", grain) is grain
    GpuPreviewWidget.set_source_color(fake, "main", "", "auto")
    assert fake._color_overrides == {} and "main" in fake._pending


def test_the_underlay_decode_key_and_job_carry_the_matrix(qtbot, monkeypatch):
    from ui import preview_panel

    panel = preview_panel.PreviewPanel(
        lambda: None, lambda: None, lambda _d: None, lambda: None, lambda: None
    )
    qtbot.addWidget(panel)
    panel.gpu_view = SimpleNamespace(
        forget_source=lambda *_: None,
        set_video_frame=lambda *_: None,
        set_composite=lambda *_a, **_k: None,
    )
    calls = []
    done = threading.Event()

    def decode(path, seconds, color_override=""):
        calls.append((path, seconds, color_override))
        done.set()

    monkeypatch.setattr(preview_panel, "_decode_underlay_frame", decode)
    entry = [
        "clip:a",
        "/x/a.mp4",
        0.5,
        (1, 0, 0, 1, 0, 0),
        (0, 0, 10, 10),
        1.0,
        "normal",
        (),
        (1.0, 1.0),
        None,
        None,
        {},
        0.0,
    ]
    panel.set_video_underlays((tuple(entry + ["bt709"]),))
    assert done.wait(2.0) and calls == [("/x/a.mp4", 0.5, "bt709")]
    assert panel._gpu_underlay_wanted["clip:a"].endswith("|bt709")
    time.sleep(0.05)
    done.clear()
    panel.set_video_underlays(
        (tuple(entry + ["bt470bg"]),)
    )  # autre matrice : autre clé, nouveau décodage
    assert done.wait(2.0) and calls[-1][2] == "bt470bg"


# --- Décodage d'une image, pipette, flux multicam ---------------------------------------------------------------------------


def _solid(tmp_path, size=(64, 36)):
    path = tmp_path / "solid.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0xDD5C1D:s={size[0]}x{size[1]}:r=10:d=1,format=rgb24",
            "-vf",
            "scale=out_color_matrix=bt709:out_range=tv,format=yuv444p,setparams=colorspace=unknown:range=unknown",
            "-c:v",
            "ffv1",
            str(path),
        ],
        check=True,
        timeout=60,
    )
    return str(path)


@needs_ffmpeg
def test_decoding_one_image_with_a_matrix_goes_through_the_command_line_and_changes_the_colours(
    tmp_path,
):
    from core.media_io import open_media

    path = _solid(tmp_path)
    with open_media(path) as handle:
        default = handle.decode_at(0.0)
        as_709 = handle.decode_at(0.0, "bt709")
        as_601 = handle.decode_at(0.0, "bt470bg")
        ignored = handle.decode_at(
            0.0, "bt709:range=pc"
        )  # un texte inconnu n'est jamais passé à FFmpeg
    centre = lambda frame: tuple(
        frame.rgba[((frame.height // 2) * frame.width + frame.width // 2) * 4 :][:3]
    )  # noqa: E731
    assert centre(as_709) != centre(default) and centre(as_601) == centre(default)
    assert (
        max(abs(a - b) for a, b in zip(centre(as_709), (221, 92, 29))) <= 2
    )  # codes écrits en 709 : la bonne couleur
    assert ignored.rgba == default.rgba


@needs_ffmpeg
def test_the_eyedropper_reads_the_same_colour_as_the_monitor(tmp_path):
    from core.color_pick import sample_command, sample_node_input

    path = _solid(tmp_path)
    plain = sample_command("ffmpeg", path, 0.0, 10, 10, "")
    told = sample_command("ffmpeg", path, 0.0, 10, 10, "", color_override="bt709")
    assert "setparams" not in plain[plain.index("-vf") + 1]
    assert told[told.index("-vf") + 1].startswith("setparams=colorspace=bt709,crop=")
    default = sample_node_input(path, 0.0, 10, 10, None)
    forced = sample_node_input(path, 0.0, 10, 10, None, color_override="bt709")
    assert (
        default != forced
        and max(abs(a * 255 - b) for a, b in zip(forced, (221, 92, 29))) <= 2
    )


def test_a_multicam_tile_stream_tells_ffmpeg_the_matrix_of_its_media():
    from core.multicam_feed import AngleFeed, FeedPool, TileProfile, _ffmpeg_command

    profile = TileProfile("t", 64, 36, 10.0)
    hd = MediaAsset(
        "a", "/x/a.mp4", "a", 4.0, 1920, 1080, 25.0, "video", color_space="none"
    )
    tagged = MediaAsset(
        "a", "/x/a.mp4", "a", 4.0, 1920, 1080, 25.0, "video", color_space="bt709"
    )
    sd = MediaAsset(
        "a", "/x/a.mp4", "a", 4.0, 640, 360, 25.0, "video", color_space="none"
    )
    ffmpeg_exists = shutil.which("ffmpeg") is not None
    if ffmpeg_exists:

        def vf(asset):
            command = _ffmpeg_command("/x/a.mp4", profile, 0.0, asset)
            return command[command.index("-vf") + 1]

        assert vf(hd).startswith("setparams=colorspace=bt709,fps=")
        assert (
            vf(tagged).startswith("fps=")
            and vf(sd).startswith("fps=")
            and vf(None).startswith("fps=")
        )
        assert vf(
            MediaAsset(
                "a", "/x/a.mp4", "a", 4.0, 640, 360, 25.0, "video", color_matrix="bt709"
            )
        ).startswith("setparams=")
    # Le flux est une clé de plus quand l'utilisateur force : un autre choix, un autre flux.
    assert (
        FeedPool.key("p", profile) == ("p", profile) == FeedPool.key("p", profile, hd)
    )
    assert FeedPool.key(
        "p",
        profile,
        MediaAsset("a", "/x", "a", 1.0, 64, 36, 25.0, "video", color_matrix="bt601"),
    ) == ("p", profile, "bt601")
    pool = FeedPool(lambda path, prof: AngleFeed(path, prof))
    feed = pool.feed("p", profile, hd)
    assert feed.color_asset is hd and pool.feed("p", profile, hd) is feed
    forced = MediaAsset(
        "a", "/x/a.mp4", "a", 4.0, 1920, 1080, 25.0, "video", color_matrix="bt601"
    )
    assert pool.feed("p", profile, forced) is not feed
    pool.close_all()


def test_the_multicam_samples_carry_the_media_for_the_tiles():
    from core.multicam import AngleSample, AngleState

    sample = AngleSample(
        angle=None, index=0, state=AngleState.LIVE, path="/x", color_asset=_asset()
    )  # type: ignore[arg-type]
    assert sample.color_asset is not None and sample == AngleSample(
        angle=None, index=0, state=AngleState.LIVE, path="/x"
    )  # type: ignore[arg-type]


# --- Miniatures de la timeline -----------------------------------------------------------------------------------------------


def test_the_timeline_asks_for_thumbnails_keyed_by_the_matrix_of_each_clip(
    qtbot, monkeypatch
):
    from core.media_previews import thumbnail_cache_key
    from ui.timeline_panel import TimelinePanel

    panel = TimelinePanel()
    qtbot.addWidget(panel)
    project = Project(
        "p",
        1920,
        1080,
        25.0,
        media_assets=[_asset()],
        tracks=[
            Track("V1", "V1", "video", clips=[Clip("c", "a", "V1", 0.0, 0.0, 2.0)])
        ],
    )
    panel.project = project
    clip = project.tracks[0].clips[0]
    asset = panel.asset_of_clip(clip)
    assert asset is project.media_assets[0] and panel.asset_of_clip(None) is None
    forced = thumbnail_cache_key("/x/a.mp4", 1.0, 160, asset)
    plain = thumbnail_cache_key("/x/a.mp4", 1.0, 160)
    assert forced != plain
    asset.color_matrix = "bt601"
    assert thumbnail_cache_key("/x/a.mp4", 1.0, 160, asset) not in (forced, plain)
    project.media_assets.append(
        _asset()
    )  # la table se reconstruit quand la liste change
    project.media_assets[-1].id = "b"
    assert (
        panel.asset_of_clip(Clip("d", "b", "V1", 0.0, 0.0, 1.0))
        is project.media_assets[-1]
    )


def test_the_lut_of_a_displayed_layer_is_baked_with_the_matrix_the_monitor_reads():
    """``_resolve_grade`` demande la LUT avec l'espace réellement lu (étiquette, choix, sinon taille), pas le vide de Qt."""
    from collections import OrderedDict

    from core.color_grading import ColorGrade
    from core.gpu_composite import CompositeLayer, VideoSource
    from ui.gpu_preview import GpuPreviewWidget

    asked = []

    class Grades:
        def lookup(self, grade, *, slot="", **options):
            asked.append(options)
            return None

        def latest(self, slot=""):
            return None

        def failed(self, grade, **options):
            return False

    def resolve(source):
        fake = SimpleNamespace(
            _sources={"main": source},
            grades=Grades(),
            _last_luts={},
            _lut_images=OrderedDict(),
            _poll_grades=lambda: None,
        )
        layer = CompositeLayer(
            source="main",
            matrix=(1.0, 0.0, 0.0, 1.0, 0.0, 0.0),
            fit=(0.0, 0.0, 64.0, 36.0),
            grade=ColorGrade(exposure=0.3),
        )
        GpuPreviewWidget._resolve_grade(fake, layer, {})
        return asked[-1]

    assert resolve(VideoSource("main", "yuv420p", 1920, 1080))["colorspace"] == "bt709"
    assert resolve(VideoSource("main", "yuv420p", 640, 360))["colorspace"] == "bt601"
    assert (
        resolve(VideoSource("main", "yuv420p", 1920, 1080, "bt601"))["colorspace"]
        == "bt601"
    )
    assert (
        resolve(
            VideoSource("main", "yuv420p", 640, 360, "bt601", color_choice="bt709")
        )["colorspace"]
        == "bt709"
    )
    assert resolve(VideoSource("main", "yuv420p", 1920, 1080))["domain"] == "yuv"


def test_the_render_loop_applies_the_color_override_to_every_uploaded_source():
    """Garde d'atteignabilité : ``render`` passe chaque source téléversée par ``_colored`` (le rendu GPU réel n'est pas joué hors fenêtre)."""
    import ast
    import inspect
    import textwrap

    from ui.gpu_preview import GpuPreviewWidget

    tree = ast.parse(textwrap.dedent(inspect.getsource(GpuPreviewWidget.render)))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "_colored" in called and "upload_source" in called


def test_the_faithful_preview_cache_is_invalidated_by_the_new_rule():
    from core.filter_graph import RENDER_ENGINE_VERSION

    assert (
        RENDER_ENGINE_VERSION >= 11
    )  # 10 = segments rendus avec « tout non étiqueté en BT.601 »


def test_the_monitor_sync_never_calls_the_blocking_probe(qtbot, monkeypatch):
    from core import media_probe

    window = _window_with_clip(qtbot, monkeypatch)
    window.project.media_assets[0].color_space = ""
    clip = next(c for t in window.project.tracks for c in t.clips)
    blocking = []
    monkeypatch.setattr(
        media_probe, "probe_color_label", lambda path: blocking.append(path) or ""
    )
    monkeypatch.setattr(media_probe, "warm_color_label", lambda path: True)
    assert window._monitor_color_matrix(SimpleNamespace(rendered=False), clip) == (
        "bt709",
        "auto",
    )
    assert blocking == []


def test_opening_a_project_starts_the_background_label_probe():
    """Garde d'atteignabilité : le chargement d'un projet lance la sonde de fond (la fenêtre réelle n'est pas rejouée ici)."""
    import inspect

    from ui.main_window_mixins.project_files import ProjectFilesMixin

    assert "warm_color_labels" in inspect.getsource(
        ProjectFilesMixin._load_project_from_path
    )
