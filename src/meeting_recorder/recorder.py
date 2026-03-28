"""Audio recording from BlackHole virtual audio device + microphone."""

import logging
import queue
import threading
import time

import numpy as np
import sounddevice as sd
from scipy.signal import resample

from meeting_recorder.config import (
    BLACKHOLE_DEVICE_PREFIX,
    CHUNK_DURATION_SECONDS,
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


def find_default_mic() -> dict | None:
    """Find the default input (microphone) device.

    Returns the device info dict, or None if not found.
    """
    try:
        default_idx = sd.default.device[0]  # default input device index
        if default_idx is None or default_idx < 0:
            return None
        dev = sd.query_devices(default_idx)
        if dev["max_input_channels"] > 0:
            logger.info("Found mic device: %s (index %d)", dev["name"], default_idx)
            return {**dev, "index": default_idx}
    except Exception:
        pass

    # Fallback: find first non-BlackHole input device
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        if (
            dev["max_input_channels"] > 0
            and not dev["name"].startswith(BLACKHOLE_DEVICE_PREFIX)
        ):
            logger.info("Found mic device (fallback): %s (index %d)", dev["name"], i)
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
    """Records audio from BlackHole + microphone, mixing both into a single stream."""

    def __init__(self, audio_queue: queue.Queue, stop_event: threading.Event):
        self.audio_queue = audio_queue
        self.stop_event = stop_event
        self._buffer: list[np.ndarray] = []
        self._buffer_lock = threading.Lock()
        self._samples_per_chunk = TARGET_SAMPLE_RATE * CHUNK_DURATION_SECONDS
        self._samples_collected = 0
        self._chunk_index = 0
        self._blackhole_info: dict | None = None
        self._mic_info: dict | None = None
        self._blackhole_stream: sd.InputStream | None = None
        self._mic_stream: sd.InputStream | None = None
        # Mic buffer — resampled to TARGET_SAMPLE_RATE, mixed into main buffer on flush
        self._mic_buffer: list[np.ndarray] = []
        self._mic_lock = threading.Lock()
        # Store resampled chunks for post-recording diarization
        self._all_chunks_16k: list[np.ndarray] = []

    def _find_devices(self) -> int:
        """Locate BlackHole and return its device index. Raises if not found."""
        dev = find_blackhole_device()
        if dev is None:
            raise RuntimeError(
                "BlackHole audio device not found. "
                "Please install BlackHole: https://existential.audio/blackhole/"
            )
        self._blackhole_info = dev

        mic = find_default_mic()
        if mic and mic["index"] != dev["index"]:
            self._mic_info = mic
        else:
            logger.warning("No separate microphone device found — recording system audio only.")

        return dev["index"]

    def _blackhole_callback(self, indata: np.ndarray, frames: int, time_info, status):
        """Called by sounddevice for each BlackHole audio block."""
        if status:
            logger.warning("BlackHole callback status: %s", status)

        if self.stop_event.is_set():
            raise sd.CallbackAbort

        # Resample to 16kHz mono in the callback so buffers align
        chunk = _resample_to_16k(indata.copy(), self._blackhole_sr)

        with self._buffer_lock:
            self._buffer.append(chunk)
            self._samples_collected += len(chunk)

            if self._samples_collected >= self._samples_per_chunk:
                self._flush_chunk()

    def _mic_callback(self, indata: np.ndarray, frames: int, time_info, status):
        """Called by sounddevice for each mic audio block."""
        if status:
            logger.warning("Mic callback status: %s", status)

        if self.stop_event.is_set():
            raise sd.CallbackAbort

        chunk = _resample_to_16k(indata.copy(), self._mic_sr)

        with self._mic_lock:
            self._mic_buffer.append(chunk)

    def _flush_chunk(self):
        """Mix BlackHole and mic buffers, then push to queue."""
        blackhole_audio = np.concatenate(self._buffer, axis=0)
        self._buffer.clear()
        self._samples_collected = 0

        # Mix in mic audio if available
        with self._mic_lock:
            if self._mic_buffer:
                mic_audio = np.concatenate(self._mic_buffer, axis=0)
                self._mic_buffer.clear()
            else:
                mic_audio = None

        if mic_audio is not None:
            # Align lengths — trim or pad the shorter one
            target_len = len(blackhole_audio)
            if len(mic_audio) > target_len:
                mic_audio = mic_audio[:target_len]
            elif len(mic_audio) < target_len:
                mic_audio = np.pad(mic_audio, (0, target_len - len(mic_audio)))

            mixed = blackhole_audio + mic_audio
            # Prevent clipping
            peak = np.abs(mixed).max()
            if peak > 1.0:
                mixed /= peak
        else:
            mixed = blackhole_audio

        self._all_chunks_16k.append(mixed)
        timestamp_seconds = self._chunk_index * CHUNK_DURATION_SECONDS
        self.audio_queue.put((timestamp_seconds, mixed))
        self._chunk_index += 1
        logger.debug("Queued audio chunk %d", self._chunk_index)

    def run(self):
        """Start recording. Blocks until stop_event is set."""
        self._find_devices()

        self._blackhole_sr = int(
            self._blackhole_info.get("default_samplerate", 48000)
        )
        bh_channels = min(int(self._blackhole_info.get("max_input_channels", 2)), 2)

        logger.info(
            "Starting recording on %s at %d Hz",
            self._blackhole_info["name"],
            self._blackhole_sr,
        )

        self._blackhole_stream = sd.InputStream(
            device=self._blackhole_info["index"],
            samplerate=self._blackhole_sr,
            channels=bh_channels,
            dtype="float32",
            callback=self._blackhole_callback,
            blocksize=1024,
        )

        if self._mic_info:
            self._mic_sr = int(self._mic_info.get("default_samplerate", 48000))
            mic_channels = min(int(self._mic_info.get("max_input_channels", 1)), 1)
            logger.info(
                "Also recording mic: %s at %d Hz",
                self._mic_info["name"],
                self._mic_sr,
            )
            self._mic_stream = sd.InputStream(
                device=self._mic_info["index"],
                samplerate=self._mic_sr,
                channels=mic_channels,
                dtype="float32",
                callback=self._mic_callback,
                blocksize=1024,
            )

        with self._blackhole_stream:
            if self._mic_stream:
                with self._mic_stream:
                    while not self.stop_event.is_set():
                        time.sleep(0.1)
            else:
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
