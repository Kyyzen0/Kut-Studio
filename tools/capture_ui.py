"""Capture de référence de l'interface Kut-Studio (1440 × 900).

Utilitaire de développement : construit la fenêtre avec le projet de
démonstration, sélectionne le premier clip pour peupler l'inspecteur, puis
enregistre une image PNG. Sert de preuve visuelle pour la refonte du thème.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Le script vit dans tools/ : on remonte à la racine du dépôt pour que
# ``ui`` et ``core`` soient importables.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.main_window import MainWindow  # noqa: E402

OUTPUT = os.path.join(_ROOT, "kut_studio_redesign_1440x900.png")


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.resize(1440, 900)
    # La capture de référence représente l'espace de montage principal.
    # Elle ne dépend pas de la préférence utilisateur persistée des scopes.
    window._scopes_visible = False
    window.scopes_panel.hide()
    window._viewer_host.setSizes([680, 0])
    window.show()

    def shoot_and_quit() -> None:
        timeline = window.timeline_panel
        if timeline.clip_views:
            timeline.select_clip(timeline.clip_views[0].id)
        app.processEvents()
        window.grab().save(OUTPUT)
        print(f"screenshot written: {OUTPUT}", flush=True)
        app.quit()

    QTimer.singleShot(700, shoot_and_quit)
    app.exec()
    print("event loop exited cleanly", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
