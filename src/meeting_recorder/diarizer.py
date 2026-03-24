"""Speaker diarization using pyannote.audio.

Runs post-recording on concatenated audio to identify who said what,
then maps speaker segments back to transcript timestamps.
"""

import logging
import threading

import numpy as np

logger = logging.getLogger(__name__)

# Diarization settings
MIN_SPEAKERS = 2
MAX_SPEAKERS = 10


class SpeakerSegment:
    """A segment of audio attributed to a specific speaker."""

    __slots__ = ("start", "end", "speaker")

    def __init__(self, start: float, end: float, speaker: str):
        self.start = start
        self.end = end
        self.speaker = speaker

    def __repr__(self):
        return f"SpeakerSegment({self.start:.1f}-{self.end:.1f}, {self.speaker})"


class Diarizer:
    """Identifies speakers in recorded audio using pyannote.audio."""

    def __init__(self):
        self._pipeline = None
        self._pipeline_ready = threading.Event()
        self._loading_error: Exception | None = None

    def load_pipeline(self):
        """Load the pyannote diarization pipeline. Safe to call from any thread."""
        try:
            from pyannote.audio import Pipeline

            logger.info("Loading speaker diarization pipeline...")
            self._pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
            )
            logger.info("Diarization pipeline loaded.")
        except ImportError:
            self._loading_error = ImportError(
                "pyannote.audio is not installed. "
                "Install it with: pip install pyannote.audio"
            )
            logger.warning("pyannote.audio not installed — diarization disabled.")
        except Exception as e:
            self._loading_error = e
            logger.error("Failed to load diarization pipeline: %s", e)
        finally:
            self._pipeline_ready.set()

    def load_pipeline_async(self) -> threading.Thread:
        """Load the pipeline in a background thread."""
        t = threading.Thread(target=self.load_pipeline, name="DiarizeLoader", daemon=True)
        t.start()
        return t

    @property
    def loading_error(self) -> Exception | None:
        return self._loading_error

    def is_ready(self) -> bool:
        return self._pipeline_ready.is_set() and self._loading_error is None

    def diarize(self, audio: np.ndarray, sample_rate: int = 16000) -> list[SpeakerSegment]:
        """Run diarization on a complete audio array.

        Args:
            audio: 1D float32 numpy array of audio samples.
            sample_rate: Sample rate of the audio (default 16kHz).

        Returns:
            List of SpeakerSegment with start/end times and speaker labels.
        """
        if not self._pipeline_ready.wait(timeout=120):
            logger.error("Diarization pipeline not ready after 120s")
            return []

        if self._loading_error:
            logger.error("Cannot diarize — pipeline failed to load: %s", self._loading_error)
            return []

        if len(audio) == 0:
            return []

        try:
            import torch

            logger.info("Running speaker diarization on %.1f seconds of audio...", len(audio) / sample_rate)

            # pyannote expects {"waveform": tensor, "sample_rate": int}
            waveform = torch.from_numpy(audio).unsqueeze(0).float()
            input_data = {"waveform": waveform, "sample_rate": sample_rate}

            diarization = self._pipeline(
                input_data,
                min_speakers=MIN_SPEAKERS,
                max_speakers=MAX_SPEAKERS,
            )

            segments = []
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                segments.append(SpeakerSegment(
                    start=turn.start,
                    end=turn.end,
                    speaker=speaker,
                ))

            logger.info(
                "Diarization complete: %d segments, %d speakers.",
                len(segments),
                len(set(s.speaker for s in segments)),
            )
            return segments

        except Exception:
            logger.exception("Diarization failed")
            return []


def assign_speakers_to_transcript(
    transcript_segments: list[tuple[int, str]],
    speaker_segments: list[SpeakerSegment],
    chunk_duration: int = 30,
) -> list[tuple[int, str, str]]:
    """Map speaker labels onto transcript segments.

    Each transcript segment has a timestamp (seconds) and text. We find
    which speaker was talking at that timestamp by checking overlap with
    diarization segments.

    Args:
        transcript_segments: List of (timestamp_seconds, text).
        speaker_segments: Diarization output.
        chunk_duration: Duration of each transcript chunk in seconds.

    Returns:
        List of (timestamp_seconds, speaker_label, text).
    """
    if not speaker_segments:
        return [(ts, "Unknown", text) for ts, text in transcript_segments]

    result = []
    for ts, text in transcript_segments:
        # Find the speaker with the most overlap in this chunk's time range
        chunk_start = float(ts)
        chunk_end = chunk_start + chunk_duration

        speaker_overlap: dict[str, float] = {}
        for seg in speaker_segments:
            overlap_start = max(chunk_start, seg.start)
            overlap_end = min(chunk_end, seg.end)
            overlap = max(0.0, overlap_end - overlap_start)
            if overlap > 0:
                speaker_overlap[seg.speaker] = speaker_overlap.get(seg.speaker, 0.0) + overlap

        if speaker_overlap:
            speaker = max(speaker_overlap, key=speaker_overlap.get)
        else:
            speaker = "Unknown"

        result.append((ts, speaker, text))

    return result
