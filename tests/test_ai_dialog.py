"""Offline integration checks for explicit AI consent, keys, and frozen exports."""
import json
import os
import tempfile
import unittest
from contextlib import closing
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

from report_bundle import create_snapshot, publish_snapshot, verify_package
from saga import db, writes
from saga.credentials import CredentialError

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
HAS_QT = find_spec('PySide6') is not None
if HAS_QT:
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    from saga.desktop.ai_draft import AIDraftDialog, SessionKey

    class FakeSignal:
        def __init__(self):
            self.callbacks = []

        def connect(self, callback):
            self.callbacks.append(callback)

        def emit(self, *args):
            for callback in list(self.callbacks):
                callback(*args)

    class FakeTransport:
        def __init__(self):
            self.succeeded = FakeSignal()
            self.failed = FakeSignal()
            self.calls = []
            self.cancels = 0

        def deleteLater(self):
            self.succeeded.callbacks.clear()
            self.failed.callbacks.clear()

        def start(self, request, key):
            self.calls.append((request, key))

        def cancel(self):
            self.cancels += 1


class ArchiveFixture:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.database = db.init_db(self.root / 'source.db')
        with closing(db.connect(self.database)) as con:
            writes.add_measure(con, 'hours')
            self.record = writes.add_completion(con, 'work', 'Saved wording', measure='hours', quantity=2.5)
        self.snapshot = create_snapshot(self.database)

class SnapshotTests(ArchiveFixture, unittest.TestCase):
    def test_export_uses_frozen_snapshot_after_archive_changes(self):
        with closing(db.connect(self.database)) as con:
            writes.add_completion(con, 'home', 'Later accomplishment', measure='hours', quantity=100)
        output = publish_snapshot(self.snapshot, self.root / 'report')
        verify_package(output)
        evidence = json.loads((output / 'evidence.json').read_text())
        self.assertEqual(evidence['summary']['completions'], 1)
        self.assertEqual(evidence['measurements'][0]['total'], 2.5)

    def test_snapshot_returns_independent_copies_and_selects_without_rereading(self):
        evidence, _ = self.snapshot.unpack()
        evidence['summary']['completions'] = 999
        selected = self.snapshot.for_format('epb', [self.record])
        self.assertEqual(selected.unpack()[0]['summary']['completions'], 1)
        self.assertEqual(selected.unpack()[1]['selected_ids'], [self.record])
        with self.assertRaises(ValueError):
            selected.for_format('cv', [999999])

    def test_existing_output_is_never_overwritten(self):
        destination = self.root / 'report'
        destination.mkdir()
        sentinel = destination / 'preserve'
        sentinel.write_text('unchanged')
        with self.assertRaises(FileExistsError):
            publish_snapshot(self.snapshot, destination)
        self.assertEqual(sentinel.read_text(), 'unchanged')


class FakeStore:
    def __init__(self):
        self.key = None
        self.error = False
        self.reads = 0

    def exists(self):
        return self.key is not None

    def read(self):
        self.reads += 1
        if self.error:
            raise CredentialError('test failure')
        return self.key

    def save(self, key):
        if self.error:
            raise CredentialError('test failure')
        self.key = key

    def remove(self):
        if self.error:
            raise CredentialError('test failure')
        self.key = None


@unittest.skipUnless(HAS_QT, 'Optional PySide6 desktop dependency is not installed')
class AIDialogTests(ArchiveFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self.store = FakeStore()
        self.session = SessionKey()
        self.transport = FakeTransport()
        self.dialog = AIDraftDialog(self.snapshot, credential_store=self.store,
                                    session=self.session, transport=self.transport)
        self.addCleanup(self._dispose_dialog)

    def _dispose_dialog(self):
        self.dialog.reject()
        self.dialog.deleteLater()
        self.transport.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def response(self):
        evidence, _ = self.snapshot.unpack()
        fact = next(iter(evidence['facts']))
        narrative = {'schema_version': 1, 'report_id': evidence['report_id'], 'sections': [
            {'heading': 'Overview', 'paragraphs': [[{'text': 'Recorded accomplishments: '}, {'fact': fact}]]}]}
        return json.dumps({'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
                          'status': 'completed', 'content': [{'type': 'output_text',
                          'text': json.dumps(narrative)}]}]}).encode()

    def test_no_automatic_send_and_key_excluded_from_preview(self):
        self.dialog.key_input.setText('fake-test-key')
        self.assertFalse(self.transport.calls)
        self.assertNotIn('fake-test-key', self.dialog.preview.toPlainText())
        self.assertEqual(json.loads(self.dialog.preview.toPlainText()), self.dialog._request)
        self.dialog.send_button.click()
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(self.transport.calls[0][0], json.loads(self.dialog.preview.toPlainText()))
        self.assertEqual(self.transport.calls[0][1], 'fake-test-key')
        self.assertEqual(self.session.get(), 'fake-test-key')
        self.assertIsNone(self.store.key)
        self.dialog.send_button.click()
        self.assertEqual(len(self.transport.calls), 1)

    def test_remember_requires_explicit_save_and_remove_clears_session(self):
        self.dialog.key_input.setText('fake-test-key')
        self.dialog.save_key_button.click()
        self.assertIsNone(self.store.key)
        self.dialog.remember.setChecked(True)
        self.dialog.save_key_button.click()
        self.assertEqual(self.store.key, 'fake-test-key')
        self.assertEqual(self.dialog.key_input.text(), '')
        self.dialog.remember.setChecked(False)
        self.assertEqual(self.store.key, 'fake-test-key')
        self.session.set('fake-session-key')
        self.dialog.remove_key_button.click()
        self.assertIsNone(self.store.key)
        self.assertEqual(self.session.get(), '')
        self.assertFalse(self.transport.calls)

    def test_saved_key_is_only_read_when_send_is_clicked(self):
        self.store.key = 'fake-stored-key'
        self.dialog._stored_status()
        self.assertEqual(self.store.reads, 0)
        self.dialog.send_button.click()
        self.assertEqual(self.store.reads, 1)
        self.assertEqual(self.transport.calls[0][1], 'fake-stored-key')

    def test_store_failures_do_not_claim_saved_key_is_gone(self):
        self.store.key = 'fake-original-key'
        self.store.error = True
        self.dialog.remember.setChecked(True)
        self.dialog.key_input.setText('fake-replacement-key')
        self.dialog.save_key_button.click()
        self.assertIn('previously saved key may remain', self.dialog.key_status.text())
        self.dialog.remove_key_button.click()
        self.assertIn('may still be stored', self.dialog.key_status.text())
        self.assertEqual(self.store.key, 'fake-original-key')
        self.assertNotIn('fake-', self.dialog.key_status.text())

    def test_cancel_close_and_remove_ignore_late_completions(self):
        self.dialog.key_input.setText('fake-test-key')
        self.dialog.send_button.click()
        self.dialog.remove_key_button.click()
        self.transport.succeeded.emit(self.response())
        self.assertIsNone(self.dialog._draft)
        self.assertEqual(self.session.get(), '')
        self.dialog.key_input.setText('fake-other-key')
        self.dialog.send_button.click()
        self.dialog.reject()
        self.transport.succeeded.emit(self.response())
        self.assertIsNone(self.dialog._draft)
        self.assertEqual(self.dialog.key_input.text(), '')

    def test_invalid_output_and_provider_errors_are_sanitized(self):
        self.dialog.key_input.setText('fake-test-key')
        self.dialog.send_button.click()
        self.transport.failed.emit('FAKE-SECRET-AND-PROVIDER-BODY')
        self.assertNotIn('FAKE-SECRET', self.dialog.status.text())
        self.dialog.send_button.click()
        self.transport.succeeded.emit(b'not valid')
        self.assertFalse(self.dialog.save_button.isEnabled())
        self.assertIsNone(self.dialog._draft)

    def test_valid_draft_requires_explicit_save_and_keeps_keys_out_of_package(self):
        self.dialog.key_input.setText('fake-export-exclusion-key')
        self.dialog.send_button.click()
        self.transport.succeeded.emit(self.response())
        self.assertTrue(self.dialog.save_button.isEnabled())
        self.assertFalse(list(self.root.glob('Saga-AI-report-*')))
        with patch('saga.desktop.ai_draft.QFileDialog.getExistingDirectory', return_value=str(self.root)):
            self.dialog.save_button.click()
        output = next(self.root.glob('Saga-AI-report-*'))
        verify_package(output)
        for path in output.iterdir():
            self.assertNotIn(b'fake-export-exclusion-key', path.read_bytes())

    def test_model_change_invalidates_review_and_draft(self):
        self.dialog.key_input.setText('fake-test-key')
        self.dialog.send_button.click()
        self.transport.succeeded.emit(self.response())
        self.dialog.model.setText('different-model')
        self.assertIsNone(self.dialog._draft)
        self.assertFalse(self.dialog.save_button.isEnabled())
        self.assertIn('different-model', self.dialog.preview.toPlainText())

    def test_history_ai_entry_uses_selected_scope_and_no_automatic_send(self):
        from PySide6.QtCore import QDate

        from saga.application import ArchiveService
        from saga.desktop.history import HistoryWidget

        widget = HistoryWidget(ArchiveService(self.database))
        self.addCleanup(widget.close)
        widget.use_from.setChecked(False)
        widget.use_to.setChecked(False)
        with patch('saga.desktop.ai_draft.AIDraftDialog') as dialog:
            widget.ai_draft_button.click()
            captured = dialog.call_args.args[0]
            self.assertEqual(captured.unpack()[0]['summary']['completions'], 1)
            dialog.return_value.exec.assert_called_once()
        widget.use_from.setChecked(True)
        widget.use_to.setChecked(True)
        widget.from_date.setDate(QDate(2025, 2, 1))
        widget.to_date.setDate(QDate(2024, 2, 1))
        with patch('saga.desktop.ai_draft.AIDraftDialog') as dialog:
            widget.ai_draft_button.click()
            dialog.assert_not_called()
        self.assertIn('From must be', widget.status_label.text())
