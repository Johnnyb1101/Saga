"""Window-local appearance and a small, immediate theme picker."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from saga.desktop.appearance import DESKTOP_STYLE, heading

ACCENTS = {
    'teal': ('#006b65', '#6cdbce'),
    'blue': ('#175bb5', '#8dbbff'),
    'magenta': ('#a82070', '#f59bcd'),
    'purple': ('#713bb6', '#c5a6ff'),
}


def apply_theme(window, mode, accent):
    app = QApplication.instance()
    dark = mode == 'dark' or (mode == 'system' and
            app.styleHints().colorScheme() == Qt.ColorScheme.Dark)
    background, surface = ('#202126', '#2b2d34') if dark else ('#f3f4f7', '#ffffff')
    foreground, muted = ('#f2f3f5', '#a6aab5') if dark else ('#20222a', '#656976')
    border = '#555a67' if dark else '#b9bdc8'
    selected = ACCENTS[accent][int(dark)]
    on_accent = '#171a21' if dark else '#ffffff'
    palette = QPalette(window.palette())
    for role, color in ((QPalette.ColorRole.Window, background),
                        (QPalette.ColorRole.WindowText, foreground),
                        (QPalette.ColorRole.Base, surface),
                        (QPalette.ColorRole.AlternateBase, background),
                        (QPalette.ColorRole.Text, foreground),
                        (QPalette.ColorRole.Button, surface),
                        (QPalette.ColorRole.ButtonText, foreground),
                        (QPalette.ColorRole.Highlight, selected),
                        (QPalette.ColorRole.HighlightedText, on_accent),
                        (QPalette.ColorRole.PlaceholderText, muted)):
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(muted))
    window.setStyleSheet(DESKTOP_STYLE + f'''
        QWidget {{ background-color: {background}; color: {foreground}; }}
        QWidget#taskCard {{ background-color: {surface}; border-radius: 7px; }}
        QWidget#taskCard QLabel {{ background-color: transparent; }}
        QLabel#cardDetails {{ color: {muted}; }}
        QLineEdit, QPlainTextEdit, QComboBox, QDateEdit, QAbstractItemView {{
            background-color: {surface}; color: {foreground};
            selection-background-color: {selected}; selection-color: {on_accent};
        }}
        QLineEdit, QPlainTextEdit, QComboBox, QDateEdit {{
            border: 1px solid {border}; border-radius: 4px; padding: 5px;
        }}
        QPushButton {{ background-color: {surface}; border: 1px solid {border};
            border-radius: 5px; padding: 7px 12px; }}
        QPushButton:hover, QPushButton:focus {{ border: 1px solid {selected}; }}
        QPushButton:checked, QPushButton[primary="true"], QTabBar::tab:selected {{
            background-color: {selected}; color: {on_accent};
        }}
        QPushButton:disabled, QLineEdit:disabled, QComboBox:disabled, QDateEdit:disabled {{
            background-color: {background}; color: {muted}; border-color: {border};
        }}
        QTabBar::tab {{ border: 1px solid {border}; background-color: {surface}; }}
        QCheckBox::indicator {{ width: 14px; height: 14px; border: 2px solid {border};
            border-radius: 3px; background-color: {surface}; }}
        QCheckBox::indicator:checked {{ background-color: {selected}; border-color: {selected}; }}
        QCheckBox::indicator:disabled {{ background-color: {background}; border-color: {muted}; }}
        QGroupBox {{ border: 1px solid {border}; border-radius: 6px; }}
        QGroupBox::title {{ color: {selected}; }}
        QHeaderView::section {{ background-color: {surface}; color: {foreground};
            border: 1px solid {border}; }}
        QToolTip {{ background-color: {surface}; color: {foreground}; border: 1px solid {border}; }}
    ''')
    # Apply after repolishing so a previous stylesheet cannot restore its palette.
    window.setPalette(palette)


class ThemePicker(QWidget):
    """Apply a choice immediately, retaining the saved choice if writing fails."""

    def __init__(self, current, on_change, parent=None):
        super().__init__(parent)
        self.current = dict(current)
        self.on_change = on_change
        layout = QVBoxLayout(self)
        title = QLabel('Make Saga yours')
        heading(title)
        layout.addWidget(title)
        layout.addWidget(QLabel('Appearance'))
        self.mode = QComboBox()
        self.mode.setAccessibleName('Appearance')
        for value in ('system', 'light', 'dark'):
            self.mode.addItem(value.title(), value)
        self.mode.setCurrentIndex(self.mode.findData(current['mode']))
        layout.addWidget(self.mode)
        layout.addWidget(QLabel('Accent color'))
        row = QHBoxLayout()
        self.buttons = {}
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for name, colors in ACCENTS.items():
            button = QPushButton(name.title())
            button.setCheckable(True)
            button.setAccessibleName(name.title() + ' accent')
            swatch = QPixmap(18, 18)
            swatch.fill(QColor(colors[0]))
            button.setIcon(QIcon(swatch))
            self.group.addButton(button)
            self.buttons[name] = button
            button.setChecked(name == current['accent'])
            button.clicked.connect(lambda checked=False, color=name: self._change(color))
            row.addWidget(button)
        layout.addLayout(row)
        note = QLabel('Changes appear right away and are saved on this computer.')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.mode.currentIndexChanged.connect(lambda: self._change(self.current['accent']))

    def _change(self, accent):
        choice = {'mode': self.mode.currentData(), 'accent': accent}
        if self.on_change(choice):
            self.current = choice
        else:
            self.mode.blockSignals(True)
            self.mode.setCurrentIndex(self.mode.findData(self.current['mode']))
            self.mode.blockSignals(False)
            self.buttons[self.current['accent']].setChecked(True)
