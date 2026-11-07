"""Le chargeur est écrit CONTRE l'en-tête de l'ABI 1 : ``core/native_engine/abi.py`` doit dire exactement ce que dit l'en-tête.

Le test lit l'en-tête ``kut_engine.h`` désigné par la variable ``KUT_ENGINE_HEADER`` (chemin absolu ; aucune copie n'est livrée avec
l'application) et compare : numéro d'ABI, codes d'erreur, drapeaux, fonctions (nom, ordre, types), champs des structures
(noms, ordre, types) et tailles. Si la bibliothèque est construite en local (``KUT_ENGINE_LIB``), les symboles exportés par ``nm -gU``
sont comparés à :data:`core.native_engine.abi.FUNCTIONS`. Sans ``KUT_ENGINE_HEADER`` / ``KUT_ENGINE_LIB``, ces comparaisons sont ignorées avec
leur raison ; les tailles de structures (mesurées avec un compilateur C sur l'en-tête) sont toujours vérifiées.
"""

from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core.native_engine import abi

HEADER_VAR = "KUT_ENGINE_HEADER"
_header_value = os.environ.get(HEADER_VAR, "")
HEADER = Path(_header_value) if _header_value else None
needs_header = pytest.mark.skipif(
    HEADER is None or not HEADER.is_file(), reason=f"{HEADER_VAR} ne désigne aucun en-tête kut_engine.h (comparaison à l'en-tête ignorée)"
)


def _header_text() -> str:
    assert HEADER is not None
    return HEADER.read_text(encoding="utf-8")


def _clean(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _defines() -> dict[str, str]:
    return dict(re.findall(r"^#define\s+(\w+)\s+([^\s/]+)", _header_text(), flags=re.M))


_SCALARS = {
    "uint8_t": ctypes.c_uint8, "int32_t": ctypes.c_int32, "uint32_t": ctypes.c_uint32, "int64_t": ctypes.c_int64,
    "uint64_t": ctypes.c_uint64, "size_t": ctypes.c_size_t, "double": ctypes.c_double, "char": ctypes.c_char,
    "kut_status_t": ctypes.c_int32, "kut_engine_t": ctypes.c_uint64, "kut_engine_session_t": ctypes.c_uint64,
}


def _ctype(c_type: str):
    """Le type ``ctypes`` qui représente ``c_type`` (déclaré dans l'en-tête) à la frontière : ``const char*`` = texte en entrée (``c_char_p``),
    tout autre pointeur d'octets ou de ``void`` = ``c_void_p`` (tampon ou adresse), pointeur vers un type connu = ``POINTER``."""
    normal = " ".join(c_type.split())
    if normal == "void":
        return None
    if normal == "const char*":
        return ctypes.c_char_p
    text = normal.replace("const", " ")
    stars = text.count("*")
    base = " ".join(text.replace("*", " ").split())
    if base == "void":
        return ctypes.c_void_p if stars == 1 else ctypes.POINTER(ctypes.c_void_p)
    if stars == 1 and base in ("uint8_t", "char"):
        return ctypes.c_void_p
    pointee = _SCALARS[base] if base in _SCALARS else abi.STRUCTURES[base]
    for _ in range(stars):
        pointee = ctypes.POINTER(pointee)
    return pointee


def _declarations() -> dict[str, tuple[object, list[object]]]:
    text = _clean(_header_text())
    found: dict[str, tuple[object, list[object]]] = {}
    for returned, name, params in re.findall(r"KUT_API\s+([\w\s\*]+?)\b(kut_engine_\w+)\s*\(([^)]*)\)\s*;", text):
        arguments: list[object] = []
        if params.strip() != "void":
            for param in params.split(","):
                match = re.match(r"^(.*?[\s\*])(\w+)$", param.strip())
                assert match, param
                arguments.append(_ctype(match.group(1).strip()))
        found[name] = (_ctype(returned.strip()), arguments)
    return found


@needs_header
def test_abi_version_and_library_name_match_the_header():
    defines = _defines()
    assert defines["KUT_ENGINE_ABI_VERSION"].rstrip("u") == str(abi.ABI_VERSION)


@needs_header
def test_every_status_code_and_flag_of_the_header_is_known_here():
    defines = _defines()
    codes = {name: int(value) for name, value in defines.items() if name == "KUT_OK" or name.startswith("KUT_ERR_")}
    ours = {"KUT_OK": abi.OK, **{f"KUT_ERR_{name[4:]}": value for name, value in vars(abi).items() if name.startswith("ERR_")}}
    assert codes == ours
    assert {abi.STATUS_NAMES[value] for value in ours.values()} == {name.removeprefix("KUT_ERR_").lower() if name != "KUT_OK" else "ok" for name in ours}
    assert int(defines["KUT_SESSION_FLAG_NO_PRESENT"].rstrip("u")) == abi.SESSION_FLAG_NO_PRESENT
    assert int(defines["KUT_SOURCE_ID_MAX"]) == abi.SOURCE_ID_MAX
    assert int(defines["KUT_SOURCE_KIND_VIDEO"]) == abi.SOURCE_KIND_VIDEO and int(defines["KUT_SOURCE_KIND_IMAGE"]) == abi.SOURCE_KIND_IMAGE
    assert int(defines["KUT_FRAME_RENDERED"].rstrip("u")) == abi.FRAME_RENDERED and int(defines["KUT_FRAME_STALE"].rstrip("u")) == abi.FRAME_STALE


@needs_header
def test_the_function_table_is_the_header_function_for_function():
    declared = _declarations()
    assert list(declared) == list(abi.FUNCTIONS), "mêmes fonctions, dans le même ordre"
    for name, (restype, argtypes) in abi.FUNCTIONS.items():
        assert (restype, argtypes) == declared[name], name
    assert len(declared) == 30


def _struct_fields() -> dict[str, list[tuple[str, object]]]:
    text = _clean(_header_text())
    structs = {}
    for body, name in re.findall(r"typedef struct \w+ \{(.*?)\} (\w+);", text, flags=re.S):
        fields = []
        for line in filter(None, (part.strip() for part in body.split(";"))):
            array = re.match(r"^(\w+)\s+(\w+)\[(.+)\]$", line)
            if array:
                length = eval_length(array.group(3))
                fields.append((array.group(2), _SCALARS[array.group(1)] * length))
                continue
            match = re.match(r"^(\w+)\s+(\w+)$", line)
            assert match, line
            fields.append((match.group(2), _SCALARS[match.group(1)]))
        structs[name] = fields
    return structs


def eval_length(expression: str) -> int:
    defines = _defines()
    total = 0
    for term in expression.split("+"):
        term = term.strip()
        total += int(defines[term]) if term in defines else int(term)
    return total


@needs_header
def test_every_structure_has_the_header_fields_in_the_same_order_and_types():
    structs = _struct_fields()
    assert set(structs) == set(abi.STRUCTURES)
    for name, cls in abi.STRUCTURES.items():
        assert [(field, kind) for field, kind in cls._fields_] == structs[name], name


def test_structure_sizes_and_offsets_are_those_the_c_compiler_gives():
    """Valeurs mesurées avec ``cc -std=c99`` (arm64) sur ce même en-tête : ``sizeof`` et ``offsetof`` des champs qui suivent un tableau ou un double."""
    sizes = {abi.EngineConfig: 8, abi.SessionConfig: 28, abi.SourceInfo: 164, abi.SourceMapping: 48, abi.FrameInfo: 32, abi.Stats: 152}
    for cls, size in sizes.items():
        assert ctypes.sizeof(cls) == size, cls.__name__
    assert abi.SourceInfo.id.offset == 16 and abi.SourceInfo.layout.offset == 145
    assert abi.SourceMapping.speed.offset == 32 and abi.SourceMapping.frame_us.offset == 40
    assert abi.FrameInfo.sources_ready.offset == 16 and abi.FrameInfo.cpu_ms.offset == 24
    assert abi.Stats.ticks.offset == 8 and abi.Stats.latency_mean_ms.offset == 128 and abi.Stats.gpu_mean_ms.offset == 144


@pytest.mark.skipif(sys.platform != "darwin", reason="nm -gU et libkutengine.dylib : macOS seulement")
def test_the_exported_symbols_of_a_local_build_are_exactly_the_function_table():
    path = os.environ.get(abi.ENV_VAR, "")
    if not path or not Path(path).is_file():
        pytest.skip(f"{abi.ENV_VAR} ne désigne aucune bibliothèque construite en local (construction locale du moteur natif)")
    if shutil.which("nm") is None:
        pytest.skip("nm absent")
    output = subprocess.run(["nm", "-gU", path], capture_output=True, text=True, check=True).stdout
    symbols = {line.split()[-1].lstrip("_") for line in output.splitlines() if line.strip()}
    assert symbols == set(abi.FUNCTIONS)
