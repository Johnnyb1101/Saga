"""One bounded, cancellable HTTPS request to the OpenAI Responses API."""

from __future__ import annotations

import json
import re

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkRequest

from saga.ai_drafting import API_URL, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES

REQUEST_TIMEOUT_MS = 60_000
_MAX_KEY_LENGTH = 2_560
_BEARER = re.compile(r"[A-Za-z0-9._~+/-]+=*", re.ASCII)


def _authorization_value(key: str) -> bytes:
    """Reject header injection and excessive credentials before Qt sees them."""
    if not isinstance(key, str) or not 1 <= len(key) <= _MAX_KEY_LENGTH:
        raise ValueError("Enter a valid API key.")
    if _BEARER.fullmatch(key) is None:
        raise ValueError("Enter a valid API key.")
    return b"Bearer " + key.encode("ascii")


class OpenAIRequest(QObject):
    """Run at most one request at a time; terminal signals never include server text."""

    succeeded = Signal(bytes)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None,
                 manager: QNetworkAccessManager | None = None) -> None:
        super().__init__(parent)
        self._manager = manager or QNetworkAccessManager(self)
        if manager is None:
            self._manager.setProxy(QNetworkProxy(QNetworkProxy.NoProxy))
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self._reply = None
        self._body = bytearray()

    def start(self, request_dict: dict, key: str) -> None:
        if self._reply is not None:
            raise ValueError("A draft request is already running.")
        if not isinstance(request_dict, dict):
            raise ValueError("Invalid draft request.")  # noqa: TRY004 - UI catches ValueError.
        authorization = _authorization_value(key)
        try:
            payload = json.dumps(request_dict, ensure_ascii=False,
                                 separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise ValueError("Invalid draft request.") from exc
        if len(payload) > MAX_REQUEST_BYTES:
            raise ValueError("Draft request is too large.")

        request = QNetworkRequest(QUrl(API_URL))
        request.setAttribute(QNetworkRequest.RedirectPolicyAttribute,
                             QNetworkRequest.ManualRedirectPolicy)
        request.setAttribute(QNetworkRequest.CacheLoadControlAttribute,
                             QNetworkRequest.CacheLoadControl.AlwaysNetwork)
        request.setAttribute(QNetworkRequest.CacheSaveControlAttribute, False)
        request.setAttribute(QNetworkRequest.CookieLoadControlAttribute,
                             QNetworkRequest.LoadControl.Manual)
        request.setAttribute(QNetworkRequest.CookieSaveControlAttribute,
                             QNetworkRequest.LoadControl.Manual)
        request.setAttribute(QNetworkRequest.AuthenticationReuseAttribute,
                             QNetworkRequest.LoadControl.Manual)
        request.setHeader(QNetworkRequest.ContentTypeHeader, "application/json")
        request.setRawHeader(b"Authorization", authorization)
        try:
            reply = self._manager.post(request, payload)
        finally:
            # QNetworkAccessManager has copied the request at this point.
            request.setRawHeader(b"Authorization", b"")
            del authorization
        self._body.clear()
        self._reply = reply
        reply.setReadBufferSize(MAX_RESPONSE_BYTES + 1)
        reply.readyRead.connect(lambda: self._on_ready_read(reply))
        reply.finished.connect(lambda: self._on_finished(reply))
        reply.sslErrors.connect(lambda _errors: self._fail(reply, "Secure connection failed."))
        self._timer.start(REQUEST_TIMEOUT_MS)

    def cancel(self) -> None:
        reply = self._reply
        if reply is not None:
            self._disarm(reply, abort=True)

    def _on_timeout(self) -> None:
        reply = self._reply
        if reply is not None:
            self._fail(reply, "The request timed out.")

    def _on_ready_read(self, reply) -> None:
        if reply is not self._reply:
            return
        remaining = MAX_RESPONSE_BYTES - len(self._body)
        chunk = bytes(reply.read(remaining + 1))
        if len(chunk) > remaining:
            self._fail(reply, "The response is too large.")
            return
        self._body.extend(chunk)

    def _on_finished(self, reply) -> None:
        if reply is not self._reply:
            return
        self._on_ready_read(reply)
        if reply is not self._reply:
            return
        status = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        if isinstance(status, int) and not isinstance(status, bool) and not 200 <= status < 300:
            self._fail(reply, f"The service returned HTTP {status}.")
        elif not isinstance(status, int) or isinstance(status, bool) or reply.error():
            self._fail(reply, "The request failed.")
        else:
            body = bytes(self._body)
            self._disarm(reply, abort=False)
            self.succeeded.emit(body)

    def _fail(self, reply, message: str) -> None:
        if reply is not self._reply:
            return
        self._disarm(reply, abort=True)
        self.failed.emit(message)

    def _disarm(self, reply, *, abort: bool) -> None:
        # Clear identity first: abort() can emit finished() synchronously.
        self._reply = None
        self._timer.stop()
        self._body.clear()
        if abort:
            reply.abort()
        reply.deleteLater()
