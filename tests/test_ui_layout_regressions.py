"""Régressions visuelles de la refonte compacte de l'interface."""

from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QVBoxLayout, QWidget

from ui.library_organization_widgets import FilterChipBar
from ui.properties_panel import PropertiesPanel
from ui.timeline_ruler import TimelineRuler


def _render(widget) -> QImage:
    image = QImage(widget.size(), QImage.Format_RGB32)
    widget.render(image)
    return image


def test_ruler_draws_nothing_over_the_track_headers(qtbot) -> None:
    ruler = TimelineRuler()
    qtbot.addWidget(ruler)
    ruler.resize(900, 32)
    ruler.sync(
        scroll_x=0.0,
        zoom=1.0,
        pixels_per_second=120.0,
        duration=30.0,
        fps=30.0,
        playhead=5.0,
        origin=220.0,
        markers=[],
        background="#000000",
        tick="#ffffff",
        text="#ffffff",
        playhead_color="#ff0000",
        marker_color="#ffff00",
    )
    image = _render(ruler)
    background = QColor("#000000").rgb()
    for x in range(0, 215):
        for y in range(image.height()):
            assert image.pixel(x, y) == background, (x, y)


def test_filter_chips_keep_their_labels_in_a_narrow_column(qtbot) -> None:
    column = QWidget()
    qtbot.addWidget(column)
    column.setFixedWidth(260)
    layout = QVBoxLayout(column)
    layout.setContentsMargins(0, 0, 0, 0)
    bar = FilterChipBar()
    layout.addWidget(bar)
    layout.addStretch(1)
    column.resize(260, 400)
    column.show()
    qtbot.waitExposed(column)
    for button in bar._buttons.values():
        assert button.width() >= button.sizeHint().width()
        assert button.geometry().right() <= bar.width()
        assert button.geometry().bottom() <= bar.height()


def test_overflow_inspector_tabs_are_not_orphan_windows(qtbot) -> None:
    panel = PropertiesPanel(lambda *_args: None)
    qtbot.addWidget(panel)
    for button in panel.inspector_tab_buttons:
        assert button.parent() is not None
        assert not button.isWindow()
