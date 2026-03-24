"""Menubar application — the main entry point."""

import logging
import queue
import threading
import time
from datetime import datetime

import rumps

from meeting_recorder.calendar_client import CalendarClient, CalendarEvent
from meeting_recorder.config import (
    APP_NAME,
    CALENDAR_CHECK_INTERVAL_SECONDS,
    ICON_IDLE,
    ICON_RECORDING,
)
from meeting_recorder.recorder import AudioRecorder
from meeting_recorder.summarizer import Summarizer
from meeting_recorder.transcriber import Transcriber
from meeting_recorder.transcript_writer import TranscriptWriter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


class MeetingRecorderApp(rumps.App):
    def __init__(self):
        super().__init__(APP_NAME, title=ICON_IDLE, quit_button=None)

        # UI items
        self.start_stop_button = rumps.MenuItem("Start Recording", callback=self.on_start_stop)
        self.status_item = rumps.MenuItem("Idle")
        self.status_item.set_callback(None)
        self.quit_button = rumps.MenuItem("Quit", callback=self.on_quit)

        self.menu = [self.start_stop_button, self.status_item, rumps.separator, self.quit_button]

        # State
        self._recording = False
        self._start_time: datetime | None = None
        self._stop_event: threading.Event | None = None
        self._audio_queue: queue.Queue | None = None
        self._results_queue: queue.Queue | None = None
        self._recorder: AudioRecorder | None = None
        self._transcriber: Transcriber | None = None
        self._writer: TranscriptWriter | None = None
        self._recorder_thread: threading.Thread | None = None
        self._transcriber_thread: threading.Thread | None = None

        # Calendar
        self._calendar = CalendarClient()
        self._pending_event: CalendarEvent | None = None
        self._last_prompted_event: str | None = None
        self._calendar_authenticated = False

        # Pre-load whisper model
        self._transcriber_preload = Transcriber(queue.Queue(), queue.Queue(), threading.Event())
        self._transcriber_preload.load_model_async()

        # Pre-load summarization model
        self._summarizer = Summarizer()
        self._summarizer.load_model_async()

        # Timers
        self._ui_timer = rumps.Timer(self._on_ui_tick, 1)
        self._calendar_timer = rumps.Timer(self._on_calendar_tick, CALENDAR_CHECK_INTERVAL_SECONDS)
        self._calendar_timer.start()

    def _on_ui_tick(self, _timer):
        """Called every second while recording to update elapsed time and poll results."""
        if not self._recording:
            return

        # Update elapsed time
        elapsed = time.time() - self._start_time.timestamp()
        minutes = int(elapsed) // 60
        seconds = int(elapsed) % 60
        self.title = f"{ICON_RECORDING} {minutes:02d}:{seconds:02d}"

        # Poll transcription results
        if self._results_queue and self._writer:
            while True:
                try:
                    ts, text = self._results_queue.get_nowait()
                    self._writer.append_segment(ts, text)
                except queue.Empty:
                    break

    def _on_calendar_tick(self, _timer):
        """Called periodically to check for upcoming calendar events."""
        if self._recording:
            return

        try:
            if not self._calendar_authenticated:
                self._calendar.authenticate()
                self._calendar_authenticated = True

            event = self._calendar.get_upcoming_event()
            if event and event.title != self._last_prompted_event:
                self._pending_event = event
                self._last_prompted_event = event.title
                self._show_event_prompt(event)
        except FileNotFoundError as e:
            logger.warning("Calendar not configured: %s", e)
            self._calendar_timer.stop()
        except Exception:
            logger.exception("Calendar check failed")

    def _show_event_prompt(self, event: CalendarEvent):
        """Show a notification/alert for a detected calendar event."""
        response = rumps.alert(
            title="Meeting Detected",
            message=f"Detected: {event.title}\n\nStart recording?",
            ok="Start Recording",
            cancel="Dismiss",
        )
        if response == 1:  # OK clicked
            self._start_recording(event)

    def on_start_stop(self, _sender):
        """Toggle recording on/off."""
        if self._recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self, event: CalendarEvent | None = None):
        """Begin recording and transcription."""
        if self._recording:
            return

        self._recording = True
        self._start_time = datetime.now()

        # Set up queues and stop event
        self._stop_event = threading.Event()
        self._audio_queue = queue.Queue()
        self._results_queue = queue.Queue()

        # Set up transcript writer
        if event:
            self._writer = TranscriptWriter(
                title=event.title,
                attendees=event.attendees,
                start_time=self._start_time,
            )
        else:
            self._writer = TranscriptWriter(start_time=self._start_time)
        self._writer.open()

        # Set up transcriber (reuse pre-loaded model)
        self._transcriber = Transcriber(
            self._audio_queue, self._results_queue, self._stop_event
        )
        # Share the pre-loaded model
        self._transcriber._model = self._transcriber_preload._model
        self._transcriber._model_ready = self._transcriber_preload._model_ready
        self._transcriber._loading_error = self._transcriber_preload._loading_error

        # Set up recorder
        self._recorder = AudioRecorder(self._audio_queue, self._stop_event)

        # Start threads
        try:
            self._recorder_thread = self._recorder.start_thread()
            self._transcriber_thread = self._transcriber.start_thread()
        except RuntimeError as e:
            rumps.alert("Recording Error", str(e))
            self._recording = False
            self._writer.close()
            return

        # Update UI
        self.start_stop_button.title = "Stop Recording"
        self.status_item.title = "Recording..."
        self.title = f"{ICON_RECORDING} 00:00"
        self._ui_timer.start()

        logger.info("Recording started.")

    def _stop_recording(self):
        """Stop recording and finalize transcript."""
        if not self._recording:
            return

        self._recording = False
        self._ui_timer.stop()

        # Signal threads to stop
        if self._stop_event:
            self._stop_event.set()

        # Wait for threads
        if self._recorder_thread:
            self._recorder_thread.join(timeout=5)
        if self._transcriber_thread:
            self._transcriber_thread.join(timeout=10)

        # Drain any remaining results
        if self._results_queue and self._writer:
            while True:
                try:
                    ts, text = self._results_queue.get_nowait()
                    self._writer.append_segment(ts, text)
                except queue.Empty:
                    break

        # Close writer and run summarization
        file_path = None
        writer = self._writer
        if writer:
            file_path = writer.file_path
            writer.close()

        # Update UI
        self.start_stop_button.title = "Start Recording"
        self.title = ICON_IDLE

        if file_path:
            self.status_item.title = "Summarizing..."
            logger.info("Recording stopped. Summarizing: %s", file_path)

            # Run summarization in background thread to keep UI responsive
            def _summarize():
                try:
                    transcript = writer.get_transcript_text()
                    if transcript.strip():
                        summary = self._summarizer.summarize(transcript)
                        if summary:
                            writer.insert_summary(summary)
                except Exception:
                    logger.exception("Summarization failed")

                # Update UI on completion (rumps is thread-safe for title/menu updates)
                self.status_item.title = f"Saved: {file_path}"
                rumps.notification(
                    APP_NAME,
                    "Recording saved",
                    str(file_path),
                )

            threading.Thread(target=_summarize, name="Summarizer", daemon=True).start()
        else:
            self.status_item.title = "Idle"

    def on_quit(self, _sender):
        """Clean shutdown."""
        if self._recording:
            self._stop_recording()
        rumps.quit_application()


def main():
    MeetingRecorderApp().run()


if __name__ == "__main__":
    main()
