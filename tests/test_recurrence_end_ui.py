"""Optional end dates in the shared task form and Today cards."""

import os
import tempfile
import unittest
from contextlib import closing
from importlib.util import find_spec
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HAS_QT = find_spec('PySide6') is not None
if HAS_QT:
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QApplication

    from saga import db, reads, writes
    from saga.application import ArchiveService
    from saga.desktop.task_entry import AddTaskWidget
    from saga.desktop.window import TodaySection


@unittest.skipUnless(HAS_QT, 'Optional PySide6 desktop dependency is not installed')
class RecurrenceEndUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / 'archive.db'
        db.init_db(self.database)
        self.service = ArchiveService(self.database)
        self.widget = AddTaskWidget(self.service)
        self.addCleanup(self.widget.close)
        self.form = self.widget.task
        self.form.title.setText('Invented bounded schedule')
        self.form.category.setCurrentIndex(self.form.category.findData('work'))
        self.form.repeat_task.setChecked(True)
        self.form.due_date.setDate(QDate(2026, 10, 16))
        self.form.has_end_date.setChecked(True)
        self.form.end_date.setDate(QDate(2026, 10, 17))

    def test_preview_save_and_today_show_inclusive_cutoff(self):
        self.form.show_preview()
        self.assertIsNotNone(self.form._preview_draft, self.form.status.text())
        self.assertIn('2026-10-17', self.form.preview_text.toPlainText())
        self.assertIn('included', self.form.preview_text.toPlainText())
        self.form.save()
        self.assertFalse(self.form._committed, self.form.status.text())
        self.assertEqual(self.form.title.text(), "")
        self.assertIn("Saved task #", self.form.status.text())
        rows = self.service.today(on='2026-10-16')['due_today']
        section = TodaySection('Tasks', ())
        self.addCleanup(section.close)
        section.show_rows(rows)
        self.assertIn('2026-10-17', section.cards[0].details_label.text())
        with closing(db.connect(self.database)) as con:
            series = rows[0]['recurrence_id']
            writes.complete_task(con, rows[0]['id'], outcome='Finished')
            last = reads.open_occurrence(con, series)
            self.assertEqual(last['due_date'], '2026-10-17')
            writes.complete_task(con, last['id'], outcome='Finished final task')
            self.assertIsNone(reads.open_occurrence(con, series))

    def test_toggles_preserve_end_date_and_completed_mode_ignores_schedule(self):
        before = self.form.draft()
        self.widget.already_finished.setChecked(True)
        self.assertFalse(self.form.has_end_date.isEnabled())
        self.assertFalse(self.form.end_date.isEnabled())
        self.assertNotIn('end_date', self.form.draft())
        self.widget.already_finished.setChecked(False)
        self.assertEqual(self.form.draft(), before)
        self.assertTrue(self.form.end_date.isEnabled())
        self.form.has_end_date.setChecked(False)
        self.assertNotIn('end_date', self.form.draft())
        self.assertFalse(self.form.form_layout.isRowVisible(self.form.end_date))
        self.form.has_end_date.setChecked(True)
        self.assertEqual(self.form.draft(), before)
        self.form.repeat_task.setChecked(False)
        self.assertFalse(self.form.form_layout.isRowVisible(self.form.has_end_date))
        self.assertNotIn('end_date', self.form.draft())

    def test_end_before_aligned_first_due_keeps_draft_without_saving(self):
        self.form.repeat_frequency.setCurrentIndex(self.form.repeat_frequency.findData('weekly'))
        self.form.repeat_weekday.setCurrentIndex(0)  # First Monday is October 19.
        self.form.show_preview()
        self.assertIsNone(self.form._preview_draft)
        self.assertTrue(self.form.has_unsaved_draft)
        self.assertEqual(self.form.end_date.date(), QDate(2026, 10, 17))
        with closing(db.connect(self.database)) as con:
            self.assertEqual(reads.open_tasks(con), [])

    def test_edit_preserves_end_date_and_reset_clears_it(self):
        before = self.form.draft()
        self.form.show_preview()
        self.form.edit_draft()
        self.assertEqual(self.form.draft(), before)
        self.form.reset_form()
        self.assertFalse(self.form.has_end_date.isChecked())
        self.assertFalse(self.form.form_layout.isRowVisible(self.form.has_end_date))
        self.assertNotIn('end_date', self.form.draft())

    def test_save_uses_the_reviewed_end_date(self):
        self.form.show_preview()
        self.form.end_date.setDate(QDate(2026, 12, 31))
        self.form.save()
        self.assertFalse(self.form._committed, self.form.status.text())
        self.assertEqual(self.form.title.text(), "")
        self.assertIn("Saved task #", self.form.status.text())
        with closing(db.connect(self.database)) as con:
            task = reads.open_tasks(con)[0]
            self.assertEqual(reads.recurrence_details(con, task['recurrence_id'])['end_date'],
                             '2026-10-17')


if __name__ == '__main__':
    unittest.main()
