"""Un seul dossier de cache : les analyses de tracking, images de calques et textes survivent à l'OS.

Trois modules retombaient sur ``tempfile.gettempdir()/kut-studio-cache`` quand ``KUT_STUDIO_CACHE_DIR`` était
absent : dossier que macOS et Windows vident, ignoré du reste de l'application (aperçu, proxies).
"""

from __future__ import annotations

import pytest

from core import graphics_raster, mograph_stream, tracking_engine
from core.platform_paths import user_cache_dir


@pytest.fixture
def native_cache(tmp_path, monkeypatch):
    """Aucune surcharge d'environnement : le dossier de cache natif, redirigé vers un dossier de test."""
    monkeypatch.delenv("KUT_STUDIO_CACHE_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path


def test_mograph_and_tracking_caches_live_under_the_native_cache_folder(native_cache):
    root = user_cache_dir()
    assert str(root).startswith(str(native_cache))
    assert mograph_stream.cache_directory() == root / mograph_stream.CACHE_KIND
    assert tracking_engine.cache_directory() == root / tracking_engine.CACHE_KIND


def test_rasterized_text_lands_under_the_native_cache_folder(native_cache, qapp):
    from core.graphics import GraphicOverlay, GraphicType

    graphic = GraphicOverlay(type=GraphicType.TEXT, text="Bonjour", width=120, height=40)
    path = graphics_raster.rasterize_text_graphic(graphic)
    assert str(path).startswith(str(user_cache_dir() / "graphics"))


def test_the_environment_override_still_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("KUT_STUDIO_CACHE_DIR", str(tmp_path / "forcé"))
    assert mograph_stream.cache_directory() == tmp_path / "forcé" / mograph_stream.CACHE_KIND
    assert tracking_engine.cache_directory() == tmp_path / "forcé" / tracking_engine.CACHE_KIND
