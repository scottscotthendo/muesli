"""Error tracking and retry management for the meeting recorder UI."""

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class AppError:
    """A tracked application error with optional retry."""

    component: str
    message: str
    retry_callback: Callable[[], None] | None = None
    retrying: bool = field(default=False, repr=False)


class ErrorManager:
    """Thread-safe error tracker with retry support.

    Components report errors here. The UI layer reads active errors
    and can trigger retries.
    """

    def __init__(self):
        self._errors: dict[str, AppError] = {}
        self._lock = threading.Lock()
        self._on_change: Callable[[], None] | None = None

    def set_on_change(self, callback: Callable[[], None] | None):
        """Set a callback invoked whenever the error list changes."""
        self._on_change = callback

    def report(
        self,
        component: str,
        message: str,
        retry_callback: Callable[[], None] | None = None,
    ):
        """Report an error from a component. Replaces any prior error for that component."""
        with self._lock:
            self._errors[component] = AppError(
                component=component,
                message=message,
                retry_callback=retry_callback,
            )
        logger.error("[%s] %s", component, message)
        if self._on_change:
            self._on_change()

    def clear(self, component: str):
        """Clear the error for a component (e.g. after successful retry)."""
        with self._lock:
            removed = self._errors.pop(component, None)
        if removed and self._on_change:
            self._on_change()

    def clear_all(self):
        """Clear all errors."""
        with self._lock:
            self._errors.clear()
        if self._on_change:
            self._on_change()

    def get_errors(self) -> list[AppError]:
        """Return a snapshot of all active errors."""
        with self._lock:
            return list(self._errors.values())

    def has_errors(self) -> bool:
        with self._lock:
            return len(self._errors) > 0

    def retry(self, component: str) -> bool:
        """Retry the failed operation for a component.

        Runs the retry callback in a background thread. Returns True if
        a retry was initiated, False if no retryable error exists.
        """
        with self._lock:
            error = self._errors.get(component)
            if error is None or error.retry_callback is None or error.retrying:
                return False
            error.retrying = True
            callback = error.retry_callback

        def _do_retry():
            try:
                logger.info("[%s] Retrying...", component)
                callback()
                # If callback didn't re-report, clear the error
                with self._lock:
                    current = self._errors.get(component)
                    if current is not None and current.retrying:
                        del self._errors[component]
                if self._on_change:
                    self._on_change()
            except Exception as e:
                self.report(component, str(e), retry_callback=callback)

        threading.Thread(target=_do_retry, name=f"Retry-{component}", daemon=True).start()
        return True
