"""Tests for the calendar client module."""

from datetime import datetime

from meeting_recorder.calendar_client import CalendarEvent


def test_calendar_event_creation():
    event = CalendarEvent(
        title="Sprint Planning",
        attendees=["alice@test.com", "bob@test.com"],
        start_time=datetime(2025, 1, 15, 10, 0),
    )
    assert event.title == "Sprint Planning"
    assert len(event.attendees) == 2
    assert "Sprint Planning" in repr(event)
