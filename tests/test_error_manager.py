"""Tests for the error manager module."""

import threading
import time

from meeting_recorder.error_manager import ErrorManager


def test_report_and_get():
    mgr = ErrorManager()
    mgr.report("TestComponent", "Something went wrong")
    errors = mgr.get_errors()
    assert len(errors) == 1
    assert errors[0].component == "TestComponent"
    assert errors[0].message == "Something went wrong"


def test_clear():
    mgr = ErrorManager()
    mgr.report("A", "Error A")
    mgr.report("B", "Error B")
    assert mgr.has_errors()

    mgr.clear("A")
    errors = mgr.get_errors()
    assert len(errors) == 1
    assert errors[0].component == "B"


def test_clear_all():
    mgr = ErrorManager()
    mgr.report("A", "Error A")
    mgr.report("B", "Error B")
    mgr.clear_all()
    assert not mgr.has_errors()


def test_report_replaces():
    mgr = ErrorManager()
    mgr.report("A", "First error")
    mgr.report("A", "Second error")
    errors = mgr.get_errors()
    assert len(errors) == 1
    assert errors[0].message == "Second error"


def test_retry_calls_callback():
    called = threading.Event()

    def retry_fn():
        called.set()

    mgr = ErrorManager()
    mgr.report("A", "Fail", retry_callback=retry_fn)
    result = mgr.retry("A")
    assert result is True
    called.wait(timeout=2)
    assert called.is_set()


def test_retry_no_callback():
    mgr = ErrorManager()
    mgr.report("A", "Fail")  # No retry callback
    result = mgr.retry("A")
    assert result is False


def test_retry_nonexistent():
    mgr = ErrorManager()
    result = mgr.retry("Nope")
    assert result is False


def test_on_change_callback():
    changes = []
    mgr = ErrorManager()
    mgr.set_on_change(lambda: changes.append(1))

    mgr.report("A", "Error")
    assert len(changes) == 1

    mgr.clear("A")
    assert len(changes) == 2
