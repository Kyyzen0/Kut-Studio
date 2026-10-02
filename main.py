import sys

from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def main():
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
        return 0
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
