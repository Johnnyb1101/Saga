"""Shared Add Task fields save completed work through one preview and save."""

import os
import tempfile
import unittest
from contextlib import closing
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HAS_QT = find_spec('PySide6') is not None
if HAS_QT:
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QApplication

    from saga import db, export
    from saga.application import ArchiveService
    from saga.desktop.task_entry import AddTaskWidget


@unittest.skipUnless(HAS_QT, 'Optional PySide6 desktop dependency is not installed')
class InlineCompletedEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = self.root / 'archive.db'
        db.init_db(self.database)
        self.service = ArchiveService(self.database, self.root / 'exports')
        self.widget = AddTaskWidget(self.service)
        self.addCleanup(self.widget.close)
        self.form = self.widget.task
        self.details = self.form.completion_details
        self.form.title.setText('Reviewed invented applications')
        self.form.category.setCurrentIndex(self.form.category.findData('work'))

    def finished(self):
        self.widget.already_finished.setChecked(True)
        self.details.measurement_status.setCurrentIndex(
            self.details.measurement_status.findData('not_applicable'))

    def save(self):
        self.form.show_preview()
        self.assertIsNotNone(self.form._preview_draft, self.form.status.text())
        self.form.save()
        self.assertTrue(self.form._committed, self.form.status.text())

    def rows(self, table):
        with closing(db.connect(self.database)) as con:
            return [dict(row) for row in con.execute('SELECT * FROM ' + table)]

    def test_title_alone_records_completed_work_without_open_task(self):
        self.finished()
        self.assertEqual(self.details.outcome.toPlainText(), '')
        self.save()
        self.assertEqual(self.rows('completions')[0]['outcome'], 'Reviewed invented applications')
        self.assertEqual(self.rows('tasks'), [])
        self.assertFalse(self.form.has_unsaved_draft)

    def test_result_is_added_to_title_and_backdated_measurement_is_saved(self):
        self.finished()
        self.details.outcome.setPlainText('Three packages are ready for review.')
        self.details.use_today.setChecked(False)
        self.details.completed_date.setDate(QDate(2020, 1, 15))
        self.details.measurement_status.setCurrentIndex(self.details.measurement_status.findData('measured'))
        self.details.measure.setCurrentIndex(self.details.measure.count() - 1)
        self.details.new_measure.setText('invented packages')
        self.details.quantity.setText('3')
        self.save()
        row = self.rows('completions')[0]
        self.assertEqual(row['outcome'], 'Reviewed invented applications\n\nThree packages are ready for review.')
        self.assertEqual((row['quantity'], row['measure'], row['completed_at']),
                         (3, 'invented packages', '2020-01-15 00:00:00'))

    def test_schedule_draft_survives_toggle_and_is_not_saved_as_completed(self):
        self.form.repeat_task.setChecked(True)
        self.form.repeat_frequency.setCurrentIndex(self.form.repeat_frequency.findData('weekly'))
        self.form.repeat_interval.setValue(2)
        schedule = self.form.draft()
        self.finished()
        self.assertFalse(self.form.repeat_task.isEnabled())
        self.details.outcome.setPlainText('Keep result')
        self.widget.already_finished.setChecked(False)
        self.assertEqual(self.form.draft(), schedule)
        self.assertTrue(self.form.repeat_task.isEnabled())
        self.finished()
        self.assertEqual(self.details.outcome.toPlainText(), 'Keep result')
        self.save()
        self.assertEqual(self.rows('recurrences'), [])
        self.assertEqual(self.rows('tasks'), [])

    def test_export_retry_and_reentrant_save_do_not_duplicate_completion(self):
        self.finished()
        self.form.show_preview()
        original = self.service.save_capture

        def reentrant(draft):
            self.form.save()
            return original(draft)

        with (patch.object(self.service, 'save_capture', side_effect=reentrant) as saver,
              patch.object(export, 'write_all', side_effect=export.ExportError('offline'))):
            self.form.save()
            self.form.save()
        self.assertEqual(saver.call_count, 1)
        self.assertEqual(len(self.rows('completions')), 1)
        self.assertIn('Export failed', self.form.status.text())
        self.form.retry_export()
        self.assertEqual(len(self.rows('completions')), 1)
        self.assertTrue((self.root / 'exports' / 'manifest.json').exists())
        self.assertFalse(self.widget.already_finished.isEnabled())

    def test_validation_failure_preserves_shared_text_and_evidence(self):
        self.finished()
        self.form.title.clear()
        self.details.outcome.setPlainText('Optional details cannot replace required task text')
        self.form.show_preview()
        self.assertIsNone(self.form._preview_draft)
        self.assertEqual(self.rows('completions'), [])
        self.assertTrue(self.form.has_unsaved_draft)
        self.assertIn('Optional details', self.details.outcome.toPlainText())

    def test_edit_and_new_entry_reset_preserve_then_clear_all_values(self):
        self.finished()
        self.details.outcome.setPlainText('Keep optional result')
        before = self.form.draft()
        self.form.show_preview()
        self.form.edit_draft()
        self.assertEqual(self.form.draft(), before)
        self.save()
        self.form.reset_form()
        self.assertEqual(self.form.title.text(), '')
        self.assertEqual(self.details.outcome.toPlainText(), '')
        self.assertFalse(self.widget.already_finished.isChecked())
        self.assertTrue(self.widget.already_finished.isEnabled())
        self.assertTrue(self.details.isHidden())


if __name__ == '__main__':
    unittest.main()
