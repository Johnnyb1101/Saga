"""One bounded, cancellable HTTPS request to the OpenAI Responses API."""

from __future__ import annotations

import json
import re

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkProxy,
    QNetworkReply,
    QNetworkRequest,
)

from saga.ai_drafting import API_URL, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES

REQUEST_TIMEOUT_MS = 60_000
_MAX_KEY_LENGTH = 2_560
_BEARER = re.compile(r"[A-Za-z0-9._~+/-]+=*", re.ASCII)
_QUOTA_CODES = frozenset({
    "insufficient_quota",
    "credit_balance_exhausted",
    "billing_hard_limit_reached",
    "organization_spend_limit_exceeded",
    "project_spend_limit_exceeded",
    "organization_usage_limit_exceeded",
})
_DNS_ERRORS = frozenset({QNetworkReply.HostNotFoundError, QNetworkReply.ProxyNotFoundError})
_TLS_ERRORS = frozenset({QNetworkReply.SslHandshakeFailedError})
_TIMEOUT_ERRORS = frozenset({QNetworkReply.TimeoutError})


def _http_failure(status: int, body: bytes) -> str:
    if 300 <= status < 400:
        return "redirect"
    if status == 400 or status == 422:
        return "rejected"
    if status == 401:
        return "authentication"
    if status == 403:
        return "permission"
    if status == 404:
        return "model"
    if status == 429:
        try:
            data = json.loads(body)
            error = data.get("error") if isinstance(data, dict) else None
            if isinstance(error, dict) and any(
                isinstance(error.get(field), str) and error[field] in _QUOTA_CODES
                for field in ("code", "type")
            ):
                return "quota"
        except (ValueError, UnicodeError, RecursionError):
            pass
        return "rate_limit"
    if 500 <= status < 600:
        return "service"
    return "unknown"


def _network_failure(error) -> str:
    if error in _DNS_ERRORS:
        return "dns"
    if error in _TLS_ERRORS:
        return "tls"
    if error in _TIMEOUT_ERRORS:
        return "timeout"
    return "connection"


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
        reply.sslErrors.connect(lambda _errors: self._fail(reply, "tls"))
        self._timer.start(REQUEST_TIMEOUT_MS)

    def cancel(self) -> None:
        reply = self._reply
        if reply is not None:
            self._disarm(reply, abort=True)

    def _on_timeout(self) -> None:
        reply = self._reply
        if reply is not None:
            self._fail(reply, "timeout")

    def _on_ready_read(self, reply) -> None:
        if reply is not self._reply:
            return
        remaining = MAX_RESPONSE_BYTES - len(self._body)
        chunk = bytes(reply.read(remaining + 1))
        if len(chunk) > remaining:
            self._fail(reply, "too_large")
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
            self._fail(reply, _http_failure(status, bytes(self._body)))
        elif reply.error() != QNetworkReply.NetworkError.NoError:
            self._fail(reply, _network_failure(reply.error()))
        elif not isinstance(status, int) or isinstance(status, bool):
            self._fail(reply, "unknown")
        else:
            body = bytes(self._body)
            self._disarm(reply, abort=False)
            self.succeeded.emit(body)

    def _fail(self, reply, reason: str) -> None:
        if reply is not self._reply:
            return
        self._disarm(reply, abort=True)
        self.failed.emit(reason)

    def _disarm(self, reply, *, abort: bool) -> None:
        # Clear identity first: abort() can emit finished() synchronously.
        self._reply = None
        self._timer.stop()
        self._body.clear()
        if abort:
            reply.abort()
        reply.deleteLater()
