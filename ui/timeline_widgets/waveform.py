"""Forme d'onde d'un clip audio dans la timeline : le son **que le clip fait entendre**, colonne par colonne.

L'enveloppe de crête du fichier (:mod:`core.audio_envelope`, une par fichier, en cache) est lue sur la portion de média du
clip, d'après le temps du clip (``time_map_for_clip`` : vitesse, courbe de vitesse, sens) et à son gain : au zoom
maximal une colonne de pixels vaut moins d'une crête (2 ms), au plus large plusieurs secondes.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPolygonF

from ui.timeline_widgets.common import _current_palette


def paint_waveform(widget, painter: QPainter, parent, envelope, exposed: QRect, *, title_band: int) -> None:
    """Dessine la forme d'onde du clip de ``widget`` dans la bande ``exposed``, sous la bande du titre.

    Seule la bande exposée est calculée ; le polygone est gardé sur le widget tant que ni le clip, ni sa largeur, ni
    l'enveloppe ne changent (la lecture repeint sans recalculer)."""
    clip = parent.clip_model(widget.view.id)
    width = widget.width()
    x0, x1 = max(0, exposed.left()), min(width, exposed.right() + 1)
    if clip is None or x1 <= x0:
        return
    region = max(8.0, widget.height() - title_band - 3)
    key = (id(envelope), width, x0, x1, region, clip.source_in, clip.source_out, clip.gain_db,
           repr(clip.time_remapping), repr(tuple(getattr(clip, "animation", ()) or ())))
    cached = getattr(widget, "_waveform_polygon", None)
    if cached is None or cached[0] != key:
        cached = (key, waveform_shape(widget, clip, envelope, x0, x1, region, title_band=title_band))
        widget._waveform_polygon = cached
    if cached[1] is None:
        return
    # La forme d'onde accompagne le clip sans le dominer : la couleur du thème, translucide, et seulement sous la bande du
    # nom (le titre du clip reste lisible, rien ne passe derrière).
    wave = QColor(_current_palette().clip_audio_wave)
    wave.setAlpha(120)
    painter.setPen(Qt.NoPen)
    painter.setBrush(wave)
    painter.drawPolygon(cached[1])


def waveform_shape(widget, clip, envelope, x0: int, x1: int, region: float, *, title_band: int = 16) -> QPolygonF | None:
    """Polygone de la forme d'onde des colonnes ``x0`` à ``x1`` du widget du clip (``None`` : rien à dessiner)."""
    import numpy as np

    from core.audio_envelope import column_peaks
    from core.time_map import time_map_for_clip

    timing = time_map_for_clip(clip)
    duration = float(timing.duration)
    if duration <= 0.0:
        return None
    width = float(widget.width())
    columns = np.arange(x0, x1 + 1, dtype=np.float64)
    # Le temps du clip est lu toutes les 4 colonnes puis interpolé : une courbe de vitesse ne change pas de pente à
    # l'échelle de 4 pixels, et un clip de 2 000 pixels ne coûte que 500 lectures.
    samples = np.unique(np.append(columns[::4], columns[-1]))
    times = [timing.source_time(min(duration, max(0.0, x / width * duration))) for x in samples]
    edges = np.interp(columns, samples, times)
    peaks = column_peaks(envelope, edges.tolist(), gain=10.0 ** (float(clip.gain_db) / 20.0))
    if not peaks or max(peaks) <= 0.0:
        return None
    mid = title_band + region / 2.0
    half = [max(0.5, peak * region * 0.45) for peak in peaks]
    top = [QPointF(x0 + index + 0.5, mid - h) for index, h in enumerate(half)]
    bottom = [QPointF(x0 + index + 0.5, mid + h) for index, h in reversed(list(enumerate(half)))]
    return QPolygonF(top + bottom)


__all__ = ["paint_waveform", "waveform_shape"]
