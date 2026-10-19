"""Choose saved accomplishments for a local EPB or CV preparation draft."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


class AccomplishmentSelectionDialog(QDialog):
    def __init__(self, records, report_format, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'Choose accomplishments for {report_format.upper()}')
        self.resize(700, 480)
        layout = QVBoxLayout(self)
        help_label = QLabel('Check the accomplishments to include in the draft. Verified totals will still cover the full selected date range and filters.')
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self.records_list = QListWidget()
        self.records_list.setAccessibleName('Accomplishments to include in draft')
        for record in records:
            title = record.get('outcome') or record.get('task_title') or 'Untitled accomplishment'
            text = f"{record['completed_at']} | {record['category']} | {record.get('duty') or 'No role assigned'} | {title}"
            item = QListWidgetItem(text, self.records_list)
            item.setData(Qt.ItemDataRole.UserRole, record['id'])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setToolTip(text)
        self.records_list.itemChanged.connect(self._update_save)
        layout.addWidget(self.records_list)
        actions = QHBoxLayout()
        select_all = QPushButton('Select all')
        select_all.clicked.connect(lambda: self._check_all(Qt.CheckState.Checked))
        clear = QPushButton('Clear selection')
        clear.clicked.connect(lambda: self._check_all(Qt.CheckState.Unchecked))
        actions.addWidget(select_all)
        actions.addWidget(clear)
        layout.addLayout(actions)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self._update_save()

    def selected_ids(self):
        return [self.records_list.item(index).data(Qt.ItemDataRole.UserRole)
                for index in range(self.records_list.count())
                if self.records_list.item(index).checkState() == Qt.CheckState.Checked]

    def _update_save(self):
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(bool(self.selected_ids()))

    def _check_all(self, state):
        self.records_list.blockSignals(True)
        try:
            for index in range(self.records_list.count()):
                self.records_list.item(index).setCheckState(state)
        finally:
            self.records_list.blockSignals(False)
        self._update_save()
