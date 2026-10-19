"""Offline tests for the desktop drafting transport."""

import json
import os
import unittest
from importlib.util import find_spec

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HAS_QT = find_spec("PySide6") is not None

if HAS_QT:
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtNetwork import QNetworkRequest
    from PySide6.QtWidgets import QApplication

    from saga.ai_drafting import API_URL, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES
    from saga.desktop.ai_transport import OpenAIRequest


if HAS_QT:
    class FakeSignal:
        def __init__(self):
            self.callbacks = []

        def connect(self, callback):
            self.callbacks.append(callback)

        def emit(self, *args):
            for callback in tuple(self.callbacks):
                callback(*args)


    class FakeReply:

        def __init__(self):
            self.readyRead = FakeSignal()
            self.finished = FakeSignal()
            self.sslErrors = FakeSignal()
            self.pending = bytearray()
            self.status = 200
            self.network_error = 0
            self.aborted = False
            self.read_buffer_size = None
            self.deleted_later = False

        def deleteLater(self):
            self.deleted_later = True

        def setReadBufferSize(self, size):
            self.read_buffer_size = size

        def read(self, amount):
            chunk = bytes(self.pending[:amount])
            del self.pending[:amount]
            return chunk

        def attribute(self, attribute):
            if attribute == QNetworkRequest.HttpStatusCodeAttribute:
                return self.status
            return None

        def error(self):
            return self.network_error

        def abort(self):
            self.aborted = True
            self.finished.emit()  # Exercise synchronous stale callbacks.

        def receive(self, body):
            self.pending.extend(body)
            self.readyRead.emit()


    class FakeManager:
        def __init__(self):
            self.posts = []

        def post(self, request, payload):
            reply = FakeReply()
            self.posts.append((QNetworkRequest(request), bytes(payload), reply))
            return reply


@unittest.skipUnless(HAS_QT, "Optional PySide6 desktop dependency is not installed")
class AITransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.manager = FakeManager()
        self.transport = OpenAIRequest(manager=self.manager)
        self.successes = []
        self.failures = []
        self.transport.succeeded.connect(self.successes.append)
        self.transport.failed.connect(self.failures.append)

    def tearDown(self):
        self.transport.cancel()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.manager.posts.clear()

    def start(self, data=None):
        self.transport.start(data or {"model": "test", "store": False}, "sk-test-only")
        return self.manager.posts[-1][2]

    def test_request_is_fixed_https_json_bounded_and_does_not_follow_redirects(self):
        reply = self.start()
        request, payload, _ = self.manager.posts[-1]
        self.assertEqual(request.url().toString(), API_URL)
        self.assertEqual(request.url().scheme(), "https")
        self.assertEqual(request.rawHeader("Authorization"), b"Bearer sk-test-only")
        self.assertEqual(request.header(QNetworkRequest.ContentTypeHeader), "application/json")
        self.assertEqual(request.attribute(QNetworkRequest.RedirectPolicyAttribute),
                         QNetworkRequest.ManualRedirectPolicy)
        self.assertEqual(request.attribute(QNetworkRequest.CacheLoadControlAttribute),
                         QNetworkRequest.CacheLoadControl.AlwaysNetwork)
        self.assertIs(request.attribute(QNetworkRequest.CacheSaveControlAttribute), False)
        for attribute in (QNetworkRequest.CookieLoadControlAttribute,
                          QNetworkRequest.CookieSaveControlAttribute,
                          QNetworkRequest.AuthenticationReuseAttribute):
            self.assertEqual(request.attribute(attribute), QNetworkRequest.LoadControl.Manual)
        self.assertEqual(json.loads(payload), {"model": "test", "store": False})
        self.assertEqual(reply.read_buffer_size, MAX_RESPONSE_BYTES + 1)
        reply.receive(b'{"ok":true}')
        reply.finished.emit()
        self.assertEqual(self.successes, [b'{"ok":true}'])
        self.assertEqual(self.failures, [])

    def test_rejects_invalid_credentials_and_oversized_or_invalid_payload(self):
        for key in ("", "a\nb", 'sk-"quote', "nonascii-\N{SNOWMAN}", "x" * 2561):
            with self.subTest(key_length=len(key)), self.assertRaises(ValueError):
                self.transport.start({}, key)
        with self.assertRaises(ValueError):
            self.transport.start({"data": "x" * MAX_REQUEST_BYTES}, "sk-test-only")
        with self.assertRaises(ValueError):
            self.transport.start({"invalid": object()}, "sk-test-only")
        self.assertEqual(self.manager.posts, [])

    def test_cancel_disarms_before_abort_and_allows_reuse(self):
        old = self.start()
        self.transport.cancel()
        self.assertTrue(old.aborted)
        old.receive(b"stale")
        old.finished.emit()
        self.assertEqual((self.successes, self.failures), ([], []))
        new = self.start()
        new.receive(b"fresh")
        new.finished.emit()
        self.assertEqual(self.successes, [b"fresh"])

    def test_refuses_concurrent_start(self):
        self.start()
        with self.assertRaises(ValueError):
            self.transport.start({}, "sk-test-only")
        self.assertEqual(len(self.manager.posts), 1)

    def test_response_cap_aborts_without_returning_any_data(self):
        reply = self.start()
        reply.receive(b"x" * MAX_RESPONSE_BYTES)
        self.assertFalse(reply.aborted)
        reply.receive(b"x")
        self.assertTrue(reply.aborted)
        self.assertEqual(self.failures, ["The response is too large."])
        self.assertEqual(self.successes, [])

    def test_timeout_aborts_once(self):
        reply = self.start()
        self.transport._on_timeout()
        self.transport._on_timeout()
        self.assertTrue(reply.aborted)
        self.assertEqual(self.failures, ["The request timed out."])

    def test_http_redirect_and_server_error_are_sanitized(self):
        for status in (301, 307, 401, 429, 500):
            with self.subTest(status=status):
                reply = self.start()
                reply.status = status
                reply.receive(b"secret server text")
                reply.finished.emit()
                self.assertEqual(self.failures[-1], f"The service returned HTTP {status}.")
                self.assertNotIn("secret", self.failures[-1])
        self.assertEqual(self.successes, [])

    def test_tls_and_network_errors_are_sanitized(self):
        reply = self.start()
        reply.sslErrors.emit([])
        self.assertTrue(reply.aborted)
        self.assertEqual(self.failures, ["Secure connection failed."])
        reply = self.start()
        reply.network_error = 1
        reply.status = None
        reply.finished.emit()
        self.assertEqual(self.failures[-1], "The request failed.")
        self.assertEqual(self.successes, [])


if __name__ == "__main__":
    unittest.main()
