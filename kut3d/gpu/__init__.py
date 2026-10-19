"""Moteur de rendu GPU de Kut-3D (phase 2b d'ADR-0009) : QRhi dans un `QRhiWidget` jamais affiché.

Ce paquet est le SEUL (avec `kut3d/render/images.py`) où Qt est autorisé. Importer `kut3d` ou `kut3d.gpu` n'importe
PAS PySide6 : seuls `renderer.py` et `qtapp.py` le font, et ils ne sont chargés qu'au premier rendu « qrhi »
(`kut3d.gpu.backend.render_lazy`) ou dans le sous-processus de la sonde (`probe_worker.py`, qui les importe à son tour).
"""

from __future__ import annotations

from kut3d.gpu.availability import (
    NATIVE,
    ProbeResult,
    clear_probe_cache,
    probe_qrhi,
    qrhi_unavailable_reason,
)

__all__ = [
    "NATIVE",
    "ProbeResult",
    "clear_probe_cache",
    "probe_qrhi",
    "qrhi_unavailable_reason",
    "shutdown",
]


def shutdown() -> None:
    """Libère le moteur GPU s'il a été créé (sans effet sinon, et sans importer Qt)."""
    import sys

    module = sys.modules.get("kut3d.gpu.renderer")
    if module is not None:
        module.shutdown()
