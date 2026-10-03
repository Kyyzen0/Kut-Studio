"""Le journal de diagnostic reçoit les pannes que l'utilisateur ne voit pas : repli GPU, repli matériel, suivi, cache.

Application empaquetée : pas de console. Chaque panne ci-dessous est **provoquée par l'API publique** du module concerné,
puis on relit le **fichier** de journal (pas un logger de test) : une panne qui n'y arrive pas est invisible pour
quelqu'un qui envoie son journal pour un rapport de bogue. Les pannes FFmpeg (aperçu, proxies, export, file de rendu)
sont dans ``test_ffmpeg_failure_diagnostics.py``.
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

import pytest

from core.diagnostics_log import install_diagnostics, uninstall_diagnostics


@pytest.fixture
def journal(tmp_path, monkeypatch):
    """Journal fichier dans un dossier temporaire ; renvoie une fonction qui relit son contenu."""
    monkeypatch.setattr(sys, "excepthook", lambda *_a: None)
    monkeypatch.setattr(threading, "excepthook", lambda _a: None)
    monkeypatch.setattr(sys, "unraisablehook", lambda _a: None)
    path = install_diagnostics(tmp_path / "logs")
    assert path is not None

    def text() -> str:
        for handler in logging.getLogger("kut_studio").handlers:
            handler.flush()
        return Path(path).read_text(encoding="utf-8") if Path(path).exists() else ""

    yield text
    uninstall_diagnostics()


# --- Replis GPU et matériel ------------------------------------------------------------------------------------


def test_a_gpu_failure_and_the_session_wide_disable_reach_the_log_file(journal):
    from core.gpu_backend import FAILURES_BEFORE_DISABLE, GpuHealth

    health = GpuHealth()
    for _ in range(FAILURES_BEFORE_DISABLE):
        health.record("device_lost", "pilote réinitialisé")

    text = journal()
    assert "Aperçu GPU : device_lost (pilote réinitialisé) — repli sur le CPU" in text
    assert "Aperçu GPU désactivé pour la session" in text


def test_a_hardware_decode_failure_and_the_ban_reach_the_log_file(journal):
    from core.decode_policy import FAILURES_BEFORE_BLOCK, DecodeChoice, DecodeHealth, DecodePurpose
    from core.hardware_decoding import DecodeMode

    health = DecodeHealth()
    choice = DecodeChoice(requested=DecodeMode.VIDEOTOOLBOX, used=DecodeMode.VIDEOTOOLBOX, codec="h264", reason="test")
    for _ in range(FAILURES_BEFORE_BLOCK):
        health.record_failure(choice, DecodePurpose.SEGMENT, "VideoToolbox : erreur de décodeur")

    text = journal()
    assert "repli CPU" in text and "VideoToolbox : erreur de décodeur" in text
    assert "banni pour la session" in text


# --- Suivi (tracking) ----------------------------------------------------------------------------------------


def test_a_failed_tracking_analysis_is_logged_with_its_traceback(journal, monkeypatch):
    import core.tracking_engine as engine
    from core.tracking_engine import TrackingRequest, run_tracking
    from core.tracking_model import Sample, SampleStatus, TrackData, Tracker

    def boom(*_args, **_kwargs):
        raise RuntimeError("corrélation impossible")

    monkeypatch.setattr(engine, "_analyze", boom)
    start = TrackData.from_samples(30.0, {0: Sample(10.0, 10.0, 1.0, SampleStatus.MANUAL)}, source_size=(320, 180))
    request = TrackingRequest(
        clip_id="v", media_path="/absent.mp4", media_size=(320, 180), rate=30.0,
        trackers=(Tracker(id="t1", data=start),), start_index=0, end_index=5,
    )

    result = run_tracking(request)

    assert result.state == "failed"
    text = journal()
    assert "Tracking : l'analyse a échoué" in text and "RuntimeError: corrélation impossible" in text


def test_a_tracking_binding_that_cannot_be_derived_is_logged_not_swallowed(journal, monkeypatch):
    import core.tracking_bindings as bindings
    from core.project_model import Clip, MediaAsset, Project, Track
    from core.tracking_model import ClipTracking, TrackLink

    def boom(*_args, **_kwargs):
        raise ValueError("matrice singulière")

    monkeypatch.setattr(bindings, "_compute_state", boom)
    project = Project(name="P", width=320, height=180, fps=30.0)
    project.media_assets.append(MediaAsset("m", "/absent.mp4", "m", 1.0, 320, 180, 30.0, "video"))
    follower = Clip("f", "m", "V1", 0.0, 0.0, 1.0)
    follower.tracking = ClipTracking(links=(TrackLink(source_clip_id="v", tracker_ids=("t1",)),))
    project.tracks.insert(0, Track("V1", "V1", "video", clips=[follower]))

    state = bindings.effective_clip_state(follower, bindings.TrackingContext(project))

    assert state.warnings and "matrice singulière" in state.warnings[0]            # le rendu continue, sans suivi
    text = journal()
    assert "impossible à dériver" in text and "ValueError: matrice singulière" in text


# --- Caches ---------------------------------------------------------------------------------------------------


def test_a_cache_file_that_cannot_be_deleted_is_logged(journal, tmp_path, monkeypatch):
    from core.preview_cache import DiskPreviewCache

    cache = DiskPreviewCache(tmp_path / "preview")
    victim = cache.directory / "segment.mp4"
    victim.parent.mkdir(parents=True, exist_ok=True)
    victim.write_bytes(b"x")
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: (_ for _ in ()).throw(PermissionError("fichier ouvert")))

    assert cache._unlink("segment.mp4") is False

    assert "impossible à supprimer" in journal() and "fichier ouvert" in journal()


def test_a_corrupt_tracking_cache_entry_is_logged_and_ignored(journal, tmp_path, monkeypatch):
    from core.tracking_engine import TrackingCache

    monkeypatch.setenv("KUT_STUDIO_CACHE_DIR", str(tmp_path / "cache"))
    cache = TrackingCache()
    path = cache._path("abcdef0123456789")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ceci n'est pas du JSON", encoding="utf-8")

    assert cache.load("abcdef0123456789", 30.0) is None
    assert "Cache de tracking" in journal() and "illisible" in journal()

    path.write_text('{"data": 3}', encoding="utf-8")
    assert cache.load("abcdef0123456789", 30.0) is None
    assert "forme inattendue" in journal()


def test_an_unreadable_hardware_cache_is_logged_but_a_missing_one_is_not(journal, tmp_path):
    from core.hardware_cache import CapabilityService

    absent = CapabilityService(cache_path=tmp_path / "absent.json", validate=False)
    assert absent._read_disk() is None
    assert "Cache des capacités" not in journal()                              # premier démarrage : rien d'anormal

    broken = tmp_path / "broken.json"
    broken.write_text('{"schema": 2, "encoders": 5}', encoding="utf-8")
    assert CapabilityService(cache_path=broken, validate=False)._read_disk() is None
    assert "Cache des capacités mal formé" in journal()


def test_no_print_remains_in_core_or_ui():
    """Un ``print`` est invisible dans l'application empaquetée : tout passe par le journal."""
    import ast

    root = Path(__file__).resolve().parent.parent
    offenders = []
    for folder in ("core", "ui"):
        for path in (root / folder).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            offenders += [
                f"{path.relative_to(root)}:{node.lineno}" for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print"
            ]
    assert not offenders, f"print() dans le code de l'application : {offenders}"
