"""Éditeur de courbes (Graph Editor) : outil avancé, jamais obligatoire.

Affiche la courbe d'**une** propriété animée du clip sélectionné et permet
de régler ce que la timeline ne montre pas : valeurs, interpolations et
tangentes Bézier. Aucune logique d'animation ici : la courbe dessinée est
évaluée par :mod:`core.animation` (la même que l'aperçu et l'export) et
chaque modification passe par la fenêtre principale
(:mod:`core.keyframe_editing`). Un geste continu (glisser) modifie le projet
en direct mais n'enregistre qu'**une** entrée d'historique, au relâchement.

Intégrer une nouvelle propriété : l'enregistrer dans
:mod:`core.animation_targets` suffit ; elle apparaît dans la liste.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.animation import InterpolationType, TangentMode
from core.animation_targets import get_target
from core.keyframe_editing import (
    KeyframeRef,
    add_keyframe,
    find_clip,
    move_keyframes,
    set_interpolation,
    set_keyframe_values,
    set_tangents,
)
from ui import i18n
from ui.adaptive_layout import ElidedLabel, make_shrinkable
from ui.theme import COLORS, label_style

HANDLE_RADIUS = 4.5
KEYFRAME_SIZE = 9.0
PICK_DISTANCE = 8.0


class CurveCanvas(QWidget):
    """Zone de dessin : courbe, keyframes, poignées, tête de lecture."""

    selection_changed = Signal()

    def __init__(self, editor: GraphEditorWindow) -> None:
        super().__init__(editor)
        self.editor = editor
        self.setMinimumSize(480, 260)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        # Vue : temps local [t0, t1] × valeur [v0, v1].
        self.t0, self.t1, self.v0, self.v1 = 0.0, 1.0, 0.0, 1.0
        self._gesture: str | None = None
        self._press = QPointF()
        self._origin: dict = {}
        self._rubber: QRectF | None = None

    # -- repère ------------------------------------------------------------------------------------

    def _plot(self) -> QRectF:
        return QRectF(48, 10, max(10, self.width() - 58), max(10, self.height() - 34))

    def to_screen(self, t: float, v: float) -> QPointF:
        plot = self._plot()
        x = plot.left() + (t - self.t0) / max(1e-9, self.t1 - self.t0) * plot.width()
        y = plot.bottom() - (v - self.v0) / max(1e-12, self.v1 - self.v0) * plot.height()
        return QPointF(x, y)

    def to_curve(self, point: QPointF) -> tuple[float, float]:
        plot = self._plot()
        t = self.t0 + (point.x() - plot.left()) / plot.width() * (self.t1 - self.t0)
        v = self.v0 + (plot.bottom() - point.y()) / plot.height() * (self.v1 - self.v0)
        return t, v

    def frame(self, times: list[float], values: list[float]) -> None:
        """Cadre la vue sur des points (« tout cadrer » / « cadrer la sélection »)."""
        if not times:
            return
        t0, t1 = min(times), max(times)
        v0, v1 = min(values), max(values)
        if t1 - t0 < 1e-6:
            t0, t1 = t0 - 0.5, t1 + 0.5
        if v1 - v0 < 1e-9:
            pad = max(abs(v0) * 0.1, 0.1)
            v0, v1 = v0 - pad, v1 + pad
        dt, dv = (t1 - t0) * 0.08, (v1 - v0) * 0.12
        self.t0, self.t1, self.v0, self.v1 = t0 - dt, t1 + dt, v0 - dv, v1 + dv
        self.update()

    # -- dessin -------------------------------------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(COLORS.get("panel_alt", "#1E1E1E")))
        plot = self._plot()
        self._paint_grid(painter, plot)
        curve = self.editor.curve()
        if curve is not None and curve:
            self._paint_curve(painter, plot, curve)
            self._paint_keyframes(painter, curve)
        self._paint_playhead(painter, plot)
        if self._rubber is not None:
            painter.setPen(QPen(QColor(COLORS.get("accent", "#36E6C3")), 1, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self._rubber)
        painter.end()

    def _paint_grid(self, painter: QPainter, plot: QRectF) -> None:
        grid = QColor(COLORS.get("border", "#333333"))
        text = QColor(COLORS.get("text_muted", COLORS.get("muted", "#9098A2")))
        painter.setPen(QPen(grid, 1))
        painter.drawRect(plot)
        for t in _ticks(self.t0, self.t1, 8):
            x = self.to_screen(t, 0).x()
            painter.setPen(QPen(grid, 1))
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            painter.setPen(text)
            painter.drawText(QPointF(x + 2, plot.bottom() + 14), _format(t) + " s")
        for v in _ticks(self.v0, self.v1, 6):
            y = self.to_screen(0, v).y()
            painter.setPen(QPen(grid, 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(text)
            painter.drawText(QRectF(0, y - 8, 44, 16), Qt.AlignRight | Qt.AlignVCenter, _format(v))

    def _paint_curve(self, painter: QPainter, plot: QRectF, curve) -> None:
        spec = self.editor.target().spec
        path = QPainterPath()
        steps = max(2, int(plot.width() / 2))
        for i in range(steps + 1):
            t = self.t0 + (self.t1 - self.t0) * i / steps
            v = float(spec.clamp(curve.evaluate(t)))
            point = self.to_screen(t, v)
            if i == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        painter.setPen(QPen(QColor(COLORS.get("accent", "#36E6C3")), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

    def _handles(self, curve):
        """Poignées Bézier des keyframes sélectionnés : ``(ref, côté, point écran, point courbe)``."""
        result = []
        selected = self.editor.selected_ids()
        frames = curve.keyframes
        for index, keyframe in enumerate(frames):
            if keyframe.id not in selected:
                continue
            incoming, outgoing = curve.resolved_slopes(index)
            if index + 1 < len(frames) and keyframe.interpolation is InterpolationType.BEZIER:
                span = (frames[index + 1].time_seconds - keyframe.time_seconds) / 3.0
                t, v = keyframe.time_seconds + span, float(keyframe.value) + outgoing[0] * span
                result.append((keyframe, "out", self.to_screen(t, v)))
            if index > 0 and frames[index - 1].interpolation is InterpolationType.BEZIER:
                span = (keyframe.time_seconds - frames[index - 1].time_seconds) / 3.0
                t, v = keyframe.time_seconds - span, float(keyframe.value) - incoming[0] * span
                result.append((keyframe, "in", self.to_screen(t, v)))
        return result

    def _paint_keyframes(self, painter: QPainter, curve) -> None:
        accent = QColor(COLORS.get("accent", "#36E6C3"))
        selected = self.editor.selected_ids()
        for keyframe, _side, point in self._handles(curve):
            center = self.to_screen(keyframe.time_seconds, float(keyframe.value))
            painter.setPen(QPen(QColor("#B0B6BE"), 1))
            painter.drawLine(center, point)
            painter.setBrush(QColor("#B0B6BE"))
            painter.drawEllipse(point, HANDLE_RADIUS, HANDLE_RADIUS)
        for keyframe in curve.keyframes:
            center = self.to_screen(keyframe.time_seconds, float(keyframe.value))
            half = KEYFRAME_SIZE / 2
            polygon = QPolygonF([
                QPointF(center.x(), center.y() - half), QPointF(center.x() + half, center.y()),
                QPointF(center.x(), center.y() + half), QPointF(center.x() - half, center.y()),
            ])
            is_selected = keyframe.id in selected
            painter.setBrush(QColor("#FFFFFF") if is_selected else accent)
            painter.setPen(QPen(accent if is_selected else QColor("#10141A"), 1.5))
            painter.drawPolygon(polygon)

    def _paint_playhead(self, painter: QPainter, plot: QRectF) -> None:
        local = self.editor.local_playhead()
        if local is None or not (self.t0 <= local <= self.t1):
            return
        x = self.to_screen(local, 0).x()
        painter.setPen(QPen(QColor(COLORS.get("danger", "#FF5A5A")), 1))
        painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

    # -- interaction --------------------------------------------------------------------------------

    def _pick_keyframe(self, point: QPointF):
        curve = self.editor.curve()
        if curve is None:
            return None
        best, best_distance = None, PICK_DISTANCE
        for keyframe in curve.keyframes:
            center = self.to_screen(keyframe.time_seconds, float(keyframe.value))
            distance = math.hypot(center.x() - point.x(), center.y() - point.y())
            if distance <= best_distance:
                best, best_distance = keyframe, distance
        return best

    def _pick_handle(self, point: QPointF):
        curve = self.editor.curve()
        if curve is None:
            return None
        for keyframe, side, handle in self._handles(curve):
            if math.hypot(handle.x() - point.x(), handle.y() - point.y()) <= PICK_DISTANCE:
                return keyframe, side
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt
        point = event.position()
        self._press = point
        if event.button() == Qt.MiddleButton or (
            event.button() == Qt.LeftButton and event.modifiers() & Qt.AltModifier
        ):
            self._gesture = "pan"
            self._origin = {"view": (self.t0, self.t1, self.v0, self.v1)}
            return
        if event.button() != Qt.LeftButton or self.editor.curve() is None:
            return
        handle = self._pick_handle(point)
        if handle is not None:
            self._gesture = "handle"
            self._origin = {"keyframe": handle[0], "side": handle[1]}
            return
        keyframe = self._pick_keyframe(point)
        additive = bool(event.modifiers() & Qt.ShiftModifier)
        if keyframe is not None:
            ids = self.editor.selected_ids()
            if additive:
                ids = ids ^ {keyframe.id}
            elif keyframe.id not in ids:
                ids = {keyframe.id}
            self.editor.select_ids(ids)
            curve = self.editor.curve()
            self._gesture = "move"
            self._origin = {
                "anchor": keyframe,
                "values": {k.id: float(k.value) for k in curve.keyframes if k.id in ids},
                "anchor_time": keyframe.time_seconds,
            }
            return
        if not additive:
            self.editor.select_ids(set())
        self._gesture = "rubber"
        self._rubber = QRectF(point, point)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt
        point = event.position()
        if self._gesture == "pan":
            t0, t1, v0, v1 = self._origin["view"]
            plot = self._plot()
            dt = (point.x() - self._press.x()) / plot.width() * (t1 - t0)
            dv = (point.y() - self._press.y()) / plot.height() * (v1 - v0)
            self.t0, self.t1, self.v0, self.v1 = t0 - dt, t1 - dt, v0 + dv, v1 + dv
            self.update()
        elif self._gesture == "rubber" and self._rubber is not None:
            self._rubber = QRectF(self._press, point).normalized()
            self.update()
        elif self._gesture == "move":
            self.editor.drag_keyframes(self._origin, self._press, point)
        elif self._gesture == "handle":
            t, v = self.to_curve(point)
            self.editor.drag_handle(self._origin["keyframe"], self._origin["side"], t, v)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt
        gesture, self._gesture = self._gesture, None
        if gesture == "rubber" and self._rubber is not None:
            curve = self.editor.curve()
            if curve is not None:
                inside = {
                    k.id for k in curve.keyframes
                    if self._rubber.contains(self.to_screen(k.time_seconds, float(k.value)))
                }
                additive = bool(event.modifiers() & Qt.ShiftModifier)
                self.editor.select_ids((self.editor.selected_ids() | inside) if additive else inside)
            self._rubber = None
            self.update()
        elif gesture in ("move", "handle"):
            self.editor.finish_gesture("Modifier les images-clés" if gesture == "move" else "Modifier les tangentes")

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt
        """Double clic sur la courbe : nouvelle image-clé, sans changer l'animation."""
        if event.button() == Qt.LeftButton and self._pick_keyframe(event.position()) is None:
            t, _v = self.to_curve(event.position())
            self.editor.add_keyframe_at(t)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt
        """Molette : zoom horizontal ; Maj+molette : zoom vertical (autour du pointeur)."""
        factor = 0.85 if event.angleDelta().y() > 0 else 1 / 0.85
        t, v = self.to_curve(event.position())
        if event.modifiers() & Qt.ShiftModifier:
            self.v0, self.v1 = v + (self.v0 - v) * factor, v + (self.v1 - v) * factor
        else:
            self.t0, self.t1 = t + (self.t0 - t) * factor, t + (self.t1 - t) * factor
        self.update()
        event.accept()


class GraphEditorWindow(QWidget):
    """Fenêtre de l'éditeur de courbes, synchronisée avec la fenêtre principale."""

    def __init__(self, host, parent=None) -> None:
        super().__init__(parent, Qt.Window)
        self.host = host
        self.clip_id: str | None = None
        self.property_id: str | None = None
        self._loading = False
        self._gesture_open = False
        self.setObjectName("graphEditor")
        # Fenêtre indépendante : elle reprend explicitement les couleurs du thème.
        self.setStyleSheet(
            f"QWidget#graphEditor {{ background: {COLORS.get('panel', '#141A1F')}; }}"
            f"QWidget#graphEditor QLabel {{ color: {COLORS.get('text', '#E6E8EB')}; }}"
        )
        # Jamais plus grande que l'écran qui la porte (elle est modeste : 820 × 420).
        screen = self.screen() or QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        self.resize(min(820, available.width() - 40) if available else 820, min(420, available.height() - 80) if available else 420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        bar = QHBoxLayout()
        self.property_label = QLabel()
        self.property_combo = make_shrinkable(QComboBox(), 5)
        self.property_combo.currentIndexChanged.connect(self._on_property_selected)
        self.interpolation_label = QLabel()
        self.interpolation_combo = make_shrinkable(QComboBox(), 5)
        for kind in InterpolationType:
            self.interpolation_combo.addItem("", userData=kind.value)
        self.interpolation_combo.activated.connect(self._on_interpolation)
        self.tangent_label = QLabel()
        self.tangent_combo = make_shrinkable(QComboBox(), 5)
        for key in ("linked", "broken", "auto"):
            self.tangent_combo.addItem("", userData=key)
        self.tangent_combo.activated.connect(self._on_tangent_mode)
        self.frame_all_button = QPushButton()
        self.frame_all_button.clicked.connect(self.frame_all)
        self.frame_selected_button = QPushButton()
        self.frame_selected_button.clicked.connect(self.frame_selected)
        for widget in (self.property_label, self.property_combo, self.interpolation_label,
                       self.interpolation_combo, self.tangent_label, self.tangent_combo):
            bar.addWidget(widget)
        bar.addStretch(1)
        bar.addWidget(self.frame_all_button)
        bar.addWidget(self.frame_selected_button)
        layout.addLayout(bar)
        self.canvas = CurveCanvas(self)
        layout.addWidget(self.canvas, 1)
        fields = QHBoxLayout()
        self.time_label = QLabel()
        self.time_spin = QDoubleSpinBox()
        self.time_spin.setDecimals(3)
        self.time_spin.setRange(0.0, 1e6)
        self.time_spin.editingFinished.connect(self._on_time_edited)
        self.value_label = QLabel()
        self.value_spin = QDoubleSpinBox()
        self.value_spin.setDecimals(3)
        self.value_spin.editingFinished.connect(self._on_value_edited)
        self.status_label = ElidedLabel()  # le message d'aide est long : tronqué, il ne fixe plus la largeur minimale
        self.status_label.setStyleSheet(label_style(12, "muted", 500))
        for widget in (self.time_label, self.time_spin, self.value_label, self.value_spin):
            fields.addWidget(widget)
        fields.addStretch(1)
        fields.addWidget(self.status_label)
        layout.addLayout(fields)
        self.retranslate()
        callback = self._on_language_changed
        i18n.subscribe(callback)
        self.destroyed.connect(lambda *_: i18n.unsubscribe(callback))

    # -- état ------------------------------------------------------------------------------------------

    def _clip(self):
        if self.clip_id is None:
            return None
        try:
            return find_clip(self.host.project, self.clip_id)
        except KeyError:
            return None

    def target(self):
        return get_target(self.property_id)

    def curve(self):
        clip = self._clip()
        if clip is None or self.property_id is None:
            return None
        return self.target().curve(clip)

    def selected_ids(self) -> set[str]:
        return {
            ref.keyframe_id for ref in self.host.keyframe_selection
            if ref.clip_id == self.clip_id and ref.property_id == self.property_id
        }

    def selected_refs(self) -> list[KeyframeRef]:
        return [KeyframeRef(self.clip_id, self.property_id, kid) for kid in sorted(self.selected_ids())]

    def local_playhead(self) -> float | None:
        clip = self._clip()
        if clip is None:
            return None
        return float(self.host.playhead_seconds) - float(clip.timeline_start)

    # -- synchronisation avec la fenêtre principale ------------------------------------------------

    def refresh(self) -> None:
        """Suit le clip sélectionné ; cadre la vue à l'ouverture d'une nouvelle courbe."""
        clip = self.host._animation_clip()
        new_clip = clip.id if clip is not None else None
        changed = new_clip != self.clip_id
        self.clip_id = new_clip
        self._loading = True
        try:
            self.property_combo.clear()
            if clip is not None:
                for pid in self.host._animation_properties(clip):
                    target = get_target(pid)
                    marker = "● " if target.get_keyframes(clip) else ""
                    self.property_combo.addItem(marker + _label(target), userData=pid)
                wanted = self.property_id or self.host.active_animation_property
                if wanted is None or self.property_combo.findData(wanted) < 0:
                    animated = [pid for pid in self.host._animation_properties(clip) if get_target(pid).get_keyframes(clip)]
                    wanted = animated[0] if animated else self.property_combo.itemData(0)
                self.property_combo.setCurrentIndex(max(0, self.property_combo.findData(wanted)))
                self.property_id = self.property_combo.currentData()
        finally:
            self._loading = False
        if changed:
            self.frame_all()
        self._refresh_fields()
        self.canvas.update()

    def set_property(self, property_id: str) -> None:
        index = self.property_combo.findData(property_id)
        if index >= 0:
            self.property_combo.setCurrentIndex(index)

    def set_selection(self, _refs) -> None:
        self._refresh_fields()
        self.canvas.update()

    def update_playhead(self) -> None:
        self.canvas.update()

    def _on_property_selected(self, _index: int) -> None:
        if self._loading:
            return
        self.property_id = self.property_combo.currentData()
        if self.property_id:
            self.host.active_animation_property = self.property_id
        self.frame_all()
        self._refresh_fields()

    def _refresh_fields(self) -> None:
        curve = self.curve()
        selected = [k for k in curve.keyframes if k.id in self.selected_ids()] if curve is not None else []
        single = selected[0] if len(selected) == 1 else None
        for widget in (self.time_spin, self.value_spin):
            widget.setEnabled(single is not None)
        self.interpolation_combo.setEnabled(bool(selected))
        self.tangent_combo.setEnabled(bool(selected))
        self.frame_selected_button.setEnabled(bool(selected))
        if self.property_id:
            spec = self.target().spec
            self.value_spin.setRange(spec.minimum if spec.minimum is not None else -1e9,
                                     spec.maximum if spec.maximum is not None else 1e9)
        if single is not None:
            for widget, value in ((self.time_spin, single.time_seconds), (self.value_spin, float(single.value))):
                widget.blockSignals(True)
                widget.setValue(value)
                widget.blockSignals(False)
            self.interpolation_combo.setCurrentIndex(self.interpolation_combo.findData(single.interpolation.value))
            mode = "auto" if single.in_slope is None and single.out_slope is None else single.tangent_mode.value
            self.tangent_combo.setCurrentIndex(self.tangent_combo.findData(mode))
        if self._clip() is None:
            self.status_label.setText(i18n.translate("graph.no_clip"))
        elif curve is not None and not curve:
            self.status_label.setText(i18n.translate("graph.not_animated"))
        else:
            self.status_label.setText(i18n.translate("graph.hint"))

    # -- sélection et cadrage ----------------------------------------------------------------------

    def select_ids(self, ids: set[str]) -> None:
        self.host.set_keyframe_selection({KeyframeRef(self.clip_id, self.property_id, kid) for kid in ids})

    def frame_all(self) -> None:
        curve = self.curve()
        clip = self._clip()
        if curve is None or clip is None:
            return
        if not curve:
            static = float(self.target().get_static(clip))
            self.canvas.frame([0.0, float(clip.duration)], [static])
            return
        spec = self.target().spec
        samples = [i * clip.duration / 100 for i in range(101)]
        values = [float(spec.clamp(curve.evaluate(t))) for t in samples]
        self.canvas.frame([0.0, float(clip.duration), *curve.times], values)

    def frame_selected(self) -> None:
        curve = self.curve()
        if curve is None:
            return
        chosen = [k for k in curve.keyframes if k.id in self.selected_ids()]
        if chosen:
            self.canvas.frame([k.time_seconds for k in chosen], [float(k.value) for k in chosen])

    # -- éditions (en direct ; une entrée d'historique par geste) ------------------------------------

    def _edited(self, label: str, *, record: bool) -> None:
        if not record:
            self._gesture_open = True
        self.host.on_graph_edit(label, self.clip_id, record=record)

    def drag_keyframes(self, origin: dict, press: QPointF, point: QPointF) -> None:
        t_press, v_press = self.canvas.to_curve(press)
        t_now, v_now = self.canvas.to_curve(point)
        refs = self.selected_refs()
        if not refs:
            return
        fps = float(getattr(self.host.project, "fps", 0.0) or 30.0)
        anchor = origin["anchor"]
        current = next((k for k in self.curve().keyframes if k.id == anchor.id), None)
        if current is None:
            return
        desired = origin["anchor_time"] + (t_now - t_press)
        move_keyframes(self.host.project, refs, desired - current.time_seconds, fps=fps)
        dv = v_now - v_press
        set_keyframe_values(self.host.project, {
            KeyframeRef(self.clip_id, self.property_id, kid): value + dv
            for kid, value in origin["values"].items()
        })
        self._edited("Modifier les images-clés", record=False)

    def drag_handle(self, keyframe, side: str, t: float, v: float) -> None:
        dt = t - keyframe.time_seconds
        if side == "out" and dt <= 1e-6 or side == "in" and dt >= -1e-6:
            return
        slope = (v - float(keyframe.value)) / dt
        ref = KeyframeRef(self.clip_id, self.property_id, keyframe.id)
        kwargs = {"out_slope": slope} if side == "out" else {"in_slope": slope}
        set_tangents(self.host.project, ref, **kwargs)
        self._edited("Modifier les tangentes", record=False)

    def finish_gesture(self, label: str) -> None:
        if self._gesture_open:
            self._gesture_open = False
            self._edited(label, record=True)

    def add_keyframe_at(self, local_time: float) -> None:
        clip = self._clip()
        if clip is None or self.property_id is None:
            return
        t = min(max(0.0, local_time), float(clip.duration))
        add_keyframe(self.host.project, clip.id, self.property_id, self.host._snap_local(clip, t))
        self._edited("Ajouter une image-clé", record=True)

    def _on_interpolation(self, _index: int) -> None:
        refs = self.selected_refs()
        if refs and set_interpolation(self.host.project, refs, self.interpolation_combo.currentData()):
            self._edited("Changer l'interpolation", record=True)

    def _on_tangent_mode(self, _index: int) -> None:
        mode = self.tangent_combo.currentData()
        refs = self.selected_refs()
        for ref in refs:
            if mode == "auto":
                set_tangents(self.host.project, ref, auto=True)
            else:
                set_tangents(self.host.project, ref, mode=TangentMode(mode))
        if refs:
            self._edited("Modifier les tangentes", record=True)

    def _on_time_edited(self) -> None:
        refs = self.selected_refs()
        current = next((k for k in self.curve().keyframes if k.id in self.selected_ids()), None) if refs else None
        if current is None or abs(self.time_spin.value() - current.time_seconds) < 1e-7:
            return
        move_keyframes(self.host.project, refs, self.time_spin.value() - current.time_seconds,
                       fps=float(getattr(self.host.project, "fps", 0.0) or 30.0))
        self._edited("Déplacer une image-clé", record=True)

    def _on_value_edited(self) -> None:
        refs = self.selected_refs()
        if len(refs) != 1:
            return
        set_keyframe_values(self.host.project, {refs[0]: self.value_spin.value()})
        self._edited("Modifier une image-clé", record=True)

    # -- textes ------------------------------------------------------------------------------------------

    def _on_language_changed(self, *_args) -> None:
        self.retranslate()

    def retranslate(self) -> None:
        tr = i18n.translate
        self.setWindowTitle(tr("graph.title"))
        self.property_label.setText(tr("graph.property"))
        self.interpolation_label.setText(tr("graph.interpolation"))
        self.tangent_label.setText(tr("graph.tangents"))
        for index in range(self.interpolation_combo.count()):
            self.interpolation_combo.setItemText(index, tr(f"animation.interpolation.{self.interpolation_combo.itemData(index)}"))
        for index in range(self.tangent_combo.count()):
            self.tangent_combo.setItemText(index, tr(f"graph.tangents.{self.tangent_combo.itemData(index)}"))
        self.frame_all_button.setText(tr("graph.frame_all"))
        self.frame_selected_button.setText(tr("graph.frame_selected"))
        self.time_label.setText(tr("graph.time"))
        self.value_label.setText(tr("graph.value"))
        self._refresh_fields() if self.property_id else None


def _label(target) -> str:
    text = i18n.translate(target.spec.label_key)
    return target.id if text.startswith("[") else text


def _ticks(low: float, high: float, count: int) -> list[float]:
    span = high - low
    if span <= 0:
        return []
    raw = span / max(1, count)
    magnitude = 10 ** math.floor(math.log10(raw))
    step = min((m * magnitude for m in (1, 2, 5, 10)), key=lambda s: abs(s - raw))
    first = math.ceil(low / step) * step
    return [first + i * step for i in range(int((high - first) / step) + 1)]


def _format(value: float) -> str:
    text = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


__all__ = ["CurveCanvas", "GraphEditorWindow"]
