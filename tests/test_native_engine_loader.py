"""Chargeur du moteur natif : le repli est le comportement par défaut, jamais un plantage.

Cas couverts sans la vraie bibliothèque : variable d'environnement absente / relative / vers un fichier absent ou illisible, fichier qui n'est
pas du Mach-O, Mach-O d'une autre architecture, Mach-O qui n'est pas une bibliothèque, ABI fausse, symbole de version absent, fonction
manquante (de vraies petites bibliothèques sont compilées avec ``cc`` quand il existe), version Mac App Store, plateforme autre que macOS,
chemin qui ne vient jamais d'un projet. La vraie bibliothèque est essayée dans ``test_native_engine_real.py``.
"""

from __future__ import annotations

import ctypes
import shutil
import struct
import subprocess
import sys

import pytest

from core import build_flavor
from core.native_engine import abi, loader
from core.native_engine.loader import NativeEngineUnavailable, Reason

darwin_only = pytest.mark.skipif(sys.platform != "darwin", reason="chargeur macOS : le Mach-O n'existe que là")

MH_MAGIC_64 = 0xFEEDFACF
ARM64, X86_64 = 0x0100000C, 0x01000007


def macho(cputype: int, filetype: int = 6, *, extra: int = 0) -> bytes:
    """Un en-tête Mach-O 64 bits minimal (magic, cpu, sous-type, type de fichier, 0 commande de chargement)."""
    return struct.pack("<IiiIIIII", MH_MAGIC_64, cputype, 0, filetype, 0, 0, 0, 0) + b"\x00" * extra


def fat(entries: list[tuple[int, bytes]], *, wide: bool = False) -> bytes:
    """Un fichier universel : table puis tranches (alignées sur 16 octets)."""
    entry_size = 32 if wide else 20
    offset = 8 + entry_size * len(entries)
    table, blobs = b"", b""
    for cputype, blob in entries:
        pad = (-offset) % 16
        offset += pad
        blobs += b"\x00" * pad
        if wide:
            table += struct.pack(">iiQQII", cputype, 0, offset, len(blob), 4, 0)
        else:
            table += struct.pack(">iiIII", cputype, 0, offset, len(blob), 4)
        blobs += blob
        offset += len(blob)
    return struct.pack(">II", 0xCAFEBABF if wide else 0xCAFEBABE, len(entries)) + table + blobs


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """Aucun état partagé : pas de variable d'environnement du développeur, pas de résultat mémorisé, pas de vrai réglage utilisateur."""
    monkeypatch.delenv(abi.ENV_VAR, raising=False)
    monkeypatch.setenv("KUT_STUDIO_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(build_flavor, "STORE", False)
    loader.reset()
    yield
    loader.reset()


def _code(result) -> str:
    assert isinstance(result, Reason), result
    return result.code


# -- où l'on cherche ------------------------------------------------------------------------------------------------------


def test_nothing_configured_nothing_installed_is_a_normal_unavailable_with_a_readable_reason():
    assert loader.candidate_paths({}, frozen=False) == []
    assert loader.available() is False
    reason = loader.unavailable_reason()
    assert reason is not None and reason.code in {"not_found", "platform"}
    assert reason.message and reason.translation_key == f"native_engine.reason.{reason.code}"


def test_the_environment_variable_comes_first_then_the_bundle_and_only_absolute_paths_count(tmp_path):
    absolute = tmp_path / "libkutengine.dylib"
    assert loader.candidate_paths({abi.ENV_VAR: str(absolute)}, frozen=False) == [absolute]
    assert loader.candidate_paths({abi.ENV_VAR: "relative/lib.dylib"}, frozen=False) == [None]     # présente mais invalide : signalée
    assert loader.candidate_paths({abi.ENV_VAR: "/x\x00y"}, frozen=False) == [None]
    assert loader.candidate_paths({abi.ENV_VAR: "/" + "a" * 5000}, frozen=False) == [None]
    assert loader.candidate_paths({abi.ENV_VAR: ""}, frozen=False) == []
    app = tmp_path / "Kut-Studio.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)
    exe = app / "Kut-Studio"
    bundled = tmp_path.resolve() / "Kut-Studio.app" / "Contents" / "Frameworks" / abi.LIBRARY_NAME
    assert loader.candidate_paths({}, executable=str(exe), frozen=True) == [bundled]
    assert loader.candidate_paths({abi.ENV_VAR: str(absolute)}, executable=str(exe), frozen=True) == [absolute, bundled]
    assert loader.bundled_library_path(str(exe), frozen=False) is None                            # sources, CI : pas de bundle


def test_a_project_file_can_never_name_the_library():
    """Les seules entrées du chargeur sont l'environnement et le bundle : aucune fonction ne prend un chemin venu d'ailleurs, et le module ne lit
    ni projet, ni préréglage, ni réglage."""
    import inspect

    source = inspect.getsource(loader)
    assert "settings" not in source.lower() and "project" not in source.lower().replace("projet", "")
    assert [name for name in ("load_library", "available", "unavailable_reason") if inspect.signature(getattr(loader, name)).parameters] == []


# -- le fichier, avant dlopen ----------------------------------------------------------------------------------------------


def test_macho_header_checks_without_loading_anything(tmp_path, monkeypatch):
    good = tmp_path / "good.dylib"
    good.write_bytes(macho(ARM64))
    assert loader.macho_problem(good, cpu=ARM64) is None
    assert loader.macho_problem(good, cpu=X86_64) == "wrong_arch"
    monkeypatch.setattr(loader, "_expected_cpu", lambda: None)                                     # architecture du processus inconnue
    assert loader.macho_problem(good) == "wrong_arch"

    for name, content, expected in (
        ("empty", b"", "not_macho"),
        ("text", b"#!/bin/sh\necho hello\n", "not_macho"),
        ("elf", b"\x7fELF" + b"\x00" * 60, "not_macho"),
        ("short", macho(ARM64)[:6], "not_macho"),
        ("exe", macho(ARM64, filetype=2), "not_dylib"),
        ("bundle", macho(ARM64, filetype=8), "not_dylib"),
        ("x86", macho(X86_64), "wrong_arch"),
    ):
        path = tmp_path / name
        path.write_bytes(content)
        assert loader.macho_problem(path, cpu=ARM64) == expected, name
    assert loader.macho_problem(tmp_path / "missing", cpu=ARM64) == "unreadable"


def test_universal_files_use_the_slice_of_the_running_architecture(tmp_path):
    both = tmp_path / "both"
    both.write_bytes(fat([(X86_64, macho(X86_64)), (ARM64, macho(ARM64))]))
    assert loader.macho_problem(both, cpu=ARM64) is None and loader.macho_problem(both, cpu=X86_64) is None
    only_x86 = tmp_path / "x86only"
    only_x86.write_bytes(fat([(X86_64, macho(X86_64))], wide=True))
    assert loader.macho_problem(only_x86, cpu=ARM64) == "wrong_arch"
    bad_slice = tmp_path / "badslice"
    bad_slice.write_bytes(fat([(ARM64, b"not a mach-o header at all......")]))
    assert loader.macho_problem(bad_slice, cpu=ARM64) == "not_macho"
    huge = tmp_path / "huge"
    huge.write_bytes(struct.pack(">II", 0xCAFEBABE, 0xFFFFFFFF) + b"\x00" * 64)                  # 4 milliards d'architectures annoncées
    assert loader.macho_problem(huge, cpu=ARM64) == "not_macho"
    truncated = tmp_path / "truncated"
    truncated.write_bytes(struct.pack(">II", 0xCAFEBABE, 3) + b"\x00" * 10)
    assert loader.macho_problem(truncated, cpu=ARM64) == "not_macho"


@darwin_only
def test_probe_refuses_bad_files_with_a_reason_and_never_raises(tmp_path):
    cases = {
        "missing.dylib": (None, "not_found"),
        "text.dylib": (b"hello", "not_macho"),
        "wrongarch.dylib": (macho(X86_64 if loader._expected_cpu() == ARM64 else ARM64), "wrong_arch"),
        "exe.dylib": (macho(loader._expected_cpu() or ARM64, filetype=2), "not_dylib"),
    }
    for name, (content, expected) in cases.items():
        path = tmp_path / name
        if content is not None:
            path.write_bytes(content)
        result = loader.probe({abi.ENV_VAR: str(path)}, frozen=False)
        assert _code(result) == expected, name
        assert str(tmp_path) not in result.message and str(tmp_path) not in result.detail         # aucun chemin recopié
    assert _code(loader.probe({abi.ENV_VAR: str(tmp_path)}, frozen=False)) == "not_found"          # un dossier n'est pas un fichier
    assert _code(loader.probe({abi.ENV_VAR: "relative.dylib"}, frozen=False)) == "bad_path"
    unreadable = tmp_path / "locked.dylib"
    unreadable.write_bytes(macho(loader._expected_cpu() or ARM64))
    unreadable.chmod(0)
    try:
        assert _code(loader.probe({abi.ENV_VAR: str(unreadable)}, frozen=False)) in {"unreadable", "load_failed"}
    finally:
        unreadable.chmod(0o600)


# -- vraies petites bibliothèques : version d'ABI, fonctions ---------------------------------------------------------------


def _compile(tmp_path, name: str, source: str):
    compiler = shutil.which("cc") or shutil.which("clang")
    if compiler is None or sys.platform != "darwin":
        pytest.skip("pas de compilateur C : bibliothèques d'essai non construites")
    c_file = tmp_path / f"{name}.c"
    c_file.write_text(source, encoding="utf-8")
    out = tmp_path / f"{name}.dylib"
    subprocess.run([compiler, "-dynamiclib", "-o", str(out), str(c_file)], check=True, capture_output=True)
    return out


def _stub_source(version: int, *, skip: str = "") -> str:
    """Une bibliothèque qui annonce ``version`` et définit chaque fonction de l'ABI (sans rien faire), sauf ``skip``."""
    lines = ["#include <stdint.h>", "#include <stddef.h>"]
    for name in abi.FUNCTIONS:
        if name == skip:
            continue
        if name == "kut_engine_abi_version":
            lines.append(f"uint32_t {name}(void) {{ return {version}u; }}")
        elif name == "kut_engine_version_string":
            lines.append(f'const char* {name}(void) {{ return "stub"; }}')
        else:
            lines.append(f"int32_t {name}() {{ return 0; }}")
    return "\n".join(lines)


@darwin_only
def test_a_library_with_another_abi_version_is_refused_and_no_other_function_is_bound(tmp_path):
    lib = _compile(tmp_path, "abi2", _stub_source(2))
    result = loader.probe({abi.ENV_VAR: str(lib)}, frozen=False)
    assert _code(result) == "abi_mismatch" and result.detail == "2/1"
    # rien d'autre n'a été lié : les fonctions de la bibliothèque rechargée n'ont ni argtypes ni restype posés par le chargeur
    raw = ctypes.CDLL(str(lib))
    assert raw.kut_engine_create.argtypes is None


@darwin_only
def test_a_library_without_the_version_symbol_or_with_a_missing_function_is_refused(tmp_path):
    no_symbol = _compile(tmp_path, "nosym", "int unrelated(void) { return 1; }")
    assert _code(loader.probe({abi.ENV_VAR: str(no_symbol)}, frozen=False)) == "no_abi_symbol"
    incomplete = _compile(tmp_path, "incomplete", _stub_source(1, skip="kut_engine_session_render"))
    result = loader.probe({abi.ENV_VAR: str(incomplete)}, frozen=False)
    assert _code(result) == "incomplete" and result.detail == "kut_engine_session_render"


@darwin_only
def test_a_library_with_the_right_abi_and_every_function_is_bound_with_its_types(tmp_path):
    lib = _compile(tmp_path, "good", _stub_source(1))
    result = loader.probe({abi.ENV_VAR: str(lib)}, frozen=False)
    assert isinstance(result, loader.NativeLibrary)
    assert result.abi_version == 1 and result.version_string == "stub"
    for name, (restype, argtypes) in abi.FUNCTIONS.items():
        function = getattr(result.cdll, name)
        assert function.restype == restype and list(function.argtypes) == argtypes, name


@darwin_only
def test_available_is_memoised_and_reset_forgets_it(tmp_path, monkeypatch):
    lib = _compile(tmp_path, "memo", _stub_source(1))
    monkeypatch.setenv(abi.ENV_VAR, str(lib))
    assert loader.available() is True and loader.unavailable_reason() is None
    monkeypatch.setenv(abi.ENV_VAR, str(tmp_path / "gone.dylib"))
    assert loader.available() is True            # mémorisé
    loader.reset()
    assert loader.available() is False
    with pytest.raises(NativeEngineUnavailable) as raised:
        loader.load_library()
    assert raised.value.reason.code == "not_found"


# -- Store et plateforme ------------------------------------------------------------------------------------------------


@darwin_only
def test_the_store_build_never_loads_a_native_library_even_when_the_variable_points_to_a_valid_one(tmp_path, monkeypatch):
    lib = _compile(tmp_path, "storeok", _stub_source(1))
    monkeypatch.setenv(abi.ENV_VAR, str(lib))
    monkeypatch.setattr(build_flavor, "STORE", True)
    loader.reset()
    assert loader.available() is False
    assert loader.unavailable_reason().code == "store"                                            # type: ignore[union-attr]
    with pytest.raises(NativeEngineUnavailable):
        loader.load_library()
    # preuve que c'est bien le Store qui refuse : sans lui la même bibliothèque se charge
    monkeypatch.setattr(build_flavor, "STORE", False)
    loader.reset()
    assert loader.available() is True


def test_other_platforms_are_unavailable_with_their_own_reason(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert _code(loader.probe({abi.ENV_VAR: "/whatever"}, frozen=False)) == "platform"


def test_every_reason_has_a_message_and_a_translation_in_the_three_languages():
    from ui import i18n
    from ui.i18n_native_engine import NATIVE_ENGINE_TRANSLATIONS

    assert set(NATIVE_ENGINE_TRANSLATIONS) == {f"native_engine.reason.{code}" for code in loader.REASON_MESSAGES}
    for code, message in loader.REASON_MESSAGES.items():
        entry = NATIVE_ENGINE_TRANSLATIONS[f"native_engine.reason.{code}"]
        assert entry["fr"] == message
        assert all(entry[lang].strip() for lang in ("fr", "en", "es"))
        assert i18n.translate_strict(f"native_engine.reason.{code}", "en") == entry["en"]


def test_importing_the_package_does_not_load_anything_nor_touch_the_filesystem(tmp_path):
    """Importer le paquet ne cherche pas la bibliothèque (pas de ``probe`` à l'import) et n'écrit rien."""
    import importlib

    from core import native_engine

    importlib.reload(native_engine.loader)
    assert native_engine.loader._cached is None
    assert not (tmp_path / "config").exists()
