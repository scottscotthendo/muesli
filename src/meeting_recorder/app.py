"""Menubar application — the main entry point."""

import gc
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
    MAX_RECORDING_HOURS,
    MIN_DISK_SPACE_MB,
    NUDGE_SCHEDULE_MINUTES,
    RECOVERY_DIR,
    SILENCE_AUTO_STOP_SECONDS,
    SILENCE_GRACE_SECONDS,
    TARGET_SAMPLE_RATE,
)
from meeting_recorder.diarizer import Diarizer, assign_speakers_to_transcript
from meeting_recorder.error_manager import ErrorManager
from meeting_recorder.recorder import AudioRecorder
from meeting_recorder.summarizer import Summarizer
from meeting_recorder.transcriber import Transcriber
from meeting_recorder.notion_client import push_to_notion
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
ERR_AUDIO_TAP = "System Audio"


def _free_memory():
    """Force garbage collection to reclaim memory from unloaded models."""
    gc.collect()


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
        self.level_item = rumps.MenuItem("Level: --")
        self.level_item.set_callback(None)
        self._error_separator = rumps.separator
        self._error_menu_items: list[rumps.MenuItem] = []
        self.quit_button = rumps.MenuItem("Quit", callback=self.on_quit)

        self.menu = [
            self.start_stop_button,
            self.status_item,
            self.level_item,
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

        # Safety guard state
        self._next_nudge_index: int = 0
        self._last_nudge_time: float = 0.0
        self._disk_check_counter: int = 0

        # Calendar
        self._calendar = CalendarClient()
        self._pending_event: CalendarEvent | None = None
        self._last_prompted_event: str | None = None
        self._calendar_authenticated = False

        # Pre-load whisper model only — other models loaded on demand
        self._transcriber_preload = Transcriber(queue.Queue(), queue.Queue(), threading.Event())
        self._load_whisper()

        # Check for recovery files from a previous crash
        self._check_recovery_files()

        # Timers
        self._ui_timer = rumps.Timer(self._on_ui_tick, 1)
        self._calendar_timer = rumps.Timer(self._on_calendar_tick, CALENDAR_CHECK_INTERVAL_SECONDS)
        self._calendar_timer.start()

    # ── Crash recovery ─────────────────────────────────────────────

    def _check_recovery_files(self):
        """Check for recovery WAV files from a previous crash and notify user."""
        if not RECOVERY_DIR.exists():
            return
        wav_files = sorted(RECOVERY_DIR.glob("recovery_*.wav"))
        if not wav_files:
            return

        count = len(wav_files)
        total_mb = sum(f.stat().st_size for f in wav_files) / (1024 * 1024)
        logger.info(
            "Found %d recovery file(s) (%.1f MB) from a previous session.",
            count,
            total_mb,
        )
        response = rumps.alert(
            title="Recovered Audio Found",
            message=(
                f"Found {count} audio file(s) ({total_mb:.1f} MB) from a previous "
                f"session that may not have saved properly.\n\n"
                f"Location: {RECOVERY_DIR}\n\n"
                "Keep them for manual recovery, or delete them?"
            ),
            ok="Keep",
            cancel="Delete",
        )
        if response == 0:  # Cancel = Delete
            for f in wav_files:
                try:
                    f.unlink()
                except OSError:
                    logger.warning("Could not delete %s", f)
            logger.info("Recovery files deleted.")

    # ── Model loading with error handling ──────────────────────────

    def _load_whisper(self):
        """Load whisper model in background, reporting errors to ErrorManager."""

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
        _free_memory()
        self._transcriber_preload.load_model()
        if self._transcriber_preload._loading_error:
            self._errors.report(
                ERR_WHISPER,
                str(self._transcriber_preload._loading_error),
                retry_callback=self._retry_whisper,
            )
        else:
            self._errors.clear(ERR_WHISPER)

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

        # Update audio level indicator
        if self._recorder:
            rms, peak = self._recorder.get_audio_levels()
            # Show a simple bar: 0-5 blocks based on RMS
            bar_count = min(int(rms * 50), 5)
            bar = "\u2588" * bar_count + "\u2591" * (5 - bar_count)
            self.level_item.title = f"Level: [{bar}]"

        # Poll transcription results
        if self._results_queue and self._writer:
            while True:
                try:
                    ts, text = self._results_queue.get_nowait()
                    self._writer.append_segment(ts, text)
                    self._transcript_segments.append((ts, text))
                except queue.Empty:
                    break

        # Run safety guards
        self._check_safety_guards(elapsed)

    # ── Safety guards ──────────────────────────────────────────────

    def _check_safety_guards(self, elapsed_seconds: float):
        """Run all safety checks: hard cap, silence auto-stop, disk space, nudges."""
        elapsed_hours = elapsed_seconds / 3600
        elapsed_minutes = elapsed_seconds / 60

        # Tier 4: Hard time cap
        if elapsed_hours >= MAX_RECORDING_HOURS:
            logger.warning("Hard time cap reached (%d hours). Auto-stopping.", MAX_RECORDING_HOURS)
            rumps.notification(
                APP_NAME,
                "Recording Auto-Stopped",
                f"Maximum recording duration ({MAX_RECORDING_HOURS}h) reached.",
            )
            self._stop_recording()
            return

        # Warn at 90% of hard cap
        cap_warn_hours = MAX_RECORDING_HOURS * 0.9
        if elapsed_hours >= cap_warn_hours and elapsed_hours < cap_warn_hours + (1 / 3600):
            rumps.notification(
                APP_NAME,
                "Recording Time Warning",
                f"Recording will auto-stop in {int((MAX_RECORDING_HOURS - elapsed_hours) * 60)} minutes.",
            )

        # Tier 3: Silence auto-stop (with grace period)
        if self._recorder:
            silence_duration = self._recorder.get_silence_duration()
            if silence_duration >= SILENCE_AUTO_STOP_SECONDS + SILENCE_GRACE_SECONDS:
                logger.warning(
                    "Prolonged silence (%ds). Auto-stopping recording.",
                    int(silence_duration),
                )
                rumps.notification(
                    APP_NAME,
                    "Recording Auto-Stopped",
                    f"No audio detected for {int(silence_duration // 60)} minutes.",
                )
                self._stop_recording()
                return

        # Tier 2: Disk space check (every 10 seconds to avoid I/O spam)
        self._disk_check_counter += 1
        if self._disk_check_counter >= 10:
            self._disk_check_counter = 0
            if self._recorder and not self._recorder.check_disk_space():
                logger.warning("Low disk space. Auto-stopping recording.")
                rumps.notification(
                    APP_NAME,
                    "Recording Auto-Stopped",
                    f"Disk space dropped below {MIN_DISK_SPACE_MB} MB.",
                )
                self._stop_recording()
                return

        # Tier 1: Escalating nudges
        self._check_nudge(elapsed_minutes)

    def _check_nudge(self, elapsed_minutes: float):
        """Send escalating reminder notifications that recording is still active."""
        if self._next_nudge_index < len(NUDGE_SCHEDULE_MINUTES):
            threshold = NUDGE_SCHEDULE_MINUTES[self._next_nudge_index]
        else:
            # After the schedule is exhausted, nudge every 30 minutes
            last = NUDGE_SCHEDULE_MINUTES[-1] if NUDGE_SCHEDULE_MINUTES else 30
            intervals_past = self._next_nudge_index - len(NUDGE_SCHEDULE_MINUTES) + 1
            threshold = last + (intervals_past * 30)

        if elapsed_minutes >= threshold:
            rumps.notification(
                APP_NAME,
                "Still Recording",
                f"Recording has been running for {int(elapsed_minutes)} minutes.",
            )
            self._next_nudge_index += 1

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

    # ── Audio reliability callbacks ──────────────────────────────

    def _on_silence_warning(self):
        """Called by the recorder when audio has been silent too long."""
        self._errors.report(
            "Silence",
            "No audio detected — check your audio source.",
        )
        rumps.notification(
            APP_NAME,
            "No Audio Detected",
            "Recording is running but no audio is being captured. Check your audio source.",
        )

    def _on_audio_tap_error(self, message: str):
        """Called by the recorder when the audio_tap subprocess crashes."""
        self._errors.report(ERR_AUDIO_TAP, message)
        rumps.notification(
            APP_NAME,
            "System Audio Capture Failed",
            message,
        )

    def _on_low_disk_space(self, free_mb: float):
        """Called by the recorder when disk space is critically low."""
        rumps.notification(
            APP_NAME,
            "Low Disk Space",
            f"Only {free_mb:.0f} MB remaining. Recording will stop to prevent data loss.",
        )

    def _on_mic_reconnect(self, device_name: str):
        """Called by the recorder when the mic successfully reconnects."""
        self._errors.clear("Microphone")
        rumps.notification(
            APP_NAME,
            "Microphone Reconnected",
            f"Now using: {device_name}",
        )

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

        # Reset safety guard state
        self._next_nudge_index = 0
        self._last_nudge_time = 0.0
        self._disk_check_counter = 0

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

        # Set up recorder with error/silence callbacks
        self._recorder = AudioRecorder(self._audio_queue, self._stop_event)
        self._recorder._on_silence_warning = self._on_silence_warning
        self._recorder._on_audio_tap_error = self._on_audio_tap_error
        self._recorder._on_low_disk_space = self._on_low_disk_space
        self._recorder._on_mic_reconnect = self._on_mic_reconnect

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
        self.level_item.title = "Level: --"
        self.title = ICON_IDLE
        self._errors.clear("Silence")
        self._errors.clear(ERR_AUDIO_TAP)

        if file_path:
            self.status_item.title = "Processing..."
            logger.info("Recording stopped. Post-processing: %s", file_path)

            # Run post-processing in background with sequential model lifecycle
            transcript_segments = list(self._transcript_segments)
            recorder = self._recorder

            def _post_process():
                self._run_post_processing(writer, file_path, full_audio, transcript_segments)
                # Recording completed successfully — delete recovery file
                if recorder:
                    recorder.delete_recovery_file()

            threading.Thread(target=_post_process, name="PostProcess", daemon=True).start()
        else:
            self.status_item.title = "Idle"

    def _run_post_processing(self, writer, file_path, full_audio, transcript_segments):
        """Run diarization and summarization sequentially, loading one model at a time.

        Model lifecycle:
        1. Unload whisper (no longer needed)
        2. Load diarizer -> run -> unload diarizer
        3. Load summarizer -> run -> unload summarizer
        4. Reload whisper (ready for next recording)
        """
        # Step 1: Unload whisper to free ~460MB
        logger.info("Unloading whisper model to free memory for post-processing...")
        self._transcriber_preload.unload_model()
        _free_memory()

        # Step 2: Speaker diarization
        if DIARIZATION_ENABLED and full_audio is not None:
            self.status_item.title = "Identifying speakers..."
            diarizer = Diarizer()
            try:
                diarizer.load_pipeline()
                if diarizer.is_ready():
                    speaker_segments = diarizer.diarize(full_audio, TARGET_SAMPLE_RATE)
                    if speaker_segments:
                        labelled = assign_speakers_to_transcript(
                            transcript_segments,
                            speaker_segments,
                            chunk_duration=CHUNK_DURATION_SECONDS,
                        )
                        writer.update_with_speakers(labelled)
                    self._errors.clear(ERR_DIARIZATION)
                elif diarizer.loading_error:
                    self._errors.report(ERR_DIARIZER, str(diarizer.loading_error))
            except Exception as e:
                logger.exception("Diarization failed")
                self._errors.report(ERR_DIARIZATION, str(e))
            finally:
                diarizer.unload_pipeline()
                del diarizer
                _free_memory()

        # Free the raw audio — no longer needed
        del full_audio
        _free_memory()

        # Step 3: Summarization
        self.status_item.title = "Summarizing..."
        summary = None
        summarizer = Summarizer()
        try:
            summarizer.load_model()
            if summarizer._loading_error:
                self._errors.report(ERR_SUMMARIZER, str(summarizer._loading_error))
            else:
                transcript = writer.get_transcript_text()
                if transcript.strip():
                    summary = summarizer.summarize(transcript)
                    if summary:
                        writer.insert_summary(summary)
                self._errors.clear(ERR_SUMMARIZATION)
        except Exception as e:
            logger.exception("Summarization failed")
            self._errors.report(ERR_SUMMARIZATION, str(e))
        finally:
            summarizer.unload_model()
            del summarizer
            _free_memory()

        # Step 4: Push to Notion
        self.status_item.title = "Syncing to Notion..."
        try:
            push_to_notion(
                title=writer.title,
                start_time=writer.start_time,
                attendees=writer.attendees,
                summary=summary,
                transcript_path=file_path,
            )
        except Exception:
            logger.exception("Notion sync failed")

        # Step 5: Reload whisper for the next recording
        logger.info("Reloading whisper model for next recording...")
        self.status_item.title = "Reloading transcription model..."
        self._load_whisper()

        # Done
        self.status_item.title = f"Saved: {file_path}"
        rumps.notification(
            APP_NAME,
            "Recording saved",
            str(file_path),
        )

    def on_quit(self, _sender):
        """Clean shutdown."""
        if self._recording:
            self._stop_recording()
        rumps.quit_application()


def main():
    import multiprocessing
    multiprocessing.freeze_support()
    MeetingRecorderApp().run()


if __name__ == "__main__":
    main()
