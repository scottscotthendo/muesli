"""Audio recording from BlackHole virtual audio device."""

import logging
import queue
import threading
import time

import numpy as np
import sounddevice as sd
from scipy.signal import resample

from meeting_recorder.config import (
    BLACKHOLE_DEVICE_PREFIX,
    CHANNELS,
    CHUNK_DURATION_SECONDS,
    SAMPLE_RATE,
    TARGET_SAMPLE_RATE,
)

logger = logging.getLogger(__name__)


def find_blackhole_device() -> dict | None:
    """Find the BlackHole audio device by name prefix.

    Returns the device info dict, or None if not found.
    """
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        if dev["name"].startswith(BLACKHOLE_DEVICE_PREFIX) and dev["max_input_channels"] > 0:
            logger.info("Found BlackHole device: %s (index %d)", dev["name"], i)
            return {**dev, "index": i}
    return None


def _resample_to_16k(audio: np.ndarray, orig_sr: int) -> np.ndarray:
    """Resample audio from orig_sr to 16kHz mono float32."""
    # Convert to mono if stereo
    if audio.ndim > 1:
        audio = audio.mean(axis=1)

    # Resample
    if orig_sr != TARGET_SAMPLE_RATE:
        num_samples = int(len(audio) * TARGET_SAMPLE_RATE / orig_sr)
        audio = resample(audio, num_samples)

    return audio.astype(np.float32)


class AudioRecorder:
    """Records audio from BlackHole in streaming mode, emitting chunks to a queue."""

    def __init__(self, audio_queue: queue.Queue, stop_event: threading.Event):
        self.audio_queue = audio_queue
        self.stop_event = stop_event
        self._buffer: list[np.ndarray] = []
        self._buffer_lock = threading.Lock()
        self._samples_per_chunk = SAMPLE_RATE * CHUNK_DURATION_SECONDS
        self._samples_collected = 0
        self._chunk_index = 0
        self._device_info: dict | None = None
        self._stream: sd.InputStream | None = None
        # Store resampled chunks for post-recording diarization
        self._all_chunks_16k: list[np.ndarray] = []

    def _find_device(self) -> int:
        """Locate BlackHole and return its device index. Raises if not found."""
        dev = find_blackhole_device()
        if dev is None:
            raise RuntimeError(
                "BlackHole audio device not found. "
                "Please install BlackHole: https://existential.audio/blackhole/"
            )
        self._device_info = dev
        return dev["index"]

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status):
        """Called by sounddevice for each audio block."""
        if status:
            logger.warning("Audio callback status: %s", status)

        if self.stop_event.is_set():
            raise sd.CallbackAbort

        with self._buffer_lock:
            self._buffer.append(indata.copy())
            self._samples_collected += frames

            if self._samples_collected >= self._samples_per_chunk:
                self._flush_chunk()

    def _flush_chunk(self):
        """Concatenate buffered audio, resample, and push to queue."""
        raw = np.concatenate(self._buffer, axis=0)
        self._buffer.clear()
        self._samples_collected = 0

        chunk_16k = _resample_to_16k(raw, SAMPLE_RATE)
        self._all_chunks_16k.append(chunk_16k)
        timestamp_seconds = self._chunk_index * CHUNK_DURATION_SECONDS
        self.audio_queue.put((timestamp_seconds, chunk_16k))
        self._chunk_index += 1
        logger.debug("Queued audio chunk %d", self._chunk_index)

    def run(self):
        """Start recording. Blocks until stop_event is set."""
        device_index = self._find_device()
        actual_sr = int(self._device_info.get("default_samplerate", SAMPLE_RATE))

        logger.info(
            "Starting recording on %s at %d Hz",
            self._device_info["name"],
            actual_sr,
        )

        self._stream = sd.InputStream(
            device=device_index,
            samplerate=actual_sr,
            channels=CHANNELS,
            dtype="float32",
            callback=self._audio_callback,
            blocksize=1024,
        )

        with self._stream:
            while not self.stop_event.is_set():
                time.sleep(0.1)

        # Flush any remaining audio
        with self._buffer_lock:
            if self._buffer:
                self._flush_chunk()

        logger.info("Recording stopped.")

    def get_full_audio_16k(self) -> np.ndarray | None:
        """Return all recorded audio concatenated as 16kHz mono float32.

        Available after recording stops. Used for post-recording diarization.
        """
        if not self._all_chunks_16k:
            return None
        return np.concatenate(self._all_chunks_16k, axis=0)

    def start_thread(self) -> threading.Thread:
        """Launch the recorder in a background thread."""
        t = threading.Thread(target=self.run, name="AudioRecorder", daemon=True)
        t.start()
        return t
