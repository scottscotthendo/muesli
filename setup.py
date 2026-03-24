"""py2app setup for Meeting Recorder."""

from setuptools import setup

APP = ["src/meeting_recorder/app.py"]
DATA_FILES = []
OPTIONS = {
    "argv_emulation": False,
    "emulate_shell_environment": True,
    "plist": {
        "LSUIElement": True,  # No dock icon
        "CFBundleName": "Hendos Meeting Recorder",
        "CFBundleDisplayName": "Hendos Meeting Recorder",
        "CFBundleIdentifier": "com.personal.meeting-recorder",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "NSMicrophoneUsageDescription": (
            "Meeting Recorder needs microphone access to capture audio "
            "from the BlackHole virtual audio device."
        ),
    },
    "packages": [
        "rumps",
        "sounddevice",
        "numpy",
        "scipy",
        "faster_whisper",
        "ctranslate2",
        "google.auth",
        "google.oauth2",
        "google_auth_oauthlib",
        "googleapiclient",
        "llama_cpp",
        "huggingface_hub",
        "meeting_recorder",
    ],
    "includes": [
        "meeting_recorder.app",
        "meeting_recorder.recorder",
        "meeting_recorder.transcriber",
        "meeting_recorder.summarizer",
        "meeting_recorder.calendar_client",
        "meeting_recorder.transcript_writer",
        "meeting_recorder.config",
    ],
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
