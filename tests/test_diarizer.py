"""Tests for the diarizer module."""

from meeting_recorder.diarizer import SpeakerSegment, assign_speakers_to_transcript


def test_assign_speakers_single_speaker():
    transcript = [(0, "Hello"), (30, "World")]
    speakers = [SpeakerSegment(0.0, 60.0, "SPEAKER_00")]
    result = assign_speakers_to_transcript(transcript, speakers, chunk_duration=30)
    assert len(result) == 2
    assert result[0] == (0, "SPEAKER_00", "Hello")
    assert result[1] == (30, "SPEAKER_00", "World")


def test_assign_speakers_two_speakers():
    transcript = [(0, "Hi from A"), (30, "Hi from B")]
    speakers = [
        SpeakerSegment(0.0, 25.0, "SPEAKER_00"),
        SpeakerSegment(25.0, 60.0, "SPEAKER_01"),
    ]
    result = assign_speakers_to_transcript(transcript, speakers, chunk_duration=30)
    assert result[0][1] == "SPEAKER_00"
    assert result[1][1] == "SPEAKER_01"


def test_assign_speakers_empty():
    transcript = [(0, "Hello")]
    result = assign_speakers_to_transcript(transcript, [], chunk_duration=30)
    assert result[0] == (0, "Unknown", "Hello")


def test_assign_speakers_overlap():
    """When multiple speakers overlap in a chunk, pick the one with most overlap."""
    transcript = [(0, "Discussion")]
    speakers = [
        SpeakerSegment(0.0, 10.0, "SPEAKER_00"),  # 10s overlap
        SpeakerSegment(10.0, 30.0, "SPEAKER_01"),  # 20s overlap
    ]
    result = assign_speakers_to_transcript(transcript, speakers, chunk_duration=30)
    assert result[0][1] == "SPEAKER_01"


def test_speaker_segment_repr():
    seg = SpeakerSegment(1.5, 3.5, "SPEAKER_00")
    assert "1.5" in repr(seg)
    assert "SPEAKER_00" in repr(seg)
