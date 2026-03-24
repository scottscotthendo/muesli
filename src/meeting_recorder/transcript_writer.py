"""Markdown transcript file creation and appending."""

import logging
import re
from datetime import datetime
from pathlib import Path

from meeting_recorder.config import MEETINGS_DIR

logger = logging.getLogger(__name__)


def _slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")[:80]


def _format_timestamp(seconds: int) -> str:
    """Format seconds as [MM:SS]."""
    minutes = seconds // 60
    secs = seconds % 60
    return f"[{minutes:02d}:{secs:02d}]"


class TranscriptWriter:
    """Creates and appends to a markdown transcript file."""

    def __init__(
        self,
        title: str = "Untitled Meeting",
        attendees: list[str] | None = None,
        start_time: datetime | None = None,
    ):
        self.title = title
        self.attendees = attendees or []
        self.start_time = start_time or datetime.now()
        self._file_path: Path | None = None
        self._file = None

    @property
    def file_path(self) -> Path:
        if self._file_path is None:
            self._file_path = self._build_path()
        return self._file_path

    def _build_path(self) -> Path:
        """Generate the transcript file path."""
        MEETINGS_DIR.mkdir(parents=True, exist_ok=True)
        date_prefix = self.start_time.strftime("%Y-%m-%d_%H-%M")
        slug = _slugify(self.title) or "untitled"
        filename = f"{date_prefix}_{slug}.md"
        return MEETINGS_DIR / filename

    def open(self):
        """Create the file and write the header."""
        path = self.file_path
        logger.info("Creating transcript: %s", path)

        self._file = open(path, "w", encoding="utf-8")

        # Write header
        self._file.write(f"# {self.title}\n\n")
        self._file.write(f"Date: {self.start_time.strftime('%Y-%m-%d %H:%M')}\n")
        if self.attendees:
            self._file.write(f"Attendees: {', '.join(self.attendees)}\n")
        self._file.write("\n---\n\n")
        self._file.flush()

    def append_segment(self, timestamp_seconds: int, text: str):
        """Append a timestamped transcript segment."""
        if self._file is None:
            self.open()

        ts = _format_timestamp(timestamp_seconds)
        self._file.write(f"{ts} {text}\n\n")
        self._file.flush()

    def close(self):
        """Close the transcript file."""
        if self._file is not None:
            self._file.close()
            self._file = None
            logger.info("Transcript saved: %s", self.file_path)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()
