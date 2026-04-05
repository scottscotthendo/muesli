"""Paths, constants, and settings for Muesli."""

import os
from pathlib import Path

# Directories
CONFIG_DIR = Path(os.path.expanduser("~/.config/muesli"))
MEETINGS_DIR = Path(os.path.expanduser("~/meetings"))

# Google OAuth
CREDENTIALS_PATH = CONFIG_DIR / "credentials.json"
TOKEN_PATH = CONFIG_DIR / "token.json"
CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

# Audio
TARGET_SAMPLE_RATE = 16000  # faster-whisper expects 16kHz
CHUNK_DURATION_SECONDS = 30

# Transcription
WHISPER_MODEL = "small.en"
WHISPER_DEVICE = "cpu"
WHISPER_COMPUTE_TYPE = "int8"

# Calendar
CALENDAR_LOOKAHEAD_MINUTES = 10
CALENDAR_CHECK_INTERVAL_SECONDS = 60

# Diarization
DIARIZATION_ENABLED = True  # Set to False to skip speaker identification

# Recovery
RECOVERY_DIR = CONFIG_DIR / "recovery"
SILENCE_WARNING_SECONDS = 30  # warn if audio is near-silent for this long

# Notion
NOTION_TOKEN_PATH = CONFIG_DIR / "notion_token"
NOTION_DATABASE_ID = "e295f2aa27af4d54a371e6db4e9f8613"

# Recording safety guards
MAX_RECORDING_HOURS = 12  # hard time cap
NUDGE_SCHEDULE_MINUTES = [5, 15, 30]  # escalating nudge intervals, then every 30m
SILENCE_AUTO_STOP_SECONDS = 300  # auto-stop after 5 min of silence (with grace period)
SILENCE_GRACE_SECONDS = 60  # grace period before silence auto-stop takes effect
MIN_DISK_SPACE_MB = 500  # auto-stop if disk space drops below this

# UI
APP_NAME = "Muesli"
ICON_IDLE = "🎙"
ICON_RECORDING = "🔴"
ICON_ERROR = "⚠️"
