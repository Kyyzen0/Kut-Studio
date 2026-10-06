"""Les polices embarquées (Anton, Saira ExtraCondensed) rendent pareil sur toutes les machines."""

from __future__ import annotations

import logging

from core.bundled_fonts import BUNDLED_FAMILIES, BUNDLED_FONT_FILES, register_bundled_fonts
from core.platform_paths import bundled_assets_dir


def test_every_bundled_font_ships_with_its_licence():
    directory = bundled_assets_dir("fonts")
    assert all((directory / name).is_file() for name in BUNDLED_FONT_FILES)
    licence = (directory / "OFL.txt").read_text(encoding="utf-8")
    assert "SIL OPEN FONT LICENSE Version 1.1" in licence
    assert "The Anton Project Authors" in licence and "The Saira Project Authors" in licence


def test_the_bundled_files_register_the_template_families(qapp):
    families = register_bundled_fonts()
    assert set(BUNDLED_FAMILIES) <= set(families)
    assert register_bundled_fonts() is families                     # une seule fois par processus


def test_the_rasterizer_resolves_the_bundled_families(qapp):
    from core.mograph_raster import resolve_family

    for family in BUNDLED_FAMILIES:
        assert resolve_family(family) == family


def test_a_missing_family_is_reported_once_in_the_log(qapp, caplog):
    from core.mograph_raster import resolve_family

    with caplog.at_level(logging.WARNING, logger="kut_studio.fonts"):
        resolve_family("Police Introuvable Kut 42")
        resolve_family("Police Introuvable Kut 42")
        resolve_family("Sans Serif")                                   # générique : remplacement voulu, pas de bruit
    messages = [record.getMessage() for record in caplog.records]
    assert len([m for m in messages if "Police Introuvable Kut 42" in m]) == 1
    # Rien sur « Sans Serif » lui-même (sous Linux, il peut être le remplaçant nommé dans le message ci-dessus).
    assert not any(m.startswith("Police « Sans Serif »") for m in messages)
