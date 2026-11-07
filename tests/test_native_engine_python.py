"""Couche Python du moteur natif (propriété des sessions, bornes des images) contre une FAUSSE bibliothèque : aucun GPU ni bibliothèque requis."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.native_engine import abi
from core.native_engine.engine import NativeEngine
from core.native_engine.errors import InvalidArgumentError, InvalidStateError, NotFoundError
from core.native_engine.loader import NativeLibrary


class FakeCdll:
    """Imite les fonctions de l'ABI utilisées : les handles sont des entiers, ``byref`` s'écrit par ``_obj``."""

    def __init__(self, image_size=(4, 3)):
        self.sessions: set[int] = set()
        self.engines: set[int] = set()
        self.next_handle = 100
        self.fail_session_destroy = 0         # nombre d'échecs à simuler
        self.static_calls: list[tuple] = []
        self.image_size = image_size
        self.last_error = b""

    def _new(self, out) -> int:
        self.next_handle += 1
        out._obj.value = self.next_handle
        return self.next_handle

    def kut_engine_last_error_message(self, buffer, capacity, needed):
        buffer.value = self.last_error
        return 0

    def kut_engine_create(self, config, out):
        self.engines.add(self._new(out))
        return 0

    def kut_engine_destroy(self, handle):
        if self.sessions:
            self.last_error = b"engine still has live sessions"
            return abi.ERR_INVALID_STATE
        self.engines.discard(handle)
        return 0

    def kut_engine_session_create(self, engine, data, size, config, out):
        self.sessions.add(self._new(out))
        return 0

    def kut_engine_session_destroy(self, handle):
        if self.fail_session_destroy:
            self.fail_session_destroy -= 1
            self.last_error = b"device lost"
            return abi.ERR_DEVICE
        self.sessions.discard(handle)
        return 0

    def kut_engine_session_source_count(self, handle, out):
        out._obj.value = 2
        return 0

    def kut_engine_session_source_info(self, handle, index, out):
        info = out._obj
        info.kind = abi.SOURCE_KIND_VIDEO if index == 0 else abi.SOURCE_KIND_IMAGE
        info.width, info.height = (64, 36) if index == 0 else self.image_size
        info.id = b"video" if index == 0 else b"logo"
        info.layout = b"nv12" if index == 0 else b"rgba"
        return 0

    def kut_engine_session_set_static_image(self, handle, source_id, data, stride):
        self.static_calls.append((source_id, len(data), stride))
        return 0


@pytest.fixture()
def fake():
    cdll = FakeCdll()
    library = NativeLibrary(cdll, Path("/fake/libkutengine.dylib"), "fake")      # type: ignore[arg-type]
    return cdll, library


PLAN = b"plan"


def test_a_session_dropped_without_close_is_still_closed_by_the_engine(fake):
    cdll, library = fake
    engine = NativeEngine.open(library)
    engine.create_session(PLAN)                           # aucune référence gardée : le ramasse-miettes ne doit pas la perdre
    import gc

    gc.collect()
    assert len(cdll.sessions) == 1
    engine.close()                                        # ferme la session puis détruit le moteur : pas d'InvalidStateError
    assert cdll.sessions == set() and cdll.engines == set() and engine.closed


def test_closing_a_session_releases_it_from_the_engine_and_is_idempotent(fake):
    cdll, library = fake
    with NativeEngine.open(library) as engine:
        session = engine.create_session(PLAN)
        session.close()
        session.close()
        assert engine._sessions == set() and cdll.sessions == set()


def test_a_failed_session_close_keeps_everything_retryable(fake):
    cdll, library = fake
    engine = NativeEngine.open(library)
    session = engine.create_session(PLAN)
    cdll.fail_session_destroy = 1
    with pytest.raises(Exception, match="device"):
        engine.close()                                    # la session ne se détruit pas : le moteur reste ouvert
    assert not engine.closed and not session.closed and len(cdll.sessions) == 1
    engine.close()                                        # deuxième essai : tout se ferme
    assert engine.closed and session.closed and cdll.sessions == set() and cdll.engines == set()


def test_a_failed_engine_destroy_is_retryable(fake):
    cdll, library = fake
    engine = NativeEngine.open(library)
    cdll.sessions.add(999)                                # une session native que Python ne connaît pas : l'ABI refuse
    with pytest.raises(InvalidStateError):
        engine.close()
    assert not engine.closed
    cdll.sessions.clear()
    engine.close()
    assert engine.closed


# -- images fixes : bornes vérifiées avant l'appel natif ---------------------------------------------------------------------------


def _session(fake, size=(4, 3)):
    cdll, library = fake
    cdll.image_size = size
    return cdll, NativeEngine.open(library).create_session(PLAN)


def test_a_valid_static_image_reaches_the_library(fake):
    cdll, session = _session(fake)
    session.set_static_image("logo", bytes(4 * 4 * 3), 16)
    session.set_static_image("logo", bytes(20 * 3 + 7), 20)             # pas plus large que la ligne, octets en trop acceptés
    assert cdll.static_calls == [(b"logo", 48, 16), (b"logo", 60, 20)]


@pytest.mark.parametrize(
    ("data", "stride"),
    [(b"x", 16), (bytes(47), 16), (bytes(16 * 3 - 1), 16), (b"", 16)],
    ids=["un octet", "un octet de moins", "dernière ligne tronquée", "vide"],
)
def test_a_buffer_shorter_than_stride_times_height_is_refused_before_the_native_call(fake, data, stride):
    cdll, session = _session(fake)
    with pytest.raises(InvalidArgumentError, match="trop courte"):
        session.set_static_image("logo", data, stride)
    assert cdll.static_calls == []


@pytest.mark.parametrize("stride", [15, 0, -16, 1 << 40, True], ids=["trop petit", "zéro", "négatif", "démesuré", "booléen"])
def test_an_invalid_stride_is_refused_before_the_native_call(fake, stride):
    cdll, session = _session(fake)
    with pytest.raises(InvalidArgumentError):
        session.set_static_image("logo", bytes(1024), stride)
    assert cdll.static_calls == []


def test_huge_dimensions_are_bounded_without_allocating(fake):
    cdll, session = _session(fake, size=(16384, 16384))
    with pytest.raises(InvalidArgumentError, match="trop grande"):
        session.set_static_image("logo", bytes(16), 16384 * 4 + 4)     # plus de 1 Gio : refusé avant toute copie
    with pytest.raises(InvalidArgumentError, match="trop courte"):
        session.set_static_image("logo", bytes(16), 16384 * 4)         # exactement 1 Gio (la limite) : la longueur est alors seule en cause
    with pytest.raises(InvalidArgumentError):
        session.set_static_image("logo", bytes(16), 1 << 29)
    assert cdll.static_calls == []


def test_unknown_or_video_sources_are_refused_locally(fake):
    cdll, session = _session(fake)
    with pytest.raises(NotFoundError):
        session.set_static_image("nope", bytes(100), 16)
    with pytest.raises(NotFoundError):
        session.set_static_image("video", bytes(100), 256)             # une source vidéo ne reçoit pas d'image fixe
    with pytest.raises(InvalidArgumentError):
        session.set_static_image("", bytes(100), 16)
    assert cdll.static_calls == []
