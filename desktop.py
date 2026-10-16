"""Start Saga's optional Windows desktop interface."""

import sys

from PySide6.QtWidgets import QApplication

from saga.desktop.window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Saga")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
