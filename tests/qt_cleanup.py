"""Dispose test widgets without flushing unrelated Qt deferred deletes."""


def destroy_widget(widget):
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid

    if isValid(widget):
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(widget, QEvent.Type.DeferredDelete)


def close_and_destroy_widget(widget):
    from shiboken6 import isValid

    if isValid(widget):
        try:
            widget.close()
        finally:
            destroy_widget(widget)
