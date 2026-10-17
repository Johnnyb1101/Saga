"""Read-only desktop history and reports."""

import sqlite3

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


def _label(text=""):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _value(value, fallback="—"):
    return str(value) if value is not None and value != "" else fallback


def _number(row):
    quantity = row.get("quantity")
    if quantity is None:
        return "—"
    return f"{quantity} {_value(row.get('measure'), 'units')}"


def _table(headings, name):
    table = QTableWidget(0, len(headings))
    table.setHorizontalHeaderLabels(headings)
    table.setAccessibleName(name)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setSortingEnabled(False)
    table.setWordWrap(True)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.horizontalHeader().setDefaultSectionSize(120)
    table.horizontalHeader().setStretchLastSection(True)
    table.setMinimumHeight(135)
    return table


def _fill(table, rows):
    table.setRowCount(len(rows))
    for row_index, values in enumerate(rows):
        for column, value in enumerate(values):
            item = QTableWidgetItem(_value(value, ""))
            item.setToolTip(_value(value, ""))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row_index, column, item)


class HistoryWidget(QWidget):
    """A report for an explicitly chosen historical scope."""

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self._accomplishments = []

        today = QDate.currentDate()
        self.use_from = QCheckBox("From")
        self.use_from.setChecked(True)
        self.from_date = QDateEdit(QDate(today.year(), today.month(), 1))
        self.from_date.setDisplayFormat("yyyy-MM-dd")
        self.from_date.setCalendarPopup(True)
        self.from_date.setAccessibleName("From date, inclusive")
        self.use_to = QCheckBox("Through")
        self.use_to.setChecked(True)
        self.to_date = QDateEdit(today)
        self.to_date.setDisplayFormat("yyyy-MM-dd")
        self.to_date.setCalendarPopup(True)
        self.to_date.setAccessibleName("Through date, inclusive")
        self.use_from.toggled.connect(self.from_date.setEnabled)
        self.use_to.toggled.connect(self.to_date.setEnabled)

        self.category_filter = QComboBox()
        self.category_filter.setAccessibleName("History category filter")
        self.category_filter.addItem("All categories", None)
        self.role_filter = QComboBox()
        self.role_filter.setAccessibleName("History role filter")
        self.role_filter.addItem("All roles", None)
        self.this_month_button = QPushButton("This month")
        self.this_year_button = QPushButton("This year")
        self.all_time_button = QPushButton("All time")
        self.show_report_button = QPushButton("Show report")
        self.this_month_button.clicked.connect(self._this_month)
        self.this_year_button.clicked.connect(self._this_year)
        self.all_time_button.clicked.connect(self._all_time)
        self.show_report_button.clicked.connect(self.refresh)

        self.scope_label = _label("Choose a range, then show the report.")
        self.scope_label.setAccessibleName("Applied report scope")
        self.status_label = _label()
        self.status_label.setAccessibleName("History report status")
        self.summary_label = _label()
        self.summary_label.setAccessibleName("History totals")
        self.measures_table = _table(("Measure", "Total", "Occasions"), "Totals by measure")
        self.categories_table = _table(("Category", "Completions", "With a number", "Flagged"), "Totals by category")
        for table in (self.measures_table, self.categories_table):
            table.horizontalHeader().setStretchLastSection(False)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.accomplishments_table = _table(
            ("Date", "Accomplishment", "Category", "Role", "Number", "Review"),
            "Historical accomplishments",
        )
        self.accomplishments_table.horizontalHeader().setStretchLastSection(False)
        self.accomplishments_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self.accomplishments_table.setColumnWidth(0, 160)
        self.accomplishments_table.setToolTip("Select an accomplishment to read its saved details")
        self.accomplishments_table.itemClicked.connect(self._open_item)
        self.accomplishments_table.itemActivated.connect(self._open_item)

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content = QWidget()
        body = QVBoxLayout(content)
        range_group = QGroupBox("Report range")
        range_layout = QVBoxLayout(range_group)
        quick = QHBoxLayout()
        for button in (self.this_month_button, self.this_year_button, self.all_time_button):
            quick.addWidget(button)
        range_layout.addLayout(quick)
        dates = QHBoxLayout()
        dates.addWidget(self.use_from)
        dates.addWidget(self.from_date)
        dates.addWidget(self.use_to)
        dates.addWidget(self.to_date)
        range_layout.addLayout(dates)
        filters = QHBoxLayout()
        filters.addWidget(_label("Category"))
        filters.addWidget(self.category_filter)
        filters.addWidget(_label("Role"))
        filters.addWidget(self.role_filter)
        range_layout.addLayout(filters)
        range_layout.addWidget(self.show_report_button)
        body.addWidget(range_group)
        body.addWidget(self.scope_label)
        body.addWidget(self.status_label)
        body.addWidget(self.summary_label)
        body.addWidget(_label("Numbers by measure (each unit has its own total)"))
        body.addWidget(self.measures_table)
        body.addWidget(_label("By category"))
        body.addWidget(self.categories_table)
        body.addWidget(_label("Accomplishments — select a row for saved details"))
        body.addWidget(self.accomplishments_table)
        body.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _this_month(self):
        today = QDate.currentDate()
        self.use_from.setChecked(True)
        self.use_to.setChecked(True)
        self.from_date.setDate(QDate(today.year(), today.month(), 1))
        self.to_date.setDate(today)
        self.refresh()

    def _this_year(self):
        today = QDate.currentDate()
        self.use_from.setChecked(True)
        self.use_to.setChecked(True)
        self.from_date.setDate(QDate(today.year(), 1, 1))
        self.to_date.setDate(today)
        self.refresh()

    def _all_time(self):
        self.use_from.setChecked(False)
        self.use_to.setChecked(False)
        self.refresh()

    def _reload_filters(self, available):
        for combo, names, all_label in (
            (self.category_filter, available["categories"], "All categories"),
            (self.role_filter, available["roles"], "All roles"),
        ):
            selected = combo.currentData()
            combo.clear()
            combo.addItem(all_label, None)
            for name in names:
                combo.addItem(name, name)
            index = combo.findData(selected)
            combo.setCurrentIndex(max(index, 0))

    def _clear_report(self):
        self._accomplishments = []
        self.summary_label.clear()
        for table in (self.measures_table, self.categories_table, self.accomplishments_table):
            table.setRowCount(0)

    def refresh(self):
        """Apply the visible controls and replace the full displayed snapshot."""
        since = self.from_date.date().toString("yyyy-MM-dd") if self.use_from.isChecked() else None
        until = self.to_date.date().toString("yyyy-MM-dd") if self.use_to.isChecked() else None
        if since is not None and until is not None and since > until:
            self._clear_report()
            self.scope_label.setText("No report applied.")
            self.status_label.setText("From must be on or before Through.")
            return
        try:
            self._reload_filters(self.service.history_filters())
            report = self.service.history_report(
                since=since,
                until=until,
                category=self.category_filter.currentData(),
                duty=self.role_filter.currentData(),
            )
        except (ValueError, OSError, sqlite3.Error) as exc:
            self._clear_report()
            self.scope_label.setText("No report applied.")
            self.status_label.setText(f"Could not load history: {exc}")
            return

        self._clear_report()
        from_text = _value(report.get("since"), "beginning")
        until_text = _value(report.get("until"), "no end date")
        category = _value(report.get("category"), "all categories")
        role = _value(report.get("duty"), "all roles")
        self.scope_label.setText(f"Report: {from_text} through {until_text} (inclusive) · {category} · {role}")
        summary = report["summary"]
        self.summary_label.setText(
            f"{summary['completions']} completions · "
            f"{summary['with_a_number']} with a number · "
            f"{summary['flagged']} flagged · "
            f"{summary['review_counting']} count toward reviews"
        )
        _fill(self.measures_table, [
            (row["measure"], row["total"], row["occasions"])
            for row in report["measures"]
        ])
        _fill(self.categories_table, [
            (row["category"], row["completions"], row["with_a_number"], row["flagged"])
            for row in report["categories"]
        ])
        self._accomplishments = list(report["accomplishments"])
        _fill(self.accomplishments_table, [
            (
                row.get("completed_at"),
                row.get("outcome") or row.get("task_title") or "Untitled accomplishment",
                row.get("category"),
                row.get("duty"),
                _number(row),
                "Flagged" if row.get("flagged") else "",
            )
            for row in self._accomplishments
        ])
        self.status_label.setText(
            "No accomplishments in this range." if not self._accomplishments else ""
        )

    def _open_item(self, item):
        row_index = item.row()
        if 0 <= row_index < len(self._accomplishments):
            dialog = self._detail_dialog(self._accomplishments[row_index])
            dialog.exec()
            dialog.deleteLater()

    def _detail_dialog(self, row):
        """Build a plain-text view of the saved completion snapshot."""
        dialog = QDialog(self)
        dialog.setWindowTitle("Accomplishment details")
        layout = QVBoxLayout(dialog)
        scroll = QScrollArea(dialog)
        scroll.setWidgetResizable(True)
        content = QWidget(scroll)
        form = QFormLayout(content)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        outcome = QPlainTextEdit(_value(row.get("outcome"), "No result details saved."))
        outcome.setReadOnly(True)
        outcome.setAccessibleName("Saved result details")
        outcome.setMinimumHeight(90)
        form.addRow("Result", outcome)
        for title, value in (
            ("Completed", row.get("completed_at")),
            ("Task", row.get("task_title")),
            ("Category", row.get("category")),
            ("Role", row.get("duty")),
            ("Number", _number(row)),
            ("Measurement", {
                "measured": "Number recorded",
                "not_applicable": "No number needed",
                "unknown": "Number to add later",
                "unspecified": "Not recorded",
            }.get(row.get("measurement_status"), "Not recorded")),
            ("Flagged for review", "Yes" if row.get("flagged") else "No"),
            ("Counts toward reviews", "Yes" if row.get("review_counting") else "No"),
            ("Saved due date", row.get("due_date")),
            ("Project", row.get("project_name")),
        ):
            form.addRow(title, _label(_value(value)))
        scroll.setWidget(content)
        layout.addWidget(scroll)
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.resize(560, 440)
        return dialog
