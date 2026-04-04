"""Audio recording via ScreenCaptureKit (system audio) + microphone."""

import logging
import os
import queue
import signal
import struct
import subprocess
import threading
import time
import wave

import numpy as np
import sounddevice as sd
from scipy.signal import resample

from meeting_recorder.config import (
    CHUNK_DURATION_SECONDS,
    RECOVERY_DIR,
    SILENCE_WARNING_SECONDS,
    TARGET_SAMPLE_RATE,
)

logger = logging.getLogger(__name__)

# ScreenCaptureKit outputs 48kHz stereo float32
SCK_SAMPLE_RATE = 48000
SCK_CHANNELS = 2
SCK_BYTES_PER_SAMPLE = 4  # float32

# Locate the compiled audio_tap binary next to this file
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
AUDIO_TAP_PATH = os.path.join(_THIS_DIR, "audio_tap")


def _resample_to_16k(audio: np.ndarray, orig_sr: int) -> np.ndarray:
    """Resample audio from orig_sr to 16kHz mono float32."""
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if orig_sr != TARGET_SAMPLE_RATE:
        num_samples = int(len(audio) * TARGET_SAMPLE_RATE / orig_sr)
        audio = resample(audio, num_samples)
    return audio.astype(np.float32)


class AudioRecorder:
    """Records system audio (via ScreenCaptureKit) + microphone, mixed into one stream."""

    def __init__(self, audio_queue: queue.Queue, stop_event: threading.Event):
        self.audio_queue = audio_queue
        self.stop_event = stop_event
        self._buffer: list[np.ndarray] = []
        self._buffer_lock = threading.Lock()
        self._samples_per_chunk = TARGET_SAMPLE_RATE * CHUNK_DURATION_SECONDS
        self._samples_collected = 0
        self._chunk_index = 0
        self._sck_process: subprocess.Popen | None = None
        self._mic_stream: sd.InputStream | None = None
        self._mic_buffer: list[np.ndarray] = []
        self._mic_lock = threading.Lock()
        self._all_chunks_16k: list[np.ndarray] = []

        # Recovery: incremental WAV file written to disk as chunks arrive
        self._recovery_wav: wave.Wave_write | None = None
        self._recovery_path: str | None = None

        # Audio level monitoring
        self._current_rms: float = 0.0
        self._current_peak: float = 0.0
        self._silence_start: float | None = None
        self._silence_warning_fired = False
        self._on_silence_warning: callable | None = None
        self._on_audio_tap_error: callable | None = None

    def _start_system_audio(self):
        """Launch the ScreenCaptureKit audio_tap subprocess."""
        if not os.path.isfile(AUDIO_TAP_PATH):
            raise RuntimeError(
                f"audio_tap binary not found at {AUDIO_TAP_PATH}. "
                "Run: swiftc -O -o src/meeting_recorder/audio_tap "
                "src/meeting_recorder/audio_tap.swift "
                "-framework ScreenCaptureKit -framework CoreMedia -framework AVFoundation"
            )

        self._sck_process = subprocess.Popen(
            [AUDIO_TAP_PATH, str(SCK_SAMPLE_RATE), str(SCK_CHANNELS)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        logger.info("Started ScreenCaptureKit audio capture (PID %d)", self._sck_process.pid)

        # Log stderr in background
        def _log_stderr():
            for line in self._sck_process.stderr:
                logger.info("[audio_tap] %s", line.decode().strip())

        threading.Thread(target=_log_stderr, name="AudioTapStderr", daemon=True).start()

    def _open_recovery_wav(self):
        """Open a recovery WAV file for incremental writing."""
        RECOVERY_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self._recovery_path = str(RECOVERY_DIR / f"recovery_{timestamp}.wav")
        self._recovery_wav = wave.open(self._recovery_path, "wb")
        self._recovery_wav.setnchannels(1)
        self._recovery_wav.setsampwidth(2)  # 16-bit PCM
        self._recovery_wav.setframerate(TARGET_SAMPLE_RATE)
        logger.info("Recovery WAV opened: %s", self._recovery_path)

    def _write_recovery_chunk(self, audio_16k: np.ndarray):
        """Append a chunk of 16kHz float32 audio to the recovery WAV."""
        if self._recovery_wav is None:
            return
        # Convert float32 [-1, 1] to int16
        pcm = np.clip(audio_16k, -1.0, 1.0)
        pcm = (pcm * 32767).astype(np.int16)
        self._recovery_wav.writeframes(pcm.tobytes())

    def _close_recovery_wav(self):
        """Close the recovery WAV file."""
        if self._recovery_wav is not None:
            try:
                self._recovery_wav.close()
            except Exception:
                logger.exception("Error closing recovery WAV")
            self._recovery_wav = None

    def get_recovery_path(self) -> str | None:
        """Return the path to the recovery WAV file, or None if not available."""
        return self._recovery_path

    def delete_recovery_file(self):
        """Delete the recovery WAV after a successful recording completes."""
        if self._recovery_path and os.path.isfile(self._recovery_path):
            try:
                os.remove(self._recovery_path)
                logger.info("Recovery file deleted: %s", self._recovery_path)
            except OSError:
                logger.warning("Could not delete recovery file: %s", self._recovery_path)

    def _update_audio_levels(self, audio_16k: np.ndarray):
        """Track RMS and peak levels; detect prolonged silence."""
        self._current_rms = float(np.sqrt(np.mean(audio_16k ** 2)))
        self._current_peak = float(np.abs(audio_16k).max())

        # Threshold for "silence" — RMS below -60 dBFS (~0.001)
        is_silent = self._current_rms < 0.001

        now = time.time()
        if is_silent:
            if self._silence_start is None:
                self._silence_start = now
            elif (
                not self._silence_warning_fired
                and (now - self._silence_start) >= SILENCE_WARNING_SECONDS
            ):
                self._silence_warning_fired = True
                logger.warning(
                    "Audio has been silent for %d+ seconds — check your audio source.",
                    SILENCE_WARNING_SECONDS,
                )
                if self._on_silence_warning:
                    self._on_silence_warning()
        else:
            self._silence_start = None
            self._silence_warning_fired = False

    def get_audio_levels(self) -> tuple[float, float]:
        """Return the current (rms, peak) levels as floats in [0, 1]."""
        return self._current_rms, self._current_peak

    def _stop_system_audio(self):
        """Terminate the audio_tap subprocess."""
        if self._sck_process:
            try:
                self._sck_process.send_signal(signal.SIGTERM)
                self._sck_process.wait(timeout=3)
            except Exception:
                self._sck_process.kill()
            self._sck_process = None

    def _read_system_audio(self):
        """Read PCM float32 from the audio_tap subprocess and buffer it."""
        # Read in 4096-sample blocks (each sample = 4 bytes * 2 channels)
        block_samples = 4096
        block_bytes = block_samples * SCK_CHANNELS * SCK_BYTES_PER_SAMPLE

        while not self.stop_event.is_set() and self._sck_process:
            try:
                data = self._sck_process.stdout.read(block_bytes)
                if not data:
                    break

                # Parse as interleaved float32 stereo
                audio = np.frombuffer(data, dtype=np.float32)
                if SCK_CHANNELS == 2:
                    audio = audio.reshape(-1, 2)

                chunk_16k = _resample_to_16k(audio, SCK_SAMPLE_RATE)

                with self._buffer_lock:
                    self._buffer.append(chunk_16k)
                    self._samples_collected += len(chunk_16k)

                    if self._samples_collected >= self._samples_per_chunk:
                        self._flush_chunk()
            except Exception:
                if not self.stop_event.is_set():
                    logger.exception("Error reading system audio")
                break

        # Detect audio_tap crash: if we didn't request the stop, the process died
        if not self.stop_event.is_set() and self._sck_process:
            retcode = self._sck_process.poll()
            if retcode is not None and retcode != 0:
                msg = f"audio_tap crashed with exit code {retcode}"
                logger.error(msg)
                # Try to capture stderr for diagnostics
                try:
                    stderr_out = self._sck_process.stderr.read()
                    if stderr_out:
                        msg += f": {stderr_out.decode(errors='replace').strip()}"
                except Exception:
                    pass
                if self._on_audio_tap_error:
                    self._on_audio_tap_error(msg)
            elif retcode is None:
                logger.error("audio_tap stdout closed unexpectedly while process still running")
                if self._on_audio_tap_error:
                    self._on_audio_tap_error("System audio capture stopped unexpectedly")

    def _find_mic(self) -> dict | None:
        """Find the default microphone device."""
        try:
            default_idx = sd.default.device[0]
            if default_idx is not None and default_idx >= 0:
                dev = sd.query_devices(default_idx)
                if dev["max_input_channels"] > 0:
                    logger.info("Found mic: %s (index %d)", dev["name"], default_idx)
                    return {**dev, "index": default_idx}
        except Exception:
            pass
        return None

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
        """Mix system audio and mic buffers, then push to queue."""
        system_audio = np.concatenate(self._buffer, axis=0)
        self._buffer.clear()
        self._samples_collected = 0

        with self._mic_lock:
            if self._mic_buffer:
                mic_audio = np.concatenate(self._mic_buffer, axis=0)
                self._mic_buffer.clear()
            else:
                mic_audio = None

        if mic_audio is not None:
            target_len = len(system_audio)
            if len(mic_audio) > target_len:
                mic_audio = mic_audio[:target_len]
            elif len(mic_audio) < target_len:
                mic_audio = np.pad(mic_audio, (0, target_len - len(mic_audio)))
            mixed = system_audio + mic_audio
            peak = np.abs(mixed).max()
            if peak > 1.0:
                mixed /= peak
        else:
            mixed = system_audio

        # Update audio level monitoring
        self._update_audio_levels(mixed)

        # Write to recovery WAV (survives crashes)
        self._write_recovery_chunk(mixed)

        self._all_chunks_16k.append(mixed)
        timestamp_seconds = self._chunk_index * CHUNK_DURATION_SECONDS
        self.audio_queue.put((timestamp_seconds, mixed))
        self._chunk_index += 1
        logger.debug("Queued audio chunk %d", self._chunk_index)

    def run(self):
        """Start recording. Blocks until stop_event is set."""
        # Open recovery WAV for crash-safe incremental saving
        self._open_recovery_wav()

        # Start system audio capture via ScreenCaptureKit
        self._start_system_audio()

        # Start mic capture
        mic = self._find_mic()
        if mic:
            self._mic_sr = int(mic.get("default_samplerate", 48000))
            mic_channels = min(int(mic.get("max_input_channels", 1)), 1)
            logger.info("Also recording mic: %s at %d Hz", mic["name"], self._mic_sr)
            self._mic_stream = sd.InputStream(
                device=mic["index"],
                samplerate=self._mic_sr,
                channels=mic_channels,
                dtype="float32",
                callback=self._mic_callback,
                blocksize=1024,
            )
            self._mic_stream.start()
        else:
            logger.warning("No microphone found — recording system audio only.")

        # Read system audio (blocks until stop)
        self._read_system_audio()

        # Clean up
        if self._mic_stream:
            self._mic_stream.stop()
            self._mic_stream.close()
        self._stop_system_audio()

        # Flush remaining audio
        with self._buffer_lock:
            if self._buffer:
                self._flush_chunk()

        # Close recovery WAV (it remains on disk until explicitly deleted)
        self._close_recovery_wav()

        logger.info("Recording stopped.")

    def get_full_audio_16k(self) -> np.ndarray | None:
        """Return all recorded audio concatenated as 16kHz mono float32."""
        if not self._all_chunks_16k:
            return None
        return np.concatenate(self._all_chunks_16k, axis=0)

    def start_thread(self) -> threading.Thread:
        """Launch the recorder in a background thread."""
        t = threading.Thread(target=self.run, name="AudioRecorder", daemon=True)
        t.start()
        return t
