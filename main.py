import sys

from core.diagnostics_log import install_diagnostics
from core.process_supervisor import helper_mode_exit_code


def main():
    # Processus auxiliaire de la supervision des enfants (gardien, enfant d'auto-contrôle) : l'application se
    # relance elle-même avec un argument dédié, en source comme gelée. Traité avant le journal et avant Qt.
    helper = helper_mode_exit_code(sys.argv)
    if helper is not None:
        return helper
    # Avant toute autre chose : une exception, même au chargement de Qt ou de l'interface, doit laisser une trace
    # dans le journal. Qt et la fenêtre ne sont donc importés qu'ici, une fois le journal installé.
    install_diagnostics()
    # FFmpeg laissés par une instance morte que ni son gardien ni son objet Job n'ont pu arrêter.
    from core.process_supervisor import sweep_dead_instances

    sweep_dead_instances()
    from PySide6.QtWidgets import QApplication

    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    if "--smoke-test" in sys.argv:
        app.processEvents()
        window.close()
        app.processEvents()
        # Le tracking dépend de numpy (extensions C) : vérifié dans l'application construite.
        from core.tracking_match import self_check

        problem = self_check()
        if problem:
            print(f"Smoke test : tracking indisponible — {problem}", file=sys.stderr)
            return 1
        # Aperçu GPU : les shaders compilés doivent être embarqués et lisibles.
        from ui.gpu_preview import gpu_self_check

        problem = gpu_self_check()
        if problem:
            print(f"Smoke test : aperçu GPU indisponible — {problem}", file=sys.stderr)
            return 1
        # Supervision des FFmpeg : gardien (macOS, Linux) ou objet Job (Windows) réellement déclenché.
        from core.process_supervisor import supervision_self_check

        problem = supervision_self_check()
        if problem:
            print(f"Smoke test : supervision des processus indisponible — {problem}", file=sys.stderr)
            return 1
        return 0
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
