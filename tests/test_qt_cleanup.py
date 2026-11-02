"""Scoped native widget cleanup checks."""

import os
import unittest
from importlib.util import find_spec

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HAS_QT = find_spec("PySide6") is not None

if HAS_QT:
    from PySide6.QtWidgets import QApplication, QWidget
    from shiboken6 import isValid

    from tests.qt_cleanup import close_and_destroy_widget, destroy_widget


@unittest.skipUnless(HAS_QT, "Optional PySide6 desktop dependency is not installed")
class QtCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_parent_and_children_are_destroyed(self):
        class CountingWidget(QWidget):
            def __init__(self):
                super().__init__()
                self.closes = 0

            def close(self):
                self.closes += 1
                return super().close()

        parent = CountingWidget()
        child = QWidget(parent)
        close_and_destroy_widget(parent)
        self.assertEqual(parent.closes, 1)
        self.assertFalse(isValid(parent))
        self.assertFalse(isValid(child))

    def test_already_deleted_widget_is_no_op(self):
        widget = QWidget()
        destroy_widget(widget)
        self.assertFalse(isValid(widget))
        destroy_widget(widget)
        close_and_destroy_widget(widget)

    def test_unrelated_pending_delete_is_not_flushed(self):
        unrelated = QWidget()
        target = QWidget()
        unrelated.deleteLater()
        destroy_widget(target)
        self.assertFalse(isValid(target))
        self.assertTrue(isValid(unrelated))
        destroy_widget(unrelated)


if __name__ == "__main__":
    unittest.main()
