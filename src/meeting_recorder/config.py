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
SAMPLE_RATE = 44100  # BlackHole default
TARGET_SAMPLE_RATE = 16000  # faster-whisper expects 16kHz
CHANNELS = 2  # BlackHole 2ch
CHUNK_DURATION_SECONDS = 30
BLACKHOLE_DEVICE_PREFIX = "BlackHole"

# Transcription
WHISPER_MODEL = "small.en"
WHISPER_DEVICE = "cpu"
WHISPER_COMPUTE_TYPE = "int8"

# Calendar
CALENDAR_LOOKAHEAD_MINUTES = 10
CALENDAR_CHECK_INTERVAL_SECONDS = 60

# Diarization
DIARIZATION_ENABLED = True  # Set to False to skip speaker identification

# UI
APP_NAME = "Muesli"
ICON_IDLE = "🎙"
ICON_RECORDING = "🔴"
ICON_ERROR = "⚠️"
