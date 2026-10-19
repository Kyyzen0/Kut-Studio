"""L'exécuteur d'export sans interface (``core/export_runner.py``, ADR-0005 étape 1) avec le vrai FFmpeg.

Un export complet, sans ``QProcess`` ni boucle d'événements : réussite (progression qui ne recule pas, fichier lisible), échec
(message de FFmpeg, aucun fichier tronqué), annulation (FFmpeg tué), repli sur le CPU d'un encodeur matériel qui échoue,
images intermédiaires, et le fichier final atomique.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import replace
from pathlib import Path

import pytest

import core.export_engine as engine_module
from core.export_engine import ExportFormat, ExportPreset, ExportRequest
from core.export_runner import ExportStatus, partial_output_path, run_export
from core.flow_cache import FlowCache
from core.hardware_encoding import HardwareEncoder
from core.project_model import Clip, MediaAsset, Project, Track
from core.render_plan import build_render_plan
from core.time_remapping import TimeInterpolation, TimeRemapping
from core.video_encoders import EncoderChoice

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg absent")

W, H, FPS = 64, 36, 30


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> str:
    path = tmp_path_factory.mktemp("runner") / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={FPS}:d=1,format=yuv420p",
         "-c:v", "libx264", "-crf", "20", "-preset", "ultrafast", str(path)], check=True, timeout=60)
    return str(path)


@pytest.fixture(scope="module")
def long_media(tmp_path_factory) -> str:
    """Assez gros et long pour qu'un export dure de quoi être annulé en cours de route."""
    path = tmp_path_factory.mktemp("runner-long") / "long.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=20,format=yuv420p",
         "-c:v", "libx264", "-crf", "30", "-preset", "ultrafast", str(path)], check=True, timeout=120)
    return str(path)


def _project(path: str, *, seconds: float = 1.0, size=(W, H), remapping: TimeRemapping | None = None) -> Project:
    asset = MediaAsset("a", path, "m", seconds, size[0], size[1], float(FPS), "video", has_audio=False)
    clip = Clip("c", "a", "V1", 0.0, 0.0, seconds, time_remapping=remapping or TimeRemapping())
    return Project("p", width=size[0], height=size[1], fps=float(FPS), media_assets=[asset],
                   tracks=[Track("V1", "V1", "video", clips=[clip])])


def _request(project: Project, output: Path, *, size=(W, H), preset_name: str = "medium", **options) -> ExportRequest:
    return ExportRequest(
        render_plan=build_render_plan(project), output_path=str(output), format=ExportFormat.MP4_H264,
        preset=ExportPreset(name="test", resolution=size, crf=30, audio_bitrate="64k"), fps=float(FPS), **options)


def _frames(path: Path) -> int:
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames",
         "-of", "default=nokey=1:noprint_wrappers=1", str(path)], capture_output=True, text=True, timeout=60)
    return int(done.stdout.strip())


def _hidden_partials(folder: Path) -> list[str]:
    return [item.name for item in folder.iterdir() if ".partial" in item.name]


# --- Réussite ---------------------------------------------------------------------------------------------------------


def test_a_successful_export_reports_monotonic_progress_ends_at_100_and_leaves_one_file(media, tmp_path):
    output = tmp_path / "out.mp4"
    values: list[int] = []
    messages: list[str] = []
    encoders: list[EncoderChoice] = []
    result = run_export(_request(_project(media), output), progress=values.append, status=messages.append,
                        on_encoder=encoders.append)
    assert result.ok and result.status is ExportStatus.OK and result.message == ""
    assert output.is_file() and _frames(output) == FPS
    assert values == sorted(values) and values[-1] == 100 and max(values[:-1]) <= 99, "croissante, 100 seulement à la fin"
    assert messages[-1] == "Export terminé"
    assert len(encoders) == 1 and result.encoder == encoders[0] and not result.encoder.is_hardware
    assert result.fallback_reason == "" and result.preparation is None
    assert sorted(item.name for item in tmp_path.iterdir()) == ["out.mp4"], "ni .partial ni autre fichier à côté"


def test_the_callbacks_run_on_the_callers_thread(media, tmp_path):
    caller = threading.current_thread()
    seen: set[threading.Thread] = set()
    run_export(_request(_project(media), tmp_path / "out.mp4"),
               progress=lambda _value: seen.add(threading.current_thread()),
               status=lambda _text: seen.add(threading.current_thread()))
    assert seen == {caller}


def test_a_blended_clip_is_prepared_first_and_the_progress_covers_both_phases(media, tmp_path):
    project = _project(media, remapping=TimeRemapping(speed=0.5, interpolation=TimeInterpolation.BLENDING))
    output = tmp_path / "slow.mp4"
    values: list[int] = []
    messages: list[str] = []
    result = run_export(_request(project, output), progress=values.append, status=messages.append,
                        flow_cache=FlowCache(tmp_path / "flow"))
    assert result.ok, result.message
    assert "Calcul des images intermédiaires..." in messages
    assert result.preparation is not None and result.preparation.synthesized > 0
    assert _frames(output) == 2 * FPS, "demi-vitesse : deux fois plus d'images"
    assert values == sorted(values), "la barre ne recule jamais d'une phase à l'autre"
    assert any(v >= 80 for v in values[:-1]), "les images intermédiaires remplissent d'abord les 80 premiers %"
    assert values[-1] == 100


# --- Échec ------------------------------------------------------------------------------------------------------------


def test_a_media_ffmpeg_cannot_read_fails_with_its_message_and_leaves_no_file(tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_text("ce n'est pas une vidéo", encoding="utf-8")
    output = tmp_path / "out.mp4"
    result = run_export(_request(_project(str(broken)), output))
    assert result.status is ExportStatus.FAILED and not result.ok
    assert result.message.strip(), "le message de FFmpeg est rendu à l'appelant"
    assert not output.exists(), "aucun fichier sous le nom demandé"
    assert _hidden_partials(tmp_path) == [], "ni fichier intermédiaire laissé derrière"


def test_a_previous_file_is_not_overwritten_by_a_failed_export(tmp_path):
    output = tmp_path / "out.mp4"
    output.write_bytes(b"version precedente")
    broken = tmp_path / "broken.mp4"
    broken.write_text("rien", encoding="utf-8")
    assert not run_export(_request(_project(str(broken)), output)).ok
    assert output.read_bytes() == b"version precedente"


def test_requests_that_cannot_succeed_are_refused_without_running_ffmpeg(media, tmp_path):
    empty = Project("p", width=W, height=H, fps=float(FPS), tracks=[Track("V1", "V1", "video")])
    refused = run_export(_request(empty, tmp_path / "out.mp4"))
    assert refused.status is ExportStatus.FAILED and "Aucun média vidéo" in refused.message

    missing_folder = run_export(_request(_project(media), tmp_path / "absent" / "out.mp4"))
    assert missing_folder.status is ExportStatus.FAILED and "dossier de sortie est introuvable" in missing_folder.message


# --- Annulation -------------------------------------------------------------------------------------------------------


def test_cancelling_midway_kills_ffmpeg_and_leaves_nothing(long_media, tmp_path):
    output = tmp_path / "out.mp4"
    state = {"progress": 0}
    result = run_export(
        _request(_project(long_media, seconds=20.0, size=(1280, 720)), output, size=(1280, 720)),
        progress=lambda value: state.__setitem__("progress", value),
        cancelled=lambda: state["progress"] >= 1,
    )
    assert result.status is ExportStatus.CANCELLED
    assert 1 <= state["progress"] < 100, "annulé en cours de route, pas à la fin"
    assert not output.exists() and _hidden_partials(tmp_path) == []


def test_cancelling_before_the_preparation_ends_stops_it(media, tmp_path):
    project = _project(media, remapping=TimeRemapping(speed=0.5, interpolation=TimeInterpolation.BLENDING))
    result = run_export(_request(project, tmp_path / "out.mp4"), cancelled=lambda: True,
                        flow_cache=FlowCache(tmp_path / "flow"))
    assert result.status is ExportStatus.CANCELLED
    assert not (tmp_path / "out.mp4").exists() and _hidden_partials(tmp_path) == []


# --- Repli sur le CPU -------------------------------------------------------------------------------------------------


def _bogus_hardware(monkeypatch, requested: HardwareEncoder) -> None:
    """Un encodeur « matériel » qui n'existe pas : FFmpeg échoue au lancement, avant la première image."""
    real = engine_module.resolve_video_encoder

    def fake(codec, **kwargs):
        if str(getattr(kwargs.get("hardware"), "value", kwargs.get("hardware"))) == requested.value:
            return EncoderChoice(requested=requested, used=HardwareEncoder.VIDEOTOOLBOX, encoder="h264_inexistant",
                                 args=("-c:v", "h264_inexistant"), codec="h264")
        return real(codec, **kwargs)

    monkeypatch.setattr(engine_module, "resolve_video_encoder", fake)


def test_auto_falls_back_to_the_cpu_when_the_hardware_encoder_fails_to_start(media, tmp_path, monkeypatch):
    _bogus_hardware(monkeypatch, HardwareEncoder.AUTO)
    output = tmp_path / "out.mp4"
    messages: list[str] = []
    encoders: list[EncoderChoice] = []
    result = run_export(_request(_project(media), output, hardware="auto"), status=messages.append,
                        on_encoder=encoders.append)
    assert result.ok, result.message
    assert result.fallback_reason and "rendu CPU utilisé" in result.fallback_reason
    assert result.fallback_reason in messages, "la raison du repli est annoncée"
    assert result.encoder is not None and not result.encoder.is_hardware and result.encoder.fallback_reason
    assert [e.is_hardware for e in encoders] == [True, False], "un lancement matériel, puis le CPU"
    assert output.is_file() and _frames(output) == FPS
    assert result.diagnostics, "la fin de la sortie d'erreur est gardée pour le support"


def test_an_explicit_hardware_encoder_that_fails_is_reported_not_hidden(media, tmp_path, monkeypatch):
    _bogus_hardware(monkeypatch, HardwareEncoder.VIDEOTOOLBOX)
    output = tmp_path / "out.mp4"
    encoders: list[EncoderChoice] = []
    result = run_export(_request(_project(media), output, hardware="videotoolbox"), on_encoder=encoders.append)
    assert result.status is ExportStatus.FAILED and result.fallback_reason == ""
    assert result.error_kind == "encoder" and result.diagnostics
    assert len(encoders) == 1, "aucun repli silencieux"
    assert not output.exists()


# --- Fichier atomique -------------------------------------------------------------------------------------------------


def test_the_partial_file_lives_next_to_the_output_with_the_same_extension(tmp_path):
    partial = Path(partial_output_path(str(tmp_path / "film.final.mp4")))
    assert partial.parent == tmp_path and partial.suffix == ".mp4" and partial.name.startswith(".film.final.")
    assert ".partial" in partial.name
    assert partial_output_path(str(tmp_path / "a.mp4")) != partial_output_path(str(tmp_path / "a.mp4")), "unique par export"


def test_the_final_file_only_appears_when_the_export_is_complete(media, tmp_path):
    """Pendant le rendu seul le fichier caché existe ; le nom demandé n'apparaît qu'une fois tout écrit."""
    output = tmp_path / "out.mp4"
    observed: list[tuple[bool, bool]] = []

    def watch(_value: int) -> None:
        observed.append((output.exists(), bool(_hidden_partials(tmp_path))))

    result = run_export(_request(_project(media), output), progress=watch)
    assert result.ok
    assert all(not final for final, _partial in observed[:-1]), "jamais de fichier final avant la fin"
    assert any(partial for _final, partial in observed), "le fichier intermédiaire a bien existé pendant le rendu"
    assert output.is_file() and _hidden_partials(tmp_path) == []


def test_non_atomic_mode_writes_the_requested_file_directly(media, tmp_path):
    output = tmp_path / "out.mp4"
    seen_final: list[bool] = []
    result = run_export(_request(_project(media), output), atomic=False,
                        progress=lambda _value: seen_final.append(output.exists()))
    assert result.ok and output.is_file()
    assert any(seen_final), "le fichier demandé existe dès le rendu"


def test_the_request_object_is_never_modified(media, tmp_path):
    request = _request(_project(media), tmp_path / "out.mp4")
    before = replace(request)
    run_export(request)
    assert request == before and request.output_path == str(tmp_path / "out.mp4")


# --- Chemin avec « ~ » (revue Codex de la PR 121) ---------------------------------------------------------------------


def _fake_home(tmp_path, monkeypatch):
    """Faux dossier personnel **vide**, tous les emplacements « utilisateur » (journal, données, cache, réglages) ailleurs.

    Le gardien de la supervision des processus (``main.py --kut-process-reaper``) est lancé au premier enfant FFmpeg du
    processus, avec l'environnement du moment, et crée aussitôt le dossier du journal : sous Linux (XDG)
    ``$HOME/.local/state/kut-studio``. Le test qui lance ce premier enfant (selon la répartition xdist) y trouvait un
    ``.local`` dans son faux dossier personnel."""
    home = tmp_path / "home"
    home.mkdir()
    elsewhere = tmp_path / "xdg"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))            # Windows lit celui-ci
    for name, folder in (("XDG_STATE_HOME", "state"), ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"),
                         ("XDG_CONFIG_HOME", "config")):
        monkeypatch.setenv(name, str(elsewhere / folder))
    monkeypatch.setenv("KUT_STUDIO_LOG_DIR", str(elsewhere / "logs"))
    return home


@pytest.mark.parametrize("atomic", [True, False])
def test_a_tilde_output_path_is_expanded_once_and_the_finished_file_lands_there(media, tmp_path, monkeypatch, atomic):
    """FFmpeg écrivait le fichier intermédiaire dans le dossier personnel, mais le renommage visait un « ~ » littéral et le rendu
    terminé était supprimé : la destination doit être la même pour l'écriture, le renommage et le résultat."""
    home = _fake_home(tmp_path, monkeypatch)
    result = run_export(_request(_project(media), Path("~/film.mp4")), atomic=atomic)
    assert result.ok, result.message
    assert (home / "film.mp4").is_file() and _frames(home / "film.mp4") == FPS
    assert result.output_path == str(home / "film.mp4"), "le résultat nomme le fichier réel, pas le « ~ »"
    assert _hidden_partials(home) == []
    assert not Path("~").exists() and not (Path.cwd() / "~").exists(), "aucun dossier « ~ » littéral créé"


def test_a_failed_export_to_a_tilde_path_reports_the_expanded_path_and_leaves_nothing(tmp_path, monkeypatch):
    home = _fake_home(tmp_path, monkeypatch)
    broken = tmp_path / "broken.mp4"
    broken.write_text("rien", encoding="utf-8")
    result = run_export(_request(_project(str(broken)), Path("~/film.mp4")))
    assert result.status is ExportStatus.FAILED and result.output_path == str(home / "film.mp4")
    assert list(home.iterdir()) == []


def test_an_output_path_whose_user_directory_cannot_be_expanded_is_a_failed_result_not_an_exception(media, tmp_path, monkeypatch):
    """Revue Codex de la PR 123 : ``expanduser`` lève ``RuntimeError`` pour ``~utilisateur_inconnu`` (POSIX) ou sans dossier
    personnel ; ``run_export`` rend ses échecs dans le résultat, jamais en exception."""
    from pathlib import Path as _Path

    def cannot_expand(self):
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(_Path, "expanduser", cannot_expand)
    for atomic in (True, False):
        result = run_export(_request(_project(media), tmp_path / "film.mp4"), atomic=atomic)
        assert result.status is ExportStatus.FAILED and "home directory" in result.message
        assert result.output_path == str(tmp_path / "film.mp4"), "le chemin demandé, tel quel"
    assert list(tmp_path.glob("*.mp4")) == [] and _hidden_partials(tmp_path) == []


def test_a_temporary_file_still_held_for_a_moment_is_removed_once_released(tmp_path, monkeypatch):
    """Windows : FFmpeg tué garde sa poignée un instant ; la suppression doit réessayer au lieu de laisser le ``.partial``."""
    from core import export_runner

    target = tmp_path / ".out.partial.mp4"
    target.write_bytes(b"x")
    real_remove = os.remove
    refusals = {"left": 3}

    def locked_then_free(path):
        if refusals["left"] > 0:
            refusals["left"] -= 1
            raise PermissionError("utilisé par un autre processus")
        real_remove(path)

    monkeypatch.setattr(export_runner.os, "remove", locked_then_free)
    monkeypatch.setattr(export_runner, "_REMOVE_PAUSE", 0.0)
    export_runner._remove(str(target))
    assert not target.exists() and refusals["left"] == 0


def test_a_temporary_file_that_stays_locked_is_given_up_with_a_log_not_an_exception(tmp_path, monkeypatch, caplog):
    from core import export_runner

    def always_locked(_path):
        raise PermissionError("verrouillé")

    monkeypatch.setattr(export_runner.os, "remove", always_locked)
    monkeypatch.setattr(export_runner, "_REMOVE_PAUSE", 0.0)
    with caplog.at_level("WARNING", logger="kut_studio.export_runner"):
        export_runner._remove(str(tmp_path / "x"))
    assert "non supprimé" in caplog.text


def test_cancelling_kills_ffmpeg_with_its_descendants(long_media, tmp_path, monkeypatch):
    """Windows : le FFmpeg de Chocolatey est un shim qui lance le vrai FFmpeg ; tuer le seul shim laissait celui-ci écrire (et
    verrouiller) le ``.partial``. L'annulation doit passer par l'arrêt de l'arbre, pas par ``Popen.kill``."""
    from core import export_runner

    calls: list[int] = []
    real = export_runner.kill_process_tree

    def recording(process, **options):
        calls.append(process.pid)
        real(process, **options)

    monkeypatch.setattr(export_runner, "kill_process_tree", recording)
    state = {"progress": 0}
    result = run_export(
        _request(_project(long_media, seconds=20.0, size=(1280, 720)), tmp_path / "out.mp4", size=(1280, 720)),
        progress=lambda value: state.__setitem__("progress", value),
        cancelled=lambda: state["progress"] >= 1,
    )
    assert result.status is ExportStatus.CANCELLED and len(calls) == 1 and calls[0] > 0
    assert _hidden_partials(tmp_path) == []


def test_a_temporary_file_is_retried_for_about_ten_seconds_without_a_real_pause(tmp_path, monkeypatch, caplog):
    from core import export_runner

    pauses: list[float] = []

    def always_locked(_path):
        raise PermissionError(13, "utilisé par un autre processus")

    monkeypatch.setattr(export_runner.os, "remove", always_locked)
    with caplog.at_level("WARNING", logger="kut_studio.export_runner"):
        export_runner._remove(str(tmp_path / "x"), ffmpeg_pid=4321, sleep=pauses.append)
    assert 9.0 <= sum(pauses) <= 11.0, "le budget d'attente est d'environ 10 s"
    assert "pid 4321" in caplog.text and "errno 13" in caplog.text and "winerror" in caplog.text


def test_the_give_up_warning_names_the_windows_error_when_there_is_one(tmp_path, monkeypatch, caplog):
    from core import export_runner

    error = PermissionError(13, "utilisé par un autre processus")
    error.winerror = 32  # type: ignore[attr-defined]  # attribut propre à Windows : simulé ici

    def always_locked(_path):
        raise error

    monkeypatch.setattr(export_runner.os, "remove", always_locked)
    with caplog.at_level("WARNING", logger="kut_studio.export_runner"):
        export_runner._remove(str(tmp_path / "x"), sleep=lambda _s: None)
    assert "winerror 32" in caplog.text and "pid inconnu" in caplog.text


class _FakeTime:
    """Horloge simulée : ``sleep`` fait avancer ``clock`` ; aucune pause réelle."""

    def __init__(self) -> None:
        self.now = 0.0
        self.pauses: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.pauses.append(seconds)
        self.now += seconds


def _locked(monkeypatch, released_after: dict[str, int] | None = None) -> dict[str, int]:
    """``os.remove`` simulé : chemins verrouillés (ou libérés après N essais) ; renvoie le décompte des essais."""
    from core import export_runner

    attempts: dict[str, int] = {}
    free_after = released_after or {}

    def fake_remove(path):
        attempts[path] = attempts.get(path, 0) + 1
        if path in free_after and attempts[path] > free_after[path]:
            return
        raise PermissionError(13, "verrouillé")

    monkeypatch.setattr(export_runner.os, "remove", fake_remove)
    return attempts


def test_one_shared_budget_covers_every_locked_file_with_one_warning_each(monkeypatch, caplog):
    from core import export_runner

    fake = _FakeTime()
    attempts = _locked(monkeypatch)
    paths = ["p.partial", "a.ass", "b.ffconcat"]
    monkeypatch.setattr(export_runner, "_REMOVE_ATTEMPTS", 10_000)     # seule l'échéance borne l'attente
    with caplog.at_level("WARNING", logger="kut_studio.export_runner"):
        export_runner._remove_all(paths, ffmpeg_pid=7, sleep=fake.sleep, clock=fake.clock)
    budget, pause = export_runner._REMOVE_BUDGET_SECONDS, export_runner._REMOVE_PAUSE
    assert budget - pause <= sum(fake.pauses) <= budget + 2 * pause
    assert all(count >= 2 for count in attempts.values()), "chaque chemin est retenté à chaque passe"
    assert len(caplog.records) == 3 and all("non supprimé" in r.getMessage() for r in caplog.records)
    for path in paths:
        assert path in caplog.text


def test_a_file_released_on_the_third_pass_is_removed_and_the_others_go_on(monkeypatch, caplog):
    from core import export_runner

    fake = _FakeTime()
    attempts = _locked(monkeypatch, {"b": 2})
    with caplog.at_level("WARNING", logger="kut_studio.export_runner"):
        export_runner._remove_all(["a", "b"], sleep=fake.sleep, clock=fake.clock)
    assert attempts["b"] == 3, "libéré à la troisième passe, plus retenté ensuite"
    assert attempts["a"] > 3
    assert caplog.text.count("non supprimé") == 1 and "(a)" in caplog.text


def test_with_no_budget_every_path_is_still_tried_once(monkeypatch):
    from core import export_runner

    fake = _FakeTime()
    attempts = _locked(monkeypatch)
    monkeypatch.setattr(export_runner, "_REMOVE_BUDGET_SECONDS", 0.0)
    export_runner._remove_all(["a", "b", "c"], sleep=fake.sleep, clock=fake.clock)
    assert attempts == {"a": 1, "b": 1, "c": 1} and fake.pauses == []


def test_the_partial_file_is_tried_on_the_first_pass_even_if_a_temporary_resists(monkeypatch):
    from core import export_runner

    fake = _FakeTime()
    order: list[str] = []

    def tracing(path):
        order.append(path)
        raise PermissionError(13, "verrouillé")

    monkeypatch.setattr(export_runner.os, "remove", tracing)
    monkeypatch.setattr(export_runner, "_REMOVE_BUDGET_SECONDS", 0.0)
    export_runner._remove_all(["x.partial", "script.ass"], sleep=fake.sleep, clock=fake.clock)
    assert order == ["x.partial", "script.ass"]
