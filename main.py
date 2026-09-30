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
        return 0
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
