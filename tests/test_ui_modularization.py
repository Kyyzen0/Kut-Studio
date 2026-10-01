"""Garde-fous du découpage de ``timeline_panel`` et ``properties_panel``."""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

PACKAGES = (
    "ui/timeline_widgets",
    "ui/timeline_panel_mixins",
    "ui/properties_widgets",
    "ui/properties_panel_mixins",
)
MAX_MODULE_LINES = 700


def _modules() -> list[str]:
    names = []
    for package in PACKAGES:
        for path in sorted((ROOT / package).glob("*.py")):
            stem = path.stem
            if stem != "__init__":
                names.append(f"{package.replace('/', '.')}.{stem}")
    return names


@pytest.mark.parametrize("module", _modules())
def test_module_imports_alone_without_cycle(module: str) -> None:
    """Chaque module s'importe seul dans un interpréteur neuf (pas de cycle)."""
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=ROOT,
        env={"QT_QPA_PLATFORM": "offscreen", "PATH": "", "HOME": str(Path.home())},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_modules_stay_small() -> None:
    """Aucun module extrait ne redevient un fichier géant."""
    too_big = {
        str(path.relative_to(ROOT)): len(path.read_text(encoding="utf-8").splitlines())
        for package in PACKAGES
        for path in (ROOT / package).glob("*.py")
        if len(path.read_text(encoding="utf-8").splitlines()) > MAX_MODULE_LINES
    }
    assert not too_big, too_big


def test_clip_widget_does_not_import_the_panel_at_runtime() -> None:
    """``ClipWidget`` ne dépend du panneau que pour le typage."""
    source = (ROOT / "ui/timeline_widgets/clip_widget.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    runtime_imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("ui.timeline_panel")
    ]
    assert not runtime_imports


def test_historical_imports_still_work() -> None:
    timeline = importlib.import_module("ui.timeline_panel")
    properties = importlib.import_module("ui.properties_panel")
    for name in (
        "TimelinePanel", "ClipWidget", "TrackRowHeader", "TransitionMarkerWidget",
        "_TrackGrid", "_HEIGHTS", "_COLLAPSED_HEIGHT", "_current_palette",
    ):
        assert hasattr(timeline, name), name
    for name in ("PropertiesPanel", "ColorCurveEditor", "_DiamondButton", "_PROPERTY_RANGES"):
        assert hasattr(properties, name), name


def test_panels_compose_their_mixins() -> None:
    from ui.properties_panel import PropertiesPanel
    from ui.timeline_panel import TimelinePanel

    timeline_mixins = {c.__name__ for c in TimelinePanel.__mro__}
    assert {"ToolbarMixin", "LayoutMixin", "ZoomPlayheadMixin", "SelectionMixin",
            "DragToolsMixin", "PreviewsMixin"} <= timeline_mixins
    properties_mixins = {c.__name__ for c in PropertiesPanel.__mro__}
    assert {"ConstructionMixin", "TabsMixin", "ColorMixin", "AudioMixin", "EffectsMixin",
            "AudioEffectsMixin", "KeyframesMixin", "ClipContextMixin",
            "TransitionSubtitleMixin", "TimeRemappingMixin"} <= properties_mixins


@pytest.mark.parametrize(
    ("owner", "methods"),
    [
        ("ui.timeline_panel.TimelinePanel",
         ("set_project", "select_clip", "set_tool", "snap_time", "zoom_in", "zoom_out",
          "fit_timeline", "set_playhead_seconds", "clip_rect", "format_time", "pixmap_for")),
        ("ui.properties_panel.PropertiesPanel",
         ("show_clip", "show_transition", "update_color_grade_from_clip",
          "update_effects_from_clip", "update_audio_effects_from_clip",
          "update_transform_from_clip", "set_audio_clip", "refresh_keyframe_diamonds",
          "group_style", "make_slider")),
    ],
)
def test_public_methods_are_preserved(owner: str, methods: tuple[str, ...]) -> None:
    module_name, class_name = owner.rsplit(".", 1)
    cls = getattr(importlib.import_module(module_name), class_name)
    for method in methods:
        assert callable(getattr(cls, method, None)), f"{owner}.{method}"
