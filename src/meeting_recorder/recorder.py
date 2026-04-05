"""Audio recording via ScreenCaptureKit (system audio) + microphone."""

import logging
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
import wave

import numpy as np
import sounddevice as sd
from scipy.signal import resample

from meeting_recorder.config import (
    CHUNK_DURATION_SECONDS,
    MIN_DISK_SPACE_MB,
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

# Mic reconnect settings
_MIC_RECONNECT_DELAY = 1.0
_MIC_MAX_CONSECUTIVE_ERRORS = 10


def _resample_to_16k(audio: np.ndarray, orig_sr: int) -> np.ndarray:
    """Resample audio from orig_sr to 16kHz mono float32."""
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if orig_sr != TARGET_SAMPLE_RATE:
        num_samples = int(len(audio) * TARGET_SAMPLE_RATE / orig_sr)
        audio = resample(audio, num_samples)
    return audio.astype(np.float32)


def _get_free_disk_mb(path: str) -> float:
    """Return free disk space in MB for the filesystem containing path."""
    usage = shutil.disk_usage(path)
    return usage.free / (1024 * 1024)


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
        self._mic_sr: int = 48000
        self._mic_error_count: int = 0
        # Store resampled chunks for post-recording diarization
        self._all_chunks_16k: list[np.ndarray] = []

        # Recovery: incremental WAV file written to disk as chunks arrive
        self._recovery_wav: wave.Wave_write | None = None
        self._recovery_path: str | None = None

        # Audio level monitoring
        self._current_rms: float = 0.0
        self._current_peak: float = 0.0
        self._silence_start: float | None = None
        self._silence_warning_fired = False

        # Callbacks for app-level error handling
        self._on_silence_warning: callable | None = None
        self._on_audio_tap_error: callable | None = None
        self._on_low_disk_space: callable | None = None
        self._on_mic_reconnect: callable | None = None

    # ── System audio (ScreenCaptureKit) ─────────────────────────

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

    def _stop_system_audio(self):
        """Terminate the audio_tap subprocess."""
        if self._sck_process:
            try:
                self._sck_process.send_signal(signal.SIGTERM)
                self._sck_process.wait(timeout=3)
            except Exception:
                self._sck_process.kill()
            self._sck_process = None

    def _restart_system_audio(self) -> bool:
        """Attempt to restart the audio_tap subprocess after a crash.

        Returns True if restart succeeded.
        """
        logger.info("Attempting to restart audio_tap...")
        self._stop_system_audio()
        time.sleep(_MIC_RECONNECT_DELAY)
        try:
            self._start_system_audio()
            logger.info("audio_tap restarted successfully.")
            return True
        except Exception:
            logger.exception("Failed to restart audio_tap")
            return False

    def _read_system_audio(self):
        """Read PCM float32 from the audio_tap subprocess and buffer it."""
        block_samples = 4096
        block_bytes = block_samples * SCK_CHANNELS * SCK_BYTES_PER_SAMPLE

        while not self.stop_event.is_set() and self._sck_process:
            try:
                data = self._sck_process.stdout.read(block_bytes)
                if not data:
                    # Subprocess may have died — try to restart once
                    if not self.stop_event.is_set() and self._try_recover_audio_tap():
                        continue
                    break

                # Parse as interleaved float32 stereo
                audio = np.frombuffer(data, dtype=np.float32)
                if SCK_CHANNELS == 2:
                    audio = audio.reshape(-1, 2)

                chunk_16k = _resample_to_16k(audio, SCK_SAMPLE_RATE)

                # Update levels on every block for responsive UI metering
                self._current_rms = float(np.sqrt(np.mean(chunk_16k ** 2)))
                self._current_peak = float(np.abs(chunk_16k).max())

                with self._buffer_lock:
                    self._buffer.append(chunk_16k)
                    self._samples_collected += len(chunk_16k)

                    if self._samples_collected >= self._samples_per_chunk:
                        self._flush_chunk()
            except Exception:
                if not self.stop_event.is_set():
                    logger.exception("Error reading system audio")
                break

        # Final crash detection (if we exited the loop without recovery)
        if not self.stop_event.is_set() and self._sck_process:
            retcode = self._sck_process.poll()
            if retcode is not None and retcode != 0:
                msg = f"audio_tap crashed with exit code {retcode}"
                logger.error(msg)
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

    def _try_recover_audio_tap(self) -> bool:
        """Try to recover from audio_tap crash with one restart attempt."""
        retcode = self._sck_process.poll() if self._sck_process else None
        if retcode is not None and retcode != 0:
            logger.warning("audio_tap exited with code %d, attempting restart...", retcode)
            if self._restart_system_audio():
                return True
            # Restart failed — notify the app
            if self._on_audio_tap_error:
                self._on_audio_tap_error(
                    f"audio_tap crashed (exit code {retcode}) and restart failed"
                )
        return False

    # ── Microphone ─────────────────────────────────────────────────

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

    def _start_mic(self, mic: dict) -> bool:
        """Start the microphone input stream. Returns True on success."""
        try:
            self._mic_sr = int(mic.get("default_samplerate", 48000))
            mic_channels = min(int(mic.get("max_input_channels", 1)), 1)
            logger.info("Starting mic: %s at %d Hz", mic["name"], self._mic_sr)
            self._mic_stream = sd.InputStream(
                device=mic["index"],
                samplerate=self._mic_sr,
                channels=mic_channels,
                dtype="float32",
                callback=self._mic_callback,
                blocksize=1024,
            )
            self._mic_stream.start()
            self._mic_error_count = 0
            return True
        except Exception:
            logger.exception("Failed to start mic stream")
            return False

    def _stop_mic(self):
        """Stop and close the microphone stream."""
        if self._mic_stream:
            try:
                self._mic_stream.stop()
                self._mic_stream.close()
            except Exception:
                logger.exception("Error stopping mic stream")
            self._mic_stream = None

    def _reconnect_mic(self):
        """Attempt to reconnect the microphone after errors."""
        logger.info("Attempting mic reconnect...")
        self._stop_mic()
        time.sleep(_MIC_RECONNECT_DELAY)
        mic = self._find_mic()
        if mic and self._start_mic(mic):
            logger.info("Mic reconnected successfully.")
            if self._on_mic_reconnect:
                self._on_mic_reconnect(mic["name"])
        else:
            logger.warning("Mic reconnect failed — continuing with system audio only.")

    def _mic_callback(self, indata: np.ndarray, frames: int, time_info, status):
        """Called by sounddevice for each mic audio block."""
        if status:
            logger.warning("Mic callback status: %s", status)
            self._mic_error_count += 1
            if self._mic_error_count >= _MIC_MAX_CONSECUTIVE_ERRORS:
                logger.error("Too many mic errors, scheduling reconnect.")
                # Schedule reconnect from a separate thread (can't block callback)
                threading.Thread(
                    target=self._reconnect_mic, name="MicReconnect", daemon=True
                ).start()
                self._mic_error_count = 0
                raise sd.CallbackAbort
        else:
            self._mic_error_count = 0

        if self.stop_event.is_set():
            raise sd.CallbackAbort
        chunk = _resample_to_16k(indata.copy(), self._mic_sr)
        with self._mic_lock:
            self._mic_buffer.append(chunk)

    # ── Recovery WAV ───────────────────────────────────────────────

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

    # ── Audio level & silence monitoring ───────────────────────────

    def _check_silence(self, audio_16k: np.ndarray):
        """Detect prolonged silence over a full chunk."""
        chunk_rms = float(np.sqrt(np.mean(audio_16k ** 2)))

        # Threshold for "silence" — RMS below -60 dBFS (~0.001)
        is_silent = chunk_rms < 0.001

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

    def get_silence_duration(self) -> float:
        """Return how long audio has been silent in seconds, or 0 if not silent."""
        if self._silence_start is None:
            return 0.0
        return time.time() - self._silence_start

    # ── Disk space monitoring ──────────────────────────────────────

    def check_disk_space(self) -> bool:
        """Check if disk space is sufficient. Returns False if too low."""
        if self._recovery_path is None:
            return True
        try:
            free_mb = _get_free_disk_mb(self._recovery_path)
            if free_mb < MIN_DISK_SPACE_MB:
                logger.error(
                    "Low disk space: %.0f MB remaining (minimum: %d MB)",
                    free_mb, MIN_DISK_SPACE_MB,
                )
                if self._on_low_disk_space:
                    self._on_low_disk_space(free_mb)
                return False
            return True
        except Exception:
            logger.exception("Error checking disk space")
            return True  # don't stop recording on check failure

    # ── Chunk flush ────────────────────────────────────────────────

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

        # Check for prolonged silence over the full mixed chunk
        self._check_silence(mixed)

        # Write to recovery WAV (survives crashes)
        self._write_recovery_chunk(mixed)

        self._all_chunks_16k.append(mixed)
        timestamp_seconds = self._chunk_index * CHUNK_DURATION_SECONDS
        self.audio_queue.put((timestamp_seconds, mixed))
        self._chunk_index += 1
        logger.debug("Queued audio chunk %d", self._chunk_index)

    # ── Main recording loop ────────────────────────────────────────

    def run(self):
        """Start recording. Blocks until stop_event is set."""
        # Open recovery WAV for crash-safe incremental saving
        self._open_recovery_wav()

        # Start system audio capture via ScreenCaptureKit
        self._start_system_audio()

        # Start mic capture
        mic = self._find_mic()
        if mic:
            self._start_mic(mic)
        else:
            logger.warning("No microphone found — recording system audio only.")

        # Read system audio (blocks until stop)
        self._read_system_audio()

        # Clean up
        self._stop_mic()
        self._stop_system_audio()

        # Flush remaining audio
        with self._buffer_lock:
            if self._buffer:
                self._flush_chunk()

        # Close recovery WAV (it remains on disk until explicitly deleted)
        self._close_recovery_wav()

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
