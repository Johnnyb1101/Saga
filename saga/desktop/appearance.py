"""Shared desktop headings and the bundled calendar icon."""

from pathlib import Path

from PySide6.QtGui import QIcon

ICON_PATH = Path(__file__).resolve().parent / 'assets' / 'saga-calendar.ico'


def app_icon():
    return QIcon(str(ICON_PATH))


def heading(widget, extra_points=3):
    font = widget.font()
    font.setBold(True)
    font.setPointSizeF(max(font.pointSizeF(), 9) + extra_points)
    widget.setFont(font)


DESKTOP_STYLE = '''
QLabel#addTaskTitle { font-weight: bold; font-size: 16pt; }
QGroupBox {
    font-weight: bold;
    font-size: 12pt;
    margin-top: 14px;
    padding-top: 12px;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
QTabBar::tab { font-weight: bold; padding: 8px 16px; }
QHeaderView::section { font-weight: bold; padding: 5px; }
'''
