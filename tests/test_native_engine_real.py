"""Le chargeur contre la VRAIE ``libkutengine.dylib`` du moteur natif (dépôt séparé), quand elle est construite en local.

Indiquer la bibliothèque avec ``KUT_ENGINE_LIB=/chemin/absolu/libkutengine.dylib``. Le plan KFPL d'essai est désigné par ``KUT_ENGINE_PLAN`` (une source nv12). Sans l'une ou l'autre (CI, machine sans le moteur), tout le
fichier est ignoré avec sa raison : le repli est testé par ``test_native_engine_loader.py``. Les tests qui ne demandent pas de GPU n'ouvrent
aucune fenêtre ; ils créent un moteur Metal et une session hors écran avec ce plan.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from core import build_flavor
from core.native_engine import abi, loader
from core.native_engine.engine import NativeEngine
from core.native_engine.errors import (
    InvalidArgumentError,
    InvalidHandleError,
    InvalidPlanError,
    NativeEngineError,
    NotFoundError,
    QueueFullError,
    SeekRequiredError,
)

LIBRARY = os.environ.get(abi.ENV_VAR, "")
PLAN_PATH = os.environ.get("KUT_ENGINE_PLAN", "")
PLAN = Path(PLAN_PATH).read_bytes() if PLAN_PATH and Path(PLAN_PATH).is_file() else b""

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not LIBRARY or not Path(LIBRARY).is_file() or not PLAN,
    reason=f"{abi.ENV_VAR} ne désigne aucune libkutengine.dylib construite, ou KUT_ENGINE_PLAN aucun plan KFPL",
)


@pytest.fixture()
def config_dir(monkeypatch, tmp_path):
    folder = tmp_path / "config"
    folder.mkdir()
    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(folder))
    monkeypatch.setattr(build_flavor, "STORE", False)
    loader.reset()
    yield folder
    loader.reset()


@pytest.fixture()
def library(config_dir):
    result = loader.probe({abi.ENV_VAR: LIBRARY}, frozen=False)
    assert isinstance(result, loader.NativeLibrary), result
    return result


def test_the_real_library_loads_with_the_right_abi_and_the_application_sees_it_available(library, monkeypatch):
    assert library.abi_version == abi.ABI_VERSION == library.cdll.kut_engine_abi_version()
    assert library.version_string
    monkeypatch.setenv(abi.ENV_VAR, LIBRARY)
    loader.reset()
    assert loader.available() is True and loader.unavailable_reason() is None


def test_engine_and_session_lifecycle_through_python(library, config_dir):
    with NativeEngine.open(library) as engine:
        assert engine.device_name()
        session = engine.create_session(PLAN, present=False)
        sources = session.sources()
        assert sources and sources[0].kind == abi.SOURCE_KIND_VIDEO and sources[0].layout == "nv12"
        assert sources[0].width > 0 and sources[0].height > 0
        frame = session.render_at(0)                               # aucune image poussée : rien n'est rendu, ce n'est pas une erreur
        assert frame.rendered is False and frame.sources_missing >= 1
        stats = session.stats()
        assert stats["ticks"] == 1 and stats["frames_not_ready"] == 1
        session.seek(500_000)
        assert session.position() == (500_000, False)
        with pytest.raises(InvalidArgumentError):
            session.submit_pixel_buffer(sources[0].id, 0, 0)       # image nulle : refus local, rien ne part
        session.close()
        session.close()                                            # idempotent
        with pytest.raises(InvalidHandleError):
            session.render_at(0)                                   # objet fermé : refus local, la bibliothèque n'est pas appelée
    engine.close()                                                 # idempotent aussi après le with
    with pytest.raises(InvalidHandleError):
        engine.device_name()
    assert list(config_dir.iterdir()) == []                        # aucun effet de bord sur la configuration


def test_closing_the_engine_closes_its_sessions_first_and_a_session_closed_afterwards_is_a_noop(library):
    engine = NativeEngine.open(library)
    first = engine.create_session(PLAN, present=False)
    second = engine.create_session(PLAN, present=False)
    engine.close()                                                 # sans cela l'ABI refuserait (INVALID_STATE : sessions vivantes)
    assert first.closed and second.closed
    first.close()
    second.close()


def test_errors_of_the_library_become_typed_exceptions_with_a_short_message(library):
    with NativeEngine.open(library) as engine:
        with pytest.raises(InvalidPlanError) as raised:
            engine.create_session(b"\xab" * 64)
        assert raised.value.status == abi.ERR_INVALID_PLAN and raised.value.status_name == "invalid_plan" and len(raised.value.detail) <= 200
        with pytest.raises(InvalidArgumentError):
            engine.create_session(b"")
        with pytest.raises(InvalidArgumentError):
            engine.create_session(b"x" * (abi.MAX_PLAN_BYTES + 1))                 # refusé avant de franchir la frontière
        with pytest.raises(InvalidArgumentError):
            engine.create_session(PLAN, queue_depth=33)
        session = engine.create_session(PLAN, present=False)
        with pytest.raises(NotFoundError):
            session.end_of_stream("nope")
        with pytest.raises(InvalidArgumentError):
            session.end_of_stream("x" * 500)                                       # identifiant démesuré refusé localement
        with pytest.raises(InvalidArgumentError):
            session.end_of_stream("")
        with pytest.raises(InvalidArgumentError):
            session.end_of_stream("a\x00b")
        with pytest.raises(NativeEngineError):
            session.set_mapping(session.sources()[0].id, speed=0.0)
        assert session.poll_seek(session.sources()[0].id) in (None, 0)


def test_a_destroyed_handle_is_refused_by_the_library_itself(library):
    """Le garde local évite d'envoyer un handle détruit ; ce test force quand même l'appel pour prouver que la bibliothèque répond par un code."""
    engine = NativeEngine.open(library)
    handle = engine._handle
    engine.close()
    status = library.cdll.kut_engine_destroy(handle)
    assert status == abi.ERR_INVALID_HANDLE
    assert library.cdll.kut_engine_destroy(0) == abi.ERR_INVALID_HANDLE


def test_the_exported_functions_of_the_real_library_are_the_ones_we_bind(library):
    for name in abi.FUNCTIONS:
        assert getattr(library.cdll, name) is not None


# -- images décodées poussées par l'hôte (CVPixelBuffer créé ici avec ctypes) ---------------------------------------------------


def _core_foundation():
    import ctypes

    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cv = ctypes.CDLL("/System/Library/Frameworks/CoreVideo.framework/CoreVideo")
    cf.CFDictionaryCreate.restype = ctypes.c_void_p
    cf.CFDictionaryCreate.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFGetRetainCount.restype = ctypes.c_long
    cf.CFGetRetainCount.argtypes = [ctypes.c_void_p]
    cv.CVPixelBufferCreate.restype = ctypes.c_int32
    cv.CVPixelBufferCreate.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    return cf, cv


def _nv12_buffer(width: int, height: int):
    """Un CVPixelBuffer NV12 (``420v``) adossé à un IOSurface, compatible Metal : le même genre de tampon que VideoToolbox rend."""
    import ctypes

    cf, cv = _core_foundation()
    empty = cf.CFDictionaryCreate(None, None, None, 0, ctypes.addressof(ctypes.c_char.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")), ctypes.addressof(ctypes.c_char.in_dll(cf, "kCFTypeDictionaryValueCallBacks")))
    keys = (ctypes.c_void_p * 2)(ctypes.c_void_p.in_dll(cv, "kCVPixelBufferIOSurfacePropertiesKey").value, ctypes.c_void_p.in_dll(cv, "kCVPixelBufferMetalCompatibilityKey").value)
    values = (ctypes.c_void_p * 2)(empty, ctypes.c_void_p.in_dll(cf, "kCFBooleanTrue").value)
    attributes = cf.CFDictionaryCreate(None, keys, values, 2, ctypes.addressof(ctypes.c_char.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")), ctypes.addressof(ctypes.c_char.in_dll(cf, "kCFTypeDictionaryValueCallBacks")))
    buffer = ctypes.c_void_p(0)
    status = cv.CVPixelBufferCreate(None, width, height, 0x34323076, attributes, ctypes.byref(buffer))   # '420v'
    cf.CFRelease(attributes)
    cf.CFRelease(empty)
    assert status == 0 and buffer.value, f"CVPixelBufferCreate: {status}"
    return buffer.value, cf


def test_frames_pushed_from_python_are_retained_rendered_and_released(library):
    with NativeEngine.open(library) as engine:
        session = engine.create_session(PLAN, present=False, queue_depth=1)
        source = next(item for item in session.sources() if item.kind == abi.SOURCE_KIND_VIDEO)
        pixels, cf = _nv12_buffer(source.width, source.height)
        try:
            base = cf.CFGetRetainCount(pixels)
            session.submit_pixel_buffer(source.id, 0, pixels)
            assert cf.CFGetRetainCount(pixels) == base + 1                          # la bibliothèque retient
            with pytest.raises(QueueFullError):
                session.submit_pixel_buffer(source.id, 40_000, pixels)              # file de profondeur 1 pleine : pas retenue
            assert cf.CFGetRetainCount(pixels) == base + 1
            for other in session.sources():
                if other.kind == abi.SOURCE_KIND_IMAGE:
                    session.set_static_image(other.id, bytes(other.width * other.height * 4), other.width * 4)
            frame = session.render_at(0)
            assert frame.rendered is True and frame.sources_missing == 0
            session.wait_idle()
            # l'image montrée reste tenue (c'est l'image « périmée » affichée pendant le saut) jusqu'à ce qu'une autre la remplace
            assert cf.CFGetRetainCount(pixels) == base + 1
            session.seek(1_000_000)                                                 # vide la file et demande un saut à l'hôte
            assert cf.CFGetRetainCount(pixels) == base + 1
            with pytest.raises(SeekRequiredError):
                session.submit_pixel_buffer(source.id, 1_000_000, pixels)
            assert session.poll_seek(source.id) == 1_000_000
            assert session.poll_seek(source.id) is None                             # acquitté une fois
            session.submit_pixel_buffer(source.id, 1_000_000, pixels)
            session.close()
            assert cf.CFGetRetainCount(pixels) == base                              # tout est rendu : pas de fuite
        finally:
            cf.CFRelease(pixels)
