"""Tests for the transcript writer module."""

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from meeting_recorder.transcript_writer import TranscriptWriter, _format_timestamp, _slugify


def test_slugify():
    assert _slugify("Weekly Standup") == "weekly-standup"
    assert _slugify("Q1 Planning / Review!") == "q1-planning--review"
    assert _slugify("  spaces  ") == "spaces"
    assert _slugify("") == ""


def test_format_timestamp():
    assert _format_timestamp(0) == "[00:00]"
    assert _format_timestamp(30) == "[00:30]"
    assert _format_timestamp(90) == "[01:30]"
    assert _format_timestamp(3600) == "[60:00]"


def test_writer_creates_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("meeting_recorder.transcript_writer.MEETINGS_DIR", Path(tmpdir)):
            writer = TranscriptWriter(
                title="Test Meeting",
                attendees=["alice@test.com", "bob@test.com"],
                start_time=datetime(2025, 1, 15, 14, 0),
            )
            writer.open()
            writer.append_segment(0, "Hello everyone.")
            writer.append_segment(30, "Let's begin.")
            writer.close()

            content = writer.file_path.read_text()
            assert "# Test Meeting" in content
            assert "Date: 2025-01-15 14:00" in content
            assert "Attendees: alice@test.com, bob@test.com" in content
            assert "[00:00] Hello everyone." in content
            assert "[00:30] Let's begin." in content


def test_writer_filename_format():
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("meeting_recorder.transcript_writer.MEETINGS_DIR", Path(tmpdir)):
            writer = TranscriptWriter(
                title="Weekly Standup",
                start_time=datetime(2025, 3, 20, 9, 30),
            )
            assert writer.file_path.name == "2025-03-20_09-30_weekly-standup.md"


def test_writer_context_manager():
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("meeting_recorder.transcript_writer.MEETINGS_DIR", Path(tmpdir)):
            with TranscriptWriter(title="Context Test") as writer:
                writer.append_segment(0, "Works with context manager.")

            content = writer.file_path.read_text()
            assert "# Context Test" in content
            assert "[00:00] Works with context manager." in content
