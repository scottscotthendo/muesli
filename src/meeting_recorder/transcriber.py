"""Local transcription using faster-whisper."""

import logging
import queue
import threading

import numpy as np

from meeting_recorder.config import (
    WHISPER_COMPUTE_TYPE,
    WHISPER_DEVICE,
    WHISPER_MODEL,
)

logger = logging.getLogger(__name__)


class Transcriber:
    """Consumes audio chunks from a queue, transcribes them, and pushes results."""

    def __init__(
        self,
        audio_queue: queue.Queue,
        results_queue: queue.Queue,
        stop_event: threading.Event,
    ):
        self.audio_queue = audio_queue
        self.results_queue = results_queue
        self.stop_event = stop_event
        self._model = None
        self._model_ready = threading.Event()
        self._loading_error: Exception | None = None

    def load_model(self):
        """Load the faster-whisper model. Safe to call from any thread."""
        try:
            from faster_whisper import WhisperModel

            logger.info("Loading whisper model '%s' (this may download ~460MB on first run)...", WHISPER_MODEL)
            self._model = WhisperModel(
                WHISPER_MODEL,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE_TYPE,
            )
            logger.info("Whisper model loaded successfully.")
        except Exception as e:
            self._loading_error = e
            logger.error("Failed to load whisper model: %s", e)
        finally:
            self._model_ready.set()

    def load_model_async(self) -> threading.Thread:
        """Load the model in a background thread. Returns the thread."""
        t = threading.Thread(target=self.load_model, name="ModelLoader", daemon=True)
        t.start()
        return t

    def is_model_ready(self) -> bool:
        return self._model_ready.is_set()

    def wait_for_model(self, timeout: float | None = None) -> bool:
        """Block until the model is loaded. Returns True if ready, False on timeout."""
        return self._model_ready.wait(timeout=timeout)

    def _transcribe_chunk(self, audio: np.ndarray) -> str:
        """Transcribe a single audio chunk. Returns the transcribed text."""
        if self._model is None:
            if self._loading_error:
                raise self._loading_error
            raise RuntimeError("Model not loaded")

        segments, info = self._model.transcribe(
            audio,
            beam_size=5,
            language="en",
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )

        text_parts = []
        for segment in segments:
            text_parts.append(segment.text.strip())

        return " ".join(text_parts)

    def run(self):
        """Main loop: consume audio chunks, transcribe, push results."""
        # Wait for model to be ready
        logger.info("Transcriber waiting for model...")
        self._model_ready.wait()

        if self._loading_error:
            logger.error("Cannot transcribe — model failed to load: %s", self._loading_error)
            return

        logger.info("Transcriber ready.")

        while not self.stop_event.is_set():
            try:
                timestamp_seconds, audio = self.audio_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                text = self._transcribe_chunk(audio)
                if text:
                    self.results_queue.put((timestamp_seconds, text))
                    logger.debug("[%ds] %s", timestamp_seconds, text[:80])
                else:
                    logger.debug("[%ds] (silence)", timestamp_seconds)
            except Exception:
                logger.exception("Transcription error at %ds", timestamp_seconds)

        # Drain remaining items
        while not self.audio_queue.empty():
            try:
                timestamp_seconds, audio = self.audio_queue.get_nowait()
                text = self._transcribe_chunk(audio)
                if text:
                    self.results_queue.put((timestamp_seconds, text))
            except queue.Empty:
                break
            except Exception:
                logger.exception("Transcription error during drain")

        logger.info("Transcriber stopped.")

    def start_thread(self) -> threading.Thread:
        """Launch the transcriber in a background thread."""
        t = threading.Thread(target=self.run, name="Transcriber", daemon=True)
        t.start()
        return t
