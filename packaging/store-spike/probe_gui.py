"""Sonde à fenêtre (PySide6) : reçoit le fichier par l'événement FileOpen (comme le Finder) et lance ``file_tests``."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QApplication

import probe


class Filter(QObject):
    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() == QEvent.Type.FileOpen:
            probe.out("evenement_FileOpen", True, event.file())
            path = Path(event.file())
            QTimer.singleShot(1500, lambda: (probe.file_tests(path), QApplication.quit()))
            return True
        return False


app = QApplication(sys.argv)
flt = Filter(app)
app.installEventFilter(flt)
QTimer.singleShot(15000, lambda: (probe.out("evenement_FileOpen", False, "aucun en 15 s"), app.quit()))
raise SystemExit(app.exec())
