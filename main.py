import sys

from core.diagnostics_log import install_diagnostics


def main():
    # Avant toute autre chose : une exception, même au chargement de Qt ou de l'interface, doit laisser une trace
    # dans le journal. Qt et la fenêtre ne sont donc importés qu'ici, une fois le journal installé.
    install_diagnostics()
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
        return 0
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
