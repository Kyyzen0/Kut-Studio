"""Le contrat C de ``libkutengine`` (ABI 1) vu de Python : constantes, structures ``ctypes`` et table des fonctions.

Écrit **contre l'en-tête C** ``kut_engine.h`` du moteur natif (dépôt séparé).
``tests/test_native_engine_abi.py`` relit l'en-tête désigné par ``KUT_ENGINE_HEADER`` et vérifie que chaque fonction, chaque code d'erreur et chaque champ de structure
d'ici y figure, dans le même ordre, avec les mêmes tailles ; si la bibliothèque est construite en local, il compare aussi ses symboles
exportés (``nm -gU``) à :data:`FUNCTIONS`.

Règle de compatibilité (``docs/native-engine.md``) : l'ABI 1 n'ajoute que des fonctions, des champs en fin de structure et des codes ;
ce module n'en lie que ce qu'il connaît. Tout changement incompatible fait passer ``KUT_ENGINE_ABI_VERSION`` à 2 : le chargeur refuse
alors la bibliothèque avant d'appeler quoi que ce soit d'autre.
"""

from __future__ import annotations

import ctypes
from typing import Any, Final

ABI_VERSION: Final = 1
LIBRARY_NAME: Final = "libkutengine.dylib"
ENV_VAR: Final = "KUT_ENGINE_LIB"

# kut_status_t (kut_engine.h)
OK: Final = 0
ERR_INVALID_ARGUMENT: Final = 1
ERR_INVALID_HANDLE: Final = 2
ERR_INVALID_STATE: Final = 3
ERR_UNSUPPORTED: Final = 4
ERR_INVALID_PLAN: Final = 5
ERR_NOT_FOUND: Final = 6
ERR_QUEUE_FULL: Final = 7
ERR_SEEK_REQUIRED: Final = 8
ERR_BAD_FRAME: Final = 9
ERR_DEVICE: Final = 10
ERR_BUFFER_TOO_SMALL: Final = 11
ERR_INTERNAL: Final = 12

STATUS_NAMES: Final = {
    OK: "ok", ERR_INVALID_ARGUMENT: "invalid_argument", ERR_INVALID_HANDLE: "invalid_handle", ERR_INVALID_STATE: "invalid_state",
    ERR_UNSUPPORTED: "unsupported", ERR_INVALID_PLAN: "invalid_plan", ERR_NOT_FOUND: "not_found", ERR_QUEUE_FULL: "queue_full",
    ERR_SEEK_REQUIRED: "seek_required", ERR_BAD_FRAME: "bad_frame", ERR_DEVICE: "device", ERR_BUFFER_TOO_SMALL: "buffer_too_small",
    ERR_INTERNAL: "internal",
}

SESSION_FLAG_NO_PRESENT: Final = 1
SOURCE_ID_MAX: Final = 128
SOURCE_KIND_VIDEO: Final = 1
SOURCE_KIND_IMAGE: Final = 2
FRAME_RENDERED: Final = 1
FRAME_STALE: Final = 2

MAX_PLAN_BYTES: Final = 16 << 20
"""Taille maximale d'un plan KFPL (la bibliothèque refuse au-delà avant de lire)."""
MAX_ERROR_MESSAGE_BYTES: Final = 256


class EngineConfig(ctypes.Structure):
    _fields_ = [("struct_size", ctypes.c_uint32), ("flags", ctypes.c_uint32)]


class SessionConfig(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32), ("flags", ctypes.c_uint32), ("present_width", ctypes.c_uint32),
        ("present_height", ctypes.c_uint32), ("drawable_count", ctypes.c_uint32), ("source_queue_depth", ctypes.c_uint32),
        ("max_in_flight", ctypes.c_uint32),
    ]


class SourceInfo(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32), ("kind", ctypes.c_uint32), ("width", ctypes.c_uint32), ("height", ctypes.c_uint32),
        ("id", ctypes.c_char * (SOURCE_ID_MAX + 1)), ("layout", ctypes.c_char * 16),
    ]


class SourceMapping(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32), ("reserved", ctypes.c_uint32), ("timeline_start_us", ctypes.c_int64),
        ("timeline_end_us", ctypes.c_int64), ("source_in_us", ctypes.c_int64), ("speed", ctypes.c_double), ("frame_us", ctypes.c_int64),
    ]


class FrameInfo(ctypes.Structure):
    _fields_ = [
        ("struct_size", ctypes.c_uint32), ("flags", ctypes.c_uint32), ("position_us", ctypes.c_int64),
        ("sources_ready", ctypes.c_uint32), ("sources_missing", ctypes.c_uint32), ("cpu_ms", ctypes.c_double),
    ]


STATS_COUNTERS: Final = (
    "ticks", "frames_rendered", "frames_not_ready", "frames_presented", "frames_no_drawable", "source_fresh", "source_repeat",
    "source_late", "source_seeking", "source_failed", "source_ended", "skipped_frames", "seeks_issued", "submits_rejected",
    "cpu_readbacks",
)
STATS_FIGURES: Final = ("latency_mean_ms", "latency_max_ms", "gpu_mean_ms")


class Stats(ctypes.Structure):
    _fields_ = (
        [("struct_size", ctypes.c_uint32), ("reserved", ctypes.c_uint32)]
        + [(name, ctypes.c_uint64) for name in STATS_COUNTERS]
        + [(name, ctypes.c_double) for name in STATS_FIGURES]
    )


STRUCTURES: Final = {
    "kut_engine_config": EngineConfig, "kut_engine_session_config": SessionConfig, "kut_source_info": SourceInfo,
    "kut_source_mapping": SourceMapping, "kut_engine_frame_info": FrameInfo, "kut_stats": Stats,
}

_u32, _i32, _u64, _i64 = ctypes.c_uint32, ctypes.c_int32, ctypes.c_uint64, ctypes.c_int64
_vp, _cp, _sz, _dbl = ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_double
_P = ctypes.POINTER

FUNCTIONS: Final[dict[str, tuple[Any, list[Any]]]] = {
    # name: (restype, argtypes). Les handles sont des entiers 64 bits (jamais des pointeurs).
    "kut_engine_abi_version": (_u32, []),
    "kut_engine_version_string": (_cp, []),
    "kut_engine_status_name": (_cp, [_i32]),
    "kut_engine_last_error_message": (_i32, [_vp, _sz, _P(_sz)]),
    "kut_engine_create": (_i32, [_P(EngineConfig), _P(_u64)]),
    "kut_engine_destroy": (_i32, [_u64]),
    "kut_engine_device_name": (_i32, [_u64, _vp, _sz, _P(_sz)]),
    "kut_engine_now_us": (_i64, []),
    "kut_engine_session_create": (_i32, [_u64, _vp, _sz, _P(SessionConfig), _P(_u64)]),
    "kut_engine_session_destroy": (_i32, [_u64]),
    "kut_engine_session_source_count": (_i32, [_u64, _P(_u32)]),
    "kut_engine_session_source_info": (_i32, [_u64, _u32, _P(SourceInfo)]),
    "kut_engine_session_set_mapping": (_i32, [_u64, _cp, _P(SourceMapping)]),
    "kut_engine_session_submit_pixel_buffer": (_i32, [_u64, _cp, _i64, _vp]),
    "kut_engine_session_end_of_stream": (_i32, [_u64, _cp]),
    "kut_engine_session_poll_seek": (_i32, [_u64, _cp, _P(_i64), _P(_i32)]),
    "kut_engine_session_set_static_image": (_i32, [_u64, _cp, _vp, _sz]),
    "kut_engine_session_play": (_i32, [_u64]),
    "kut_engine_session_pause": (_i32, [_u64]),
    "kut_engine_session_seek": (_i32, [_u64, _i64]),
    "kut_engine_session_set_speed": (_i32, [_u64, _dbl]),
    "kut_engine_session_position": (_i32, [_u64, _P(_i64), _P(_i32)]),
    "kut_engine_session_report_audio_position": (_i32, [_u64, _i64, _i64]),
    "kut_engine_session_render": (_i32, [_u64, _P(FrameInfo)]),
    "kut_engine_session_render_at": (_i32, [_u64, _i64, _P(FrameInfo)]),
    "kut_engine_session_wait_idle": (_i32, [_u64]),
    "kut_engine_session_layer": (_i32, [_u64, _P(_vp)]),
    "kut_engine_session_read_canvas": (_i32, [_u64, _vp, _sz, _P(_sz), _P(_u32), _P(_u32), _P(_u32)]),
    "kut_engine_session_get_stats": (_i32, [_u64, _P(Stats)]),
    "kut_engine_session_reset_stats": (_i32, [_u64]),
}
"""Les 30 fonctions de l'ABI 1, dans l'ordre de l'en-tête. ``kut_engine_abi_version`` est liée et lue EN PREMIER, seule."""
