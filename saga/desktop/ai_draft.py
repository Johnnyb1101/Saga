"""Explicit preview/send/review flow; credentials never enter report payloads."""
import datetime as dt
import json
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from report_bundle import publish_snapshot
from saga.ai_drafting import DEFAULT_MODEL, DraftError, build_request, parse_response
from saga.credentials import CredentialError, CredentialStore
from saga.desktop.ai_transport import OpenAIRequest
from saga.report_html import render_html


class SessionKey:
    """Process-local only; intentionally has no representation containing its value."""
    def __init__(self):
        self._key = ''

    def get(self):
        return self._key

    def set(self, key):
        self._key = key

    def clear(self):
        self._key = ''


SESSION_KEY = SessionKey()


def _label(text):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


class AIDraftDialog(QDialog):
    def __init__(self, snapshot, parent=None, *, credential_store=None, session=None, transport=None):
        super().__init__(parent)
        self.setWindowTitle('Draft with OpenAI')
        self.resize(860, 720)
        self._snapshot = snapshot
        self._store = credential_store if credential_store is not None else CredentialStore()
        self._session = session if session is not None else SESSION_KEY
        self._transport = transport if transport is not None else OpenAIRequest(self)
        self._transport.succeeded.connect(self._received)
        self._transport.failed.connect(self._failed)
        self._request = None
        self._draft = None
        self._busy = False
        self._closed = False
        layout = QVBoxLayout(self)
        layout.addWidget(_label('OpenAI drafts wording. Saga supplies the numbers. This snapshot stays fixed; reopen this window to include later changes. Review what will be sent, then choose Send and draft. API usage may incur charges.'))
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setMaxLength(2500)
        self.key_input.setPlaceholderText('Enter an OpenAI API key, or use the saved key')
        self.key_input.setAccessibleName('OpenAI API key')
        layout.addWidget(self.key_input)
        self.remember = QCheckBox('Remember key on this PC')
        layout.addWidget(self.remember)
        self.key_status = _label('')
        layout.addWidget(self.key_status)
        controls = QHBoxLayout()
        self.save_key_button = QPushButton('Save / update key')
        self.save_key_button.clicked.connect(self._save_key)
        self.remove_key_button = QPushButton('Remove saved key')
        self.remove_key_button.clicked.connect(self._remove_key)
        self.forget_button = QPushButton('Forget session key')
        self.forget_button.clicked.connect(self._forget_key)
        for button in (self.save_key_button, self.remove_key_button, self.forget_button):
            controls.addWidget(button)
        layout.addLayout(controls)
        layout.addWidget(_label('Remember is optional. Unchecking it does not remove an existing saved key; use Remove saved key. Stored keys belong to your Windows account on this PC. Other software running as you may access them. Removing a key here does not revoke it at OpenAI.'))
        self.model = QLineEdit(DEFAULT_MODEL)
        self.model.setAccessibleName('OpenAI model')
        layout.addWidget(_label('Model'))
        layout.addWidget(self.model)
        self.tabs = QTabWidget()
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName('Exact content to send to OpenAI')
        preview_page = QWidget()
        preview_layout = QVBoxLayout(preview_page)
        preview_layout.addWidget(_label('Exact request content below excludes the authentication header. Only this content is sent after you approve. Response storage is disabled, but this is not a guarantee of zero retention.'))
        preview_layout.addWidget(self.preview)
        self.tabs.addTab(preview_page, 'Data to send')
        self.review = QTextBrowser()
        self.review.setOpenLinks(False)
        self.review.setOpenExternalLinks(False)
        self.review.setAccessibleName('AI draft for review')
        self.tabs.addTab(self.review, 'Review draft')
        layout.addWidget(self.tabs)
        self.status = _label('')
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        self.send_button = QPushButton('Send and draft')
        self.send_button.clicked.connect(self._send)
        self.cancel_button = QPushButton('Cancel request')
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.setEnabled(False)
        self.save_button = QPushButton('Save reviewed report')
        self.save_button.clicked.connect(self._save_report)
        self.save_button.setEnabled(False)
        close = QPushButton('Close')
        close.clicked.connect(self.reject)
        for button in (self.send_button, self.cancel_button, self.save_button, close):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.finished.connect(self._shutdown)
        self.model.textChanged.connect(self._refresh_preview)
        self._stored_status()
        self._refresh_preview()

    def _stored_status(self):
        try:
            available = self._store.exists()
        except CredentialError:
            self.key_status.setText('Could not check saved-key storage. You can enter a session key.')
        else:
            self.key_status.setText('Saved key available.' if available else 'No saved key. Session keys are not saved automatically.')

    def _save_key(self):
        if not self.remember.isChecked():
            self.key_status.setText('Check Remember key on this PC before saving. Existing saved keys are unchanged.')
            return
        try:
            self._store.save(self.key_input.text())
        except CredentialError:
            self.key_status.setText('Could not save / update the key. A previously saved key may remain. No plaintext fallback was used.')
            return
        self.key_input.clear()
        self._session.clear()
        self.key_status.setText('Key saved in Windows Credential Manager.')

    def _remove_key(self):
        self._cancel()
        self.key_input.clear()
        self._session.clear()
        try:
            self._store.remove()
        except CredentialError:
            self.key_status.setText('Could not remove saved key. It may still be stored. Session key cleared.')
        else:
            self.key_status.setText('Saved key removed. Session key cleared. The key has not been revoked at OpenAI.')

    def _forget_key(self):
        self._cancel()
        self.key_input.clear()
        self._session.clear()
        self.key_status.setText('Session key cleared. Any saved Windows credential remains until Remove saved key is used.')

    def _refresh_preview(self):
        if self._busy:
            return
        self._draft = None
        self.save_button.setEnabled(False)
        self.review.clear()
        evidence, preparation = self._snapshot.unpack()
        try:
            self._request = build_request(evidence, preparation, self.model.text())
        except (DraftError, ValueError):
            self._request = None
            self.preview.clear()
            self.status.setText('Could not prepare this request. Check the model or choose fewer accomplishments.')
        else:
            self.preview.setPlainText(json.dumps(self._request, ensure_ascii=False, indent=2))
            self.status.setText('Review the Data to send tab before sending. No request has been made.')
        self.send_button.setEnabled(self._request is not None)

    def _set_busy(self, busy):
        self._busy = busy
        self.send_button.setEnabled(not busy and self._request is not None)
        self.cancel_button.setEnabled(busy)
        self.model.setEnabled(not busy)
        self.key_input.setEnabled(not busy)
        self.save_key_button.setEnabled(not busy)
        self.remember.setEnabled(not busy)

    def _send(self):
        if self._busy or self._closed or self._request is None:
            return
        key = self.key_input.text() or self._session.get()
        if not key:
            try:
                key = self._store.read() or ''
            except CredentialError:
                self.status.setText('Could not read the saved key. Enter a session key or repair saved-key storage.')
                return
        if not key:
            self.status.setText('Enter an API key before sending.')
            return
        self._draft = None
        self.review.clear()
        self.save_button.setEnabled(False)
        self._set_busy(True)
        self.status.setText('Request in progress. Cancelling stops waiting; OpenAI may already be processing it.')
        try:
            self._transport.start(self._request, key)
        except (ValueError, DraftError):
            self._set_busy(False)
            self.status.setText('Could not start the request. Check the API key and request size.')
        else:
            # Session reuse is independent of Remember; saving always requires Save / update key.
            if self.key_input.text():
                self._session.set(key)
            self.key_input.clear()

    def _received(self, raw):
        if self._closed or not self._busy:
            return
        self._set_busy(False)
        evidence, preparation = self._snapshot.unpack()
        try:
            draft = parse_response(raw, evidence, preparation)
            html = render_html(evidence, draft, preparation)
        except (DraftError, ValueError):
            self.status.setText('OpenAI returned an unusable draft. No report was saved. You may try again explicitly.')
            return
        self._draft = draft
        self.review.setHtml(html)
        self.tabs.setCurrentIndex(1)
        self.save_button.setEnabled(True)
        self.status.setText('Draft ready for review. Fact references are checked; wording and claims still need your review.')

    def _failed(self, message):
        if self._closed or not self._busy:
            return
        self._set_busy(False)
        # Do not render arbitrary provider error strings, even from an injected transport.
        self.status.setText('OpenAI request failed. Check your connection, key, model access, or usage limits. No report was saved.')

    def _cancel(self):
        self._set_busy(False)
        self._transport.cancel()
        if not self._closed:
            self.status.setText('Request cancelled. OpenAI may already have processed it; no automatic retry will occur.')

    def _save_report(self):
        if self._draft is None or self._busy or self._closed:
            return
        folder = QFileDialog.getExistingDirectory(self, 'Choose where to save the reviewed report')
        if not folder:
            return
        destination = Path(folder) / f'Saga-AI-report-{dt.datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}'
        try:
            publish_snapshot(self._snapshot, destination, narrative=self._draft)
        except (OSError, ValueError):
            self.status.setText(f'Could not save the report. Partial files may remain in {destination}.')
        else:
            self.status.setText(f'Report saved: {destination / "report.html"}')

    def _shutdown(self, result):
        self._closed = True
        self._transport.cancel()
        self.key_input.clear()
