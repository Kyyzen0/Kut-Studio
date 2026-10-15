"""D1 : la molette ne modifie un champ de l'inspecteur que s'il a le focus ; sinon le panneau défile."""

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QSlider

from core.timeline_view_model import TimelineClipView
from core.visual_effects import ClipTransform
from ui.properties_panel import PropertiesPanel


def _view() -> TimelineClipView:
    return TimelineClipView(
        id="clip", track_id="V1", track_index=0, start=0.0, end=4.0, label="Plan", text="", source_path="",
        color_key="#ffffff", track_type="video", enabled=True, keyframes=(), transform=ClipTransform(),
    )


def _wheel(widget, delta: int = -120) -> None:
    center = QPointF(widget.rect().center())
    event = QWheelEvent(center, widget.mapToGlobal(center), QPoint(0, 0), QPoint(0, delta), Qt.NoButton,
                        Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)


def _panel(qtbot) -> PropertiesPanel:
    panel = PropertiesPanel(lambda *_a: None)
    qtbot.addWidget(panel)
    panel.resize(320, 260)             # basse : le contenu défile
    panel.show()
    panel.show_clip(_view())
    qtbot.wait(30)
    return panel


def test_wheel_over_an_unfocused_field_scrolls_the_panel_and_keeps_the_value(qtbot) -> None:
    panel = _panel(qtbot)
    spin = panel._spin_boxes["position_x"]
    bar = panel.scroll_area.verticalScrollBar()
    assert bar.maximum() > 0
    panel.scroll_area.ensureWidgetVisible(spin)
    qtbot.wait(20)
    before_value, before_bar = spin.value(), bar.value()
    _wheel(spin, -240)
    assert spin.value() == before_value
    assert bar.value() > before_bar


def test_wheel_over_an_unfocused_slider_and_combo_does_not_change_them(qtbot) -> None:
    panel = _panel(qtbot)
    slider = panel._slider_widgets["opacity"]
    bar = panel.scroll_area.verticalScrollBar()
    panel.scroll_area.ensureWidgetVisible(slider)
    qtbot.wait(20)
    before, before_bar = slider.value(), bar.value()
    _wheel(slider, 240)
    assert slider.value() == before
    assert bar.value() < before_bar
    combos = [c for c in panel.findChildren(QComboBox) if c.isVisible() and c.isEnabled()]
    for combo in combos:
        index = combo.currentIndex()
        _wheel(combo, -120)
        assert combo.currentIndex() == index


def test_wheel_over_the_focused_field_still_changes_it(qtbot) -> None:
    panel = _panel(qtbot)
    spin = panel._spin_boxes["position_x"]
    spin.setFocus()
    assert spin.hasFocus()
    before = spin.value()
    _wheel(spin, 120)
    assert spin.value() != before


def test_every_numeric_field_of_the_inspector_obeys_the_policy(qtbot) -> None:
    panel = _panel(qtbot)
    fields = [w for w in [*panel.findChildren(QDoubleSpinBox), *panel.findChildren(QSlider)] if w.isEnabled() and w.isVisible()]
    assert fields
    for field in fields:
        before = field.value()
        _wheel(field, -120)
        assert field.value() == before, field.objectName() or type(field).__name__
