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
    CHUNK_DURATION_SECONDS,
    DIARIZATION_ENABLED,
    ICON_ERROR,
    ICON_IDLE,
    ICON_RECORDING,
    TARGET_SAMPLE_RATE,
)
from meeting_recorder.diarizer import Diarizer, assign_speakers_to_transcript
from meeting_recorder.error_manager import ErrorManager
from meeting_recorder.recorder import AudioRecorder
from meeting_recorder.summarizer import Summarizer
from meeting_recorder.transcriber import Transcriber
from meeting_recorder.transcript_writer import TranscriptWriter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Error component keys
ERR_WHISPER = "Whisper Model"
ERR_SUMMARIZER = "Summarizer Model"
ERR_DIARIZER = "Diarization Model"
ERR_RECORDING = "Recording"
ERR_SUMMARIZATION = "Summarization"
ERR_DIARIZATION = "Diarization"
ERR_CALENDAR = "Calendar"


class MeetingRecorderApp(rumps.App):
    def __init__(self):
        super().__init__(APP_NAME, title=ICON_IDLE, quit_button=None)

        # Error manager
        self._errors = ErrorManager()
        self._errors.set_on_change(self._rebuild_error_menu)

        # UI items
        self.start_stop_button = rumps.MenuItem("Start Recording", callback=self.on_start_stop)
        self.status_item = rumps.MenuItem("Idle")
        self.status_item.set_callback(None)
        self._error_separator = rumps.separator
        self._error_menu_items: list[rumps.MenuItem] = []
        self.quit_button = rumps.MenuItem("Quit", callback=self.on_quit)

        self.menu = [
            self.start_stop_button,
            self.status_item,
            self._error_separator,
            self.quit_button,
        ]

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
        # Transcript segments collected during recording for diarization
        self._transcript_segments: list[tuple[int, str]] = []

        # Calendar
        self._calendar = CalendarClient()
        self._pending_event: CalendarEvent | None = None
        self._last_prompted_event: str | None = None
        self._calendar_authenticated = False

        # Pre-load whisper model
        self._transcriber_preload = Transcriber(queue.Queue(), queue.Queue(), threading.Event())
        self._load_whisper()

        # Pre-load summarization model
        self._summarizer = Summarizer()
        self._load_summarizer()

        # Pre-load diarization pipeline
        self._diarizer: Diarizer | None = None
        if DIARIZATION_ENABLED:
            self._diarizer = Diarizer()
            self._load_diarizer()

        # Timers
        self._ui_timer = rumps.Timer(self._on_ui_tick, 1)
        self._calendar_timer = rumps.Timer(self._on_calendar_tick, CALENDAR_CHECK_INTERVAL_SECONDS)
        self._calendar_timer.start()

    # ── Model loading with error handling ──────────────────────────

    def _load_whisper(self):
        """Load whisper model, reporting errors to ErrorManager."""

        def _do_load():
            self._transcriber_preload.load_model()
            if self._transcriber_preload._loading_error:
                self._errors.report(
                    ERR_WHISPER,
                    str(self._transcriber_preload._loading_error),
                    retry_callback=self._retry_whisper,
                )
            else:
                self._errors.clear(ERR_WHISPER)

        threading.Thread(target=_do_load, name="WhisperLoader", daemon=True).start()

    def _retry_whisper(self):
        self._transcriber_preload._model = None
        self._transcriber_preload._model_ready.clear()
        self._transcriber_preload._loading_error = None
        self._transcriber_preload.load_model()
        if self._transcriber_preload._loading_error:
            self._errors.report(
                ERR_WHISPER,
                str(self._transcriber_preload._loading_error),
                retry_callback=self._retry_whisper,
            )
        else:
            self._errors.clear(ERR_WHISPER)

    def _load_summarizer(self):
        def _do_load():
            self._summarizer.load_model()
            if self._summarizer._loading_error:
                self._errors.report(
                    ERR_SUMMARIZER,
                    str(self._summarizer._loading_error),
                    retry_callback=self._retry_summarizer,
                )
            else:
                self._errors.clear(ERR_SUMMARIZER)

        threading.Thread(target=_do_load, name="SummarizerLoader", daemon=True).start()

    def _retry_summarizer(self):
        self._summarizer._model = None
        self._summarizer._model_ready.clear()
        self._summarizer._loading_error = None
        self._summarizer.load_model()
        if self._summarizer._loading_error:
            self._errors.report(
                ERR_SUMMARIZER,
                str(self._summarizer._loading_error),
                retry_callback=self._retry_summarizer,
            )
        else:
            self._errors.clear(ERR_SUMMARIZER)

    def _load_diarizer(self):
        def _do_load():
            self._diarizer.load_pipeline()
            if self._diarizer.loading_error:
                self._errors.report(
                    ERR_DIARIZER,
                    str(self._diarizer.loading_error),
                    retry_callback=self._retry_diarizer,
                )
            else:
                self._errors.clear(ERR_DIARIZER)

        threading.Thread(target=_do_load, name="DiarizerLoader", daemon=True).start()

    def _retry_diarizer(self):
        self._diarizer = Diarizer()
        self._diarizer.load_pipeline()
        if self._diarizer.loading_error:
            self._errors.report(
                ERR_DIARIZER,
                str(self._diarizer.loading_error),
                retry_callback=self._retry_diarizer,
            )
        else:
            self._errors.clear(ERR_DIARIZER)

    # ── Error menu ─────────────────────────────────────────────────

    def _rebuild_error_menu(self):
        """Rebuild the error section of the menu."""
        # Remove old error items
        for item in self._error_menu_items:
            if item.title in self.menu:
                del self.menu[item.title]
        self._error_menu_items.clear()

        errors = self._errors.get_errors()
        if not errors:
            self.title = ICON_RECORDING if self._recording else ICON_IDLE
            return

        for error in errors:
            # Truncate long messages for the menu
            short_msg = error.message[:80] + "..." if len(error.message) > 80 else error.message
            label = f"{ICON_ERROR} {error.component}: {short_msg}"

            if error.retry_callback:
                item = rumps.MenuItem(
                    label,
                    callback=lambda sender, comp=error.component: self._on_retry_click(comp),
                )
            else:
                item = rumps.MenuItem(label)
                item.set_callback(None)

            self._error_menu_items.append(item)
            # Insert before Quit
            self.menu.insert_before(self.quit_button.title, item)

    def _on_retry_click(self, component: str):
        """Handle clicking an error menu item to retry."""
        response = rumps.alert(
            title=f"Retry {component}?",
            message=f"Would you like to retry loading {component}?",
            ok="Retry",
            cancel="Dismiss",
        )
        if response == 1:
            self._errors.retry(component)

    # ── UI timer ───────────────────────────────────────────────────

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
                    self._transcript_segments.append((ts, text))
                except queue.Empty:
                    break

    # ── Calendar ───────────────────────────────────────────────────

    def _on_calendar_tick(self, _timer):
        """Called periodically to check for upcoming calendar events."""
        if self._recording:
            return

        try:
            if not self._calendar_authenticated:
                self._calendar.authenticate()
                self._calendar_authenticated = True
                self._errors.clear(ERR_CALENDAR)

            event = self._calendar.get_upcoming_event()
            if event and event.title != self._last_prompted_event:
                self._pending_event = event
                self._last_prompted_event = event.title
                self._show_event_prompt(event)
        except FileNotFoundError as e:
            logger.warning("Calendar not configured: %s", e)
            self._calendar_timer.stop()
        except Exception as e:
            logger.exception("Calendar check failed")
            self._errors.report(
                ERR_CALENDAR,
                str(e),
                retry_callback=self._retry_calendar,
            )

    def _retry_calendar(self):
        self._calendar_authenticated = False
        self._calendar = CalendarClient()
        try:
            self._calendar.authenticate()
            self._calendar_authenticated = True
            self._errors.clear(ERR_CALENDAR)
        except Exception as e:
            self._errors.report(ERR_CALENDAR, str(e), retry_callback=self._retry_calendar)

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

    # ── Recording ──────────────────────────────────────────────────

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
        self._transcript_segments = []

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
            self._errors.report(ERR_RECORDING, str(e))
            rumps.alert("Recording Error", str(e))
            self._recording = False
            self._writer.close()
            return

        self._errors.clear(ERR_RECORDING)

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
                    self._transcript_segments.append((ts, text))
                except queue.Empty:
                    break

        # Close writer
        file_path = None
        writer = self._writer
        if writer:
            file_path = writer.file_path
            writer.close()

        # Grab full audio for diarization before recorder is cleaned up
        full_audio = self._recorder.get_full_audio_16k() if self._recorder else None

        # Update UI
        self.start_stop_button.title = "Start Recording"
        self.title = ICON_IDLE

        if file_path:
            self.status_item.title = "Processing..."
            logger.info("Recording stopped. Post-processing: %s", file_path)

            # Run diarization + summarization in background
            transcript_segments = list(self._transcript_segments)

            def _post_process():
                # 1. Speaker diarization
                if self._diarizer and self._diarizer.is_ready() and full_audio is not None:
                    try:
                        self.status_item.title = "Identifying speakers..."
                        speaker_segments = self._diarizer.diarize(full_audio, TARGET_SAMPLE_RATE)
                        if speaker_segments:
                            labelled = assign_speakers_to_transcript(
                                transcript_segments,
                                speaker_segments,
                                chunk_duration=CHUNK_DURATION_SECONDS,
                            )
                            writer.update_with_speakers(labelled)
                        self._errors.clear(ERR_DIARIZATION)
                    except Exception as e:
                        logger.exception("Diarization failed")
                        self._errors.report(
                            ERR_DIARIZATION,
                            str(e),
                        )

                # 2. Summarization
                try:
                    self.status_item.title = "Summarizing..."
                    transcript = writer.get_transcript_text()
                    if transcript.strip():
                        summary = self._summarizer.summarize(transcript)
                        if summary:
                            writer.insert_summary(summary)
                    self._errors.clear(ERR_SUMMARIZATION)
                except Exception as e:
                    logger.exception("Summarization failed")
                    self._errors.report(
                        ERR_SUMMARIZATION,
                        str(e),
                    )

                # Done
                self.status_item.title = f"Saved: {file_path}"
                rumps.notification(
                    APP_NAME,
                    "Recording saved",
                    str(file_path),
                )

            threading.Thread(target=_post_process, name="PostProcess", daemon=True).start()
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
