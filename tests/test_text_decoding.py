"""Tests pour ``core.text_decoding`` et ``core.atomic_io``."""

from __future__ import annotations

import codecs
import os
from pathlib import Path

import pytest

from core import atomic_io
from core.atomic_io import atomic_write_text
from core.text_decoding import decode_text_bytes

SAMPLE = "Café — « déjà » vu\r\nligne 2\n"


@pytest.mark.parametrize(
    "data",
    [
        SAMPLE.encode("utf-8"),
        codecs.BOM_UTF8 + SAMPLE.encode("utf-8"),
        codecs.BOM_UTF16_LE + SAMPLE.encode("utf-16-le"),
        codecs.BOM_UTF16_BE + SAMPLE.encode("utf-16-be"),
        SAMPLE.encode("cp1252"),
    ],
    ids=["utf8", "utf8-bom", "utf16-le-bom", "utf16-be-bom", "cp1252"],
)
def test_decode_text_bytes_recovers_the_text(data: bytes) -> None:
    """UTF-8 avec ou sans BOM, UTF-16 avec BOM et Windows-1252 redonnent le même texte, sans BOM."""
    assert decode_text_bytes(data) == SAMPLE


def test_decode_text_bytes_never_raises_on_bytes_undefined_in_cp1252() -> None:
    """Les 5 octets indéfinis de Windows-1252 retombent sur Latin-1 au lieu de lever une exception."""
    assert decode_text_bytes(b"a\x81b\x9dc") == "a\x81b\x9dc"


def test_decode_text_bytes_prefers_utf8_over_cp1252() -> None:
    """Un texte UTF-8 valide n'est jamais relu comme du Windows-1252 (pas de « CafÃ© »)."""
    assert decode_text_bytes("Café".encode("utf-8")) == "Café"


def test_atomic_write_text_creates_parent_and_writes(tmp_path: Path) -> None:
    """Le dossier parent est créé et le contenu est écrit en entier, sans temporaire résiduel."""
    target = tmp_path / "sub" / "file.txt"
    assert atomic_write_text(target, "été") == target
    assert target.read_text(encoding="utf-8") == "été"
    assert [p.name for p in target.parent.iterdir()] == ["file.txt"]


def test_atomic_write_text_keeps_previous_content_on_failure(tmp_path: Path, monkeypatch) -> None:
    """Si le remplacement échoue, l'ancien fichier reste intact et le temporaire est supprimé."""
    target = tmp_path / "file.txt"
    target.write_text("ancien", encoding="utf-8")

    def fail_replace(src, dst):
        raise OSError("disque plein")

    monkeypatch.setattr(atomic_io.os, "replace", fail_replace)
    with pytest.raises(OSError, match="disque plein"):
        atomic_write_text(target, "nouveau")
    assert target.read_text(encoding="utf-8") == "ancien"
    assert sorted(os.listdir(tmp_path)) == ["file.txt"]


def test_atomic_write_text_cleans_up_on_encoding_error(tmp_path: Path) -> None:
    """Une erreur d'encodage ne laisse ni cible tronquée ni temporaire."""
    target = tmp_path / "file.txt"
    target.write_text("ancien", encoding="utf-8")
    with pytest.raises(UnicodeEncodeError):
        atomic_write_text(target, "€", encoding="ascii")
    assert target.read_text(encoding="utf-8") == "ancien"
    assert sorted(os.listdir(tmp_path)) == ["file.txt"]
