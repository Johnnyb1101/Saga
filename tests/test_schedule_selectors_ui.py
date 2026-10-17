"""Day selectors, first-date previews and saved desktop schedules."""

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
class ScheduleSelectorsUiTests(unittest.TestCase):
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
        self.form.title.setText('Invented scheduled task')
        self.form.category.setCurrentIndex(self.form.category.findData('work'))
        self.form.repeat_task.setChecked(True)

    def choose(self, frequency, start):
        self.form.repeat_frequency.setCurrentIndex(self.form.repeat_frequency.findData(frequency))
        self.form.due_date.setDate(QDate.fromString(start, 'yyyy-MM-dd'))

    def saved_task(self):
        self.form.show_preview()
        self.assertIsNotNone(self.form._preview_draft, self.form.status.text())
        self.form.save()
        self.assertTrue(self.form._committed, self.form.status.text())
        with closing(db.connect(self.database)) as con:
            return dict(reads.open_tasks(con)[0])

    def test_weekly_selector_aligns_first_date_and_preview(self):
        self.choose('weekly', '2026-10-16')  # Friday
        self.form.repeat_weekday.setCurrentIndex(0)
        task = self.saved_task()
        self.assertEqual(task['due_date'], '2026-10-19')
        preview = self.form.preview_text.toPlainText()
        self.assertIn('2026-10-19', preview)
        self.assertIn('Monday', preview)
        self.assertFalse(self.form.form_layout.isRowVisible(self.form.repeat_month_day))

    def test_every_two_weeks_uses_weekday_and_fixed_interval(self):
        self.choose('biweekly', '2026-10-16')
        self.form.repeat_weekday.setCurrentIndex(0)
        self.form.repeat_interval.setValue(7)  # Hidden draft must not affect this option.
        task = self.saved_task()
        series = self.service.recurrence_details(task['id'])
        self.assertEqual((series['frequency'], series['interval']), ('weekly', 2))
        self.assertFalse(self.form.form_layout.isRowVisible(self.form.repeat_interval))
        with closing(db.connect(self.database)) as con:
            writes.complete_task(con, task['id'], outcome='Finished')
            self.assertEqual(reads.open_occurrence(con, series['id'])['due_date'], '2026-11-02')

    def test_monthly_keeps_nominal_day_after_short_month(self):
        self.choose('monthly', '2027-02-01')
        self.form.repeat_month_day.setValue(31)
        task = self.saved_task()
        self.assertEqual(task['due_date'], '2027-02-28')
        series = self.service.recurrence_details(task['id'])
        self.assertEqual(series['month_day'], 31)
        self.assertIn('31', self.form.preview_text.toPlainText())
        with closing(db.connect(self.database)) as con:
            writes.complete_task(con, task['id'], outcome='Finished')
            self.assertEqual(reads.open_occurrence(con, series['id'])['due_date'], '2027-03-31')

    def test_twice_monthly_sorts_dates_and_shows_schedule_on_card(self):
        self.choose('twice_monthly', '2026-10-16')
        self.form.repeat_month_day.setValue(31)
        self.form.repeat_second_day.setValue(15)
        self.form.repeat_interval.setValue(7)
        task = self.saved_task()
        series = self.service.recurrence_details(task['id'])
        self.assertEqual((series['month_day'], series['second_month_day'], series['interval']), (15, 31, 1))
        self.assertEqual(task['due_date'], '2026-10-31')
        rows = self.service.today(on='2026-10-31')['due_today']
        section = TodaySection('Tasks', ())
        self.addCleanup(section.close)
        section.show_rows(rows)
        self.assertIn('15', section.cards[0].details_label.text())
        self.assertIn('31', section.cards[0].details_label.text())
        with closing(db.connect(self.database)) as con:
            writes.complete_task(con, task['id'], outcome='Finished')
            self.assertEqual(reads.open_occurrence(con, series['id'])['due_date'], '2026-11-15')

    def test_duplicate_dates_keep_draft_without_saving(self):
        self.choose('twice_monthly', '2026-10-01')
        self.form.repeat_month_day.setValue(15)
        self.form.repeat_second_day.setValue(15)
        self.form.show_preview()
        self.assertIsNone(self.form._preview_draft)
        self.assertTrue(self.form.has_unsaved_draft)
        with closing(db.connect(self.database)) as con:
            self.assertEqual(reads.open_tasks(con), [])

    def test_modes_editing_and_reset_preserve_or_clear_selectors(self):
        self.choose('twice_monthly', '2026-10-01')
        self.form.repeat_month_day.setValue(1)
        self.form.repeat_second_day.setValue(15)
        before = self.form.draft()
        self.widget.already_finished.setChecked(True)
        self.widget.already_finished.setChecked(False)
        self.assertEqual(self.form.draft(), before)
        self.form.show_preview()
        self.form.edit_draft()
        self.assertEqual(self.form.draft(), before)
        self.form.repeat_task.setChecked(False)
        self.assertNotIn('month_day', self.form.draft())
        self.form.reset_form()
        self.assertFalse(self.form.repeat_task.isChecked())
        self.assertFalse(self.form.form_layout.isRowVisible(self.form.repeat_weekday))


if __name__ == '__main__':
    unittest.main()
