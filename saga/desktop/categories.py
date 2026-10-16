"""Button-based category selection and archive management for entry forms."""

import sqlite3

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

ERRORS = (ValueError, OSError, sqlite3.Error)


def _message(text):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class CategoryPicker(QWidget):
    """A category button with the small part of the combo API used by entry forms."""

    currentIndexChanged = Signal(int)
    categoriesChanged = Signal()

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self._names = []
        self._index = -1
        self._initialized = False
        self.button = QPushButton("Choose category")
        self.button.setAccessibleName("Choose category")
        self.button.setMaximumWidth(320)
        self.button.clicked.connect(self.choose)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.button)

    def clear(self):
        self._names.clear()
        self.setCurrentIndex(-1)

    def addItem(self, label, data=None):
        self._names.append(data if data is not None else label)
        if self._index < 0 and len(self._names) == 1:
            self.setCurrentIndex(0)

    def count(self):
        return len(self._names)

    def currentData(self):
        return self._names[self._index] if 0 <= self._index < len(self._names) else None

    def currentIndex(self):
        return self._index

    def findData(self, data):
        try:
            return self._names.index(data)
        except ValueError:
            return -1

    def setCurrentIndex(self, index):
        if index < 0 or index >= len(self._names):
            index = -1
        if self._index == index:
            return
        self._index = index
        self._update_button()
        self.currentIndexChanged.emit(index)

    def _update_button(self):
        name = self.currentData()
        full = f"Category: {name}" if name is not None else "Choose category"
        self.button.setText(self.button.fontMetrics().elidedText(full, Qt.TextElideMode.ElideRight, 290))
        self.button.setToolTip(full)

    def set_categories(self, names, *, preserve=True):
        selected = self.currentData() if preserve else None
        old_index = self._index
        self._names = list(names)
        if selected in self._names:
            self._index = self._names.index(selected)
        elif preserve and self._initialized:
            self._index = -1
        else:
            self._index = 0 if self._names else -1
        self._initialized = True
        self._update_button()
        if self._index != old_index or selected != self.currentData():
            self.currentIndexChanged.emit(self._index)

    def choose(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Choose category")
        layout = QVBoxLayout(dialog)
        choices = QListWidget()
        choices.setAccessibleName("Available categories")
        choices.addItems(self._names)
        if self._index >= 0:
            choices.setCurrentRow(self._index)
        layout.addWidget(choices)
        row = QHBoxLayout()
        select = QPushButton("Choose")
        select.clicked.connect(dialog.accept)
        select.setEnabled(choices.currentRow() >= 0)
        choices.currentRowChanged.connect(lambda row: select.setEnabled(row >= 0))
        choices.itemDoubleClicked.connect(lambda _: dialog.accept())
        create = QPushButton("+ Create category")
        create.clicked.connect(lambda: self._create_from_chooser(dialog, choices))
        manage = QPushButton("Manage categories")
        manage.clicked.connect(lambda: self._manage_from_chooser(dialog, choices))
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(dialog.reject)
        for button in (select, create, manage, cancel):
            row.addWidget(button)
        layout.addLayout(row)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = dialog.property("chosen_name") or (choices.currentItem().text() if choices.currentItem() else None)
            self.setCurrentIndex(self.findData(name))
        dialog.deleteLater()

    def _create_from_chooser(self, chooser, choices):
        dialog = CreateCategoryDialog(self.service, chooser)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name = dialog.created_name
            self.categoriesChanged.emit()
            choices.clear()
            choices.addItems(self._names)
            choices.setCurrentRow(self.findData(name))
            chooser.setProperty("chosen_name", name)
            chooser.accept()
        dialog.deleteLater()

    def _manage_from_chooser(self, chooser, choices):
        dialog = ManageCategoriesDialog(self.service, chooser)
        dialog.exec()
        if dialog.changed:
            self.categoriesChanged.emit()
            selected = self.currentData()
            choices.clear()
            choices.addItems(self._names)
            choices.setCurrentRow(self.findData(selected))
        dialog.deleteLater()


class CreateCategoryDialog(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.created_name = None
        self.setWindowTitle("Create category")
        layout = QVBoxLayout(self)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Category name")
        self.name.setAccessibleName("Category name")
        self.reviews = QCheckBox("Include in reviews")
        self.reviews.setChecked(True)
        self.status = _message("")
        create = QPushButton("Create category")
        create.clicked.connect(self.create)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        layout.addWidget(self.name)
        layout.addWidget(self.reviews)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        row.addWidget(create)
        row.addWidget(cancel)
        layout.addLayout(row)

    def create(self):
        name = self.name.text().strip()
        try:
            export_error = self.service.create_category(name, self.reviews.isChecked())
        except ERRORS as exc:
            self.status.setText(f"Could not create category: {exc}")
            return
        self.created_name = name
        if export_error:
            QMessageBox.warning(self, "Category created", f"Category saved. Export failed: {export_error}")
        self.accept()


class ManageCategoriesDialog(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.changed = False
        self.options = []
        self.setWindowTitle("Manage categories")
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setAccessibleName("Categories")
        self.list.currentRowChanged.connect(self._selection_changed)
        self.status = _message("")
        self.action = QPushButton("Archive category")
        self.action.clicked.connect(self._act)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(self.list)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        row.addWidget(self.action)
        row.addWidget(close)
        layout.addLayout(row)
        self.refresh()

    def refresh(self, selected=None):
        try:
            self.options = self.service.category_options()
        except ERRORS as exc:
            self.status.setText(f"Could not load categories: {exc}")
            return
        self.list.clear()
        for option in self.options:
            state = "archived" if option["archived"] else "used" if option["in_use"] else "unused"
            review = "included in reviews" if option["counts_toward_review"] else "excluded from reviews"
            self.list.addItem(f"{option['name']} — {state}; {review}")
        if selected is not None:
            for index, option in enumerate(self.options):
                if option["name"] == selected:
                    self.list.setCurrentRow(index)
                    break
        self._selection_changed(self.list.currentRow())

    def _selection_changed(self, row):
        option = self.options[row] if 0 <= row < len(self.options) else None
        self.action.setEnabled(option is not None)
        if option:
            self.action.setText("Restore category" if option["archived"] else
                                "Archive category" if option["in_use"] else "Delete category")

    def _act(self):
        row = self.list.currentRow()
        if row < 0:
            return
        option = self.options[row]
        name = option["name"]
        if option["archived"]:
            operation = "restore"
        elif option["in_use"]:
            operation = "archive"
        else:
            operation = "delete"
        if operation != "restore":
            message = (f"Delete unused category '{name}'?" if operation == "delete" else
                       f"Archive '{name}'? Existing entries will keep this category.")
            answer = QMessageBox.question(
                self, f"{operation.title()} category?", message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            export_error = getattr(self.service, f"{operation}_category")(name)
        except ERRORS as exc:
            self.status.setText(f"Could not {operation} category: {exc}")
            return
        self.changed = True
        self.refresh(name)
        past = {"delete": "deleted", "archive": "archived", "restore": "restored"}[operation]
        self.status.setText(f"Category {past}. " +
                            (f"Export failed: {export_error}" if export_error else ""))
