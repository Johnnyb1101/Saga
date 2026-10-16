"""Start Saga's optional Windows desktop interface."""

import sys

from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QApplication

from saga.desktop.appearance import app_icon
from saga.desktop.window import MainWindow


def main():
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        QStandardPaths.setTestModeEnabled(True)
        app = QApplication([sys.argv[0]])
        app.setApplicationName("Saga")
        app.setWindowIcon(app_icon())
        from saga.desktop.smoke import run_smoke
        return run_smoke(app, sys.argv[2])
    app = QApplication(sys.argv)
    app.setApplicationName("Saga")
    app.setWindowIcon(app_icon())
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
