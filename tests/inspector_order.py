"""Ordre d'affichage des widgets visibles de l'inspecteur (outil des tests de niveau d'usage, lot 3).

``display_order`` parcourt les layouts dans l'ordre où l'écran les montre (les lignes d'un ``QFormLayout`` par numéro de
ligne). ``ordered_dump`` en tire, par état de clip et par onglet, la liste ordonnée « Classe|nom » des widgets visibles,
hors ce que le niveau d'usage ajoute (conteneurs, pliants, lien du bas) et hors les contrôles que ce lot déplace à dessein
(voir ``moved_roots``). Le fichier ``tests/data/inspector_visible_before_usage_levels.json`` a été produit par ce même
code sur l'inspecteur d'``origin/main`` d'avant le lot.
"""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QFormLayout, QLayout, QWidget

IGNORED_NAMES = ("level_gate", "advanced_fold", "more_options_button", "fill_frame_quick")


def _entries(layout: QLayout) -> list[int]:
    indexes = list(range(layout.count()))
    if isinstance(layout, QFormLayout):
        def key(index: int) -> tuple[int, int]:
            row, role = layout.getItemPosition(index)
            return row, int(getattr(role, "value", role))
        indexes.sort(key=key)
    return indexes


def _walk_layout(layout: QLayout, panel: QWidget, out: list[QWidget]) -> None:
    for index in _entries(layout):
        item = layout.itemAt(index)
        widget = item.widget()
        if widget is not None:
            if widget.isVisibleTo(panel):
                out.append(widget)
                _walk_widget(widget, panel, out)
        elif item.layout() is not None:
            _walk_layout(item.layout(), panel, out)


def _walk_widget(widget: QWidget, panel: QWidget, out: list[QWidget]) -> None:
    if widget.layout() is not None:
        _walk_layout(widget.layout(), panel, out)


def display_order(panel) -> list[QWidget]:
    out: list[QWidget] = []
    _walk_widget(panel.scroll_area.widget(), panel, out)
    return out


def moved_roots(panel) -> list[QWidget]:
    """Les contrôles que le lot déplace à dessein (leur sous-arbre est écarté de la comparaison d'ordre)."""
    roots: list[QWidget] = [
        *panel._diamonds.values(), *(b for pair in panel._keyframe_nav_buttons.values() for b in pair),
        panel.frame_align, panel.advanced_transform, panel.reset_movement_button, panel.time_section,
        panel._audio_pan_row, panel.color_reset_button, panel.graphics_group, panel.subtitle_group,
        panel.compositing_group.blend_section, panel.compositing_group.crop_section,
    ]
    block = getattr(panel, "keyframe_block", None)
    if block is not None:
        roots.append(block)
    # ADR-0008, étape 4 : les rangées ‹ ◆ › sous les réglages d'effets animables (elles n'existent que pour l'effet affiché ;
    # le golden d'origin/main n'en sélectionne aucun, donc ne les connaît pas : ce sont des ajouts, pas un ordre déplacé).
    roots.extend(row.gate for row in getattr(panel, "_effect_keyframe_rows", {}).values())
    # ADR-0008, étape 8 : les rangées ‹ ◆ › sous les sept réglages de couleur (ajouts, absents du golden d'origin/main).
    roots.extend(row.gate for row in getattr(panel, "_color_keyframe_rows", {}).values() if row.gate is not None)
    return roots


def ordered_dump(panel, states: dict) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for state, view in states.items():
        panel.show_clip(view)
        excluded: set[QWidget] = set()          # relevé après show_clip : les rangées d'effets sont reconstruites à chaque clip
        for root in moved_roots(panel):
            excluded.add(root)
            excluded.update(root.findChildren(QWidget))
        for tab in range(7):
            panel._select_inspector_tab(tab)
            QApplication.processEvents()
            names = []
            for widget in display_order(panel):
                name = widget.objectName()
                if (widget in excluded or name in IGNORED_NAMES or name.startswith("qt_")
                        or type(widget).__name__ == "QScrollBar"):
                    continue
                names.append(f"{type(widget).__name__}|{name}")
            result[f"{state}/{tab}"] = names
    return result


def clip_states() -> dict:
    from core.graphics import GraphicOverlay
    from core.text_style import default_text_style
    from core.time_remapping import TimeRemapping
    from core.timeline_view_model import TimelineClipView
    from core.visual_effects import ClipTransform

    def view(**kw):
        base = dict(id="c", track_id="T", track_index=0, start=0.0, end=4.0, label="Plan", text="", source_path="",
                    color_key="#fff", track_type="video", enabled=True, keyframes=(), transform=ClipTransform())
        base.update(kw)
        return TimelineClipView(**base)

    return {
        "none": None,
        "video": view(time_remapping=TimeRemapping()),
        "video_locked": view(time_remapping=TimeRemapping(), locked=True),
        "audio": view(track_type="audio", time_remapping=TimeRemapping()),
        "subtitle": view(track_type="subtitle", text="Salut", text_style=default_text_style()),
        "graphics": view(track_type="graphics", graphic=GraphicOverlay()),
    }
