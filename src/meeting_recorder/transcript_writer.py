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

    def append_segment(self, timestamp_seconds: int, text: str, speaker: str | None = None):
        """Append a timestamped transcript segment, optionally with a speaker label."""
        if self._file is None:
            self.open()

        ts = _format_timestamp(timestamp_seconds)
        if speaker:
            self._file.write(f"{ts} **{speaker}:** {text}\n\n")
        else:
            self._file.write(f"{ts} {text}\n\n")
        self._file.flush()

    def get_transcript_text(self) -> str:
        """Read back the transcript body (everything after the --- separator)."""
        path = self.file_path
        if not path.exists():
            return ""
        content = path.read_text(encoding="utf-8")
        # Split on the --- separator and return everything after it
        parts = content.split("\n---\n", 1)
        return parts[1].strip() if len(parts) > 1 else ""

    def insert_summary(self, summary: str):
        """Insert a summary block between the header and the transcript body."""
        path = self.file_path
        if not path.exists():
            return

        content = path.read_text(encoding="utf-8")
        parts = content.split("\n---\n", 1)
        if len(parts) != 2:
            return

        header = parts[0]
        body = parts[1]

        new_content = f"{header}\n---\n\n## Summary\n\n{summary}\n\n---\n{body}"
        path.write_text(new_content, encoding="utf-8")
        logger.info("Summary inserted into %s", path)

    def update_with_speakers(self, labelled_segments: list[tuple[int, str, str]]):
        """Rewrite the transcript body with speaker labels.

        Args:
            labelled_segments: List of (timestamp_seconds, speaker, text).
        """
        path = self.file_path
        if not path.exists():
            return

        content = path.read_text(encoding="utf-8")
        parts = content.split("\n---\n", 1)
        if len(parts) != 2:
            return

        header = parts[0]
        # Check if there's a summary section
        body_part = parts[1]
        summary_split = body_part.split("\n---\n", 1)

        lines = []
        for ts_sec, speaker, text in labelled_segments:
            ts = _format_timestamp(ts_sec)
            lines.append(f"{ts} **{speaker}:** {text}\n")

        new_body = "\n" + "\n".join(lines)

        if len(summary_split) == 2:
            # Preserve summary: header --- summary --- transcript
            new_content = f"{header}\n---\n{summary_split[0]}\n---\n{new_body}"
        else:
            new_content = f"{header}\n---\n{new_body}"

        path.write_text(new_content, encoding="utf-8")
        logger.info("Transcript updated with speaker labels: %s", path)

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
