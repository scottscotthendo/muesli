# Muesli

A free, open-source, fully local meeting transcription app for macOS.

No cloud. No subscription. No data leaves your machine. Muesli sits in your menubar, records both sides of your calls (system audio + mic), transcribes with [faster-whisper](https://github.com/SYSTRAN/faster-whisper), identifies speakers with [pyannote.audio](https://github.com/pyannote/pyannote-audio), and generates AI summaries — all on-device. Integrates with Google Calendar to auto-detect meetings.

## Features

- **Menubar-only app** — no dock icon, stays out of the way
- **Full conversation capture** — records both system audio (via BlackHole) and your microphone, so both sides of the conversation are transcribed
- **Local transcription** — all audio is transcribed on-device using faster-whisper (small.en model)
- **Speaker diarization** — optionally identifies who said what using pyannote.audio (requires Hugging Face token)
- **Rolling 30-second chunks** — transcript updates in near real-time
- **Google Calendar integration** — detects meetings starting within 10 minutes and prompts you to record
- **AI-powered summaries** — after recording stops, a local LLM (Qwen2.5-1.5B) generates key decisions, action items, and topics
- **Markdown output** — clean, timestamped transcripts saved to `~/meetings/`

## Quick Start

The fastest way to get running:

```bash
./scripts/setup.sh
```

This installs all dependencies, downloads models, builds the app, and copies it to `/Applications`. See below for manual setup if you prefer.

## Prerequisites

### 1. BlackHole Virtual Audio Driver

Install [BlackHole](https://existential.audio/blackhole/) (2ch version recommended):

```bash
brew install blackhole-2ch
```

> **Important:** BlackHole requires a **reboot** after installation to load the audio driver.

After rebooting, set up audio routing so you can both hear and record system audio:

1. Open **Audio MIDI Setup** (search in Spotlight)
2. Click **+** → **Create Multi-Output Device**
3. Check both your speakers/headphones AND **BlackHole 2ch**
4. Set this Multi-Output Device as your system audio output in **System Settings → Sound**

### 2. Python 3.12

```bash
brew install python@3.12
```

> **Note:** Python 3.13 is not supported (faster-whisper lacks wheels).

### 3. Speaker Diarization (Optional)

To enable speaker identification ("who said what"):

1. Create a [Hugging Face](https://huggingface.co/settings/tokens) account and generate a **read** access token
2. Accept the model terms for both:
   - https://huggingface.co/pyannote/speaker-diarization-3.1
   - https://huggingface.co/pyannote/segmentation-3.0
3. Log in:
   ```bash
   source .venv/bin/activate
   python -c "from huggingface_hub import login; login()"
   ```
4. Install the diarization dependencies:
   ```bash
   pip install "pyannote.audio>=3.1" "torch>=2.0"
   ```

> **Note:** torch is ~2GB. Diarization runs post-recording and adds speaker labels (e.g. `**SPEAKER_00:**`) to the transcript. If not installed, the app works fine without it.

### 4. Google Calendar Credentials (Optional)

To enable calendar integration:

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use an existing one)
3. Enable the **Google Calendar API**
4. Go to **APIs & Services → Credentials**
5. Click **+ Create Credentials → OAuth client ID**
6. Application type: **Desktop app**
7. Download the JSON file and save it as:
   ```
   ~/.config/muesli/credentials.json
   ```

#### OAuth Consent Screen Setup

1. Go to **APIs & Services → OAuth consent screen**
2. Select **External** user type
3. Fill in the required fields (app name, support email)
4. Add scope: `https://www.googleapis.com/auth/calendar.readonly`
5. Add your Gmail address as a **test user**

> **Important:** Since this app is in "testing mode", you'll see an "unverified app" warning on first auth. Click **Advanced** → **Go to Muesli (unsafe)** to proceed. This is expected for personal apps.

#### Work Calendar on Personal Gmail

If your work Google Calendar is shared to your personal Gmail account with read-only access:

1. Auth with your personal Gmail account
2. The app will check **all visible calendars** (including shared ones)
3. Work meetings will be detected automatically

> **Note:** OAuth tokens in testing mode expire after 7 days. The app will automatically re-open the browser for re-authentication when this happens.

## Installation

### From Source

```bash
git clone https://github.com/scottscotthendo/muesli.git
cd muesli
brew install python@3.12
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Run:

```bash
source .venv/bin/activate
muesli
```

### Build .app Bundle

```bash
pip install py2app
./scripts/build_app.sh
open dist/app.app
```

### Create DMG

```bash
./scripts/create_dmg.sh
# Output: dist/Muesli-0.1.0.dmg
```

## Usage

1. Launch the app — a microphone icon appears in the menubar
2. On first run, the faster-whisper model (~460MB) and summarization model (~1GB) will download automatically
3. If Google credentials are configured, the app checks for upcoming calendar events every 60 seconds
4. When a meeting is detected: a prompt appears asking if you want to start recording
5. Click **Start Recording** (or accept the calendar prompt)
6. The menubar shows elapsed time while recording
7. Click **Stop Recording** when done
8. The app runs post-processing: speaker diarization (if installed), then AI summarization
9. A notification shows the path to the saved transcript

## Transcript Format

Transcripts are saved to `~/meetings/` as markdown files:

```
~/meetings/2025-01-15_14-00_weekly-standup.md
```

```markdown
# Weekly Standup

Date: 2025-01-15 14:00
Attendees: alice@company.com, bob@company.com

---

## Summary

- **Key decisions**: Agreed to ship auth module by Friday
- **Action items**: Alice to finish OAuth integration, Bob to write tests
- **Topics**: Sprint progress, auth module status, deployment timeline

---

[00:00] Good morning everyone, let's get started with the standup.

[00:30] Alice, would you like to go first?

[01:00] Sure, yesterday I worked on the authentication module...
```

## Troubleshooting

**"BlackHole audio device not found"**
- Ensure BlackHole is installed: `brew install blackhole-2ch`
- **Reboot your Mac** after installing — the audio driver won't load until you do
- The app searches for devices starting with "BlackHole"

**No audio being captured**
- Make sure your system audio output is set to the Multi-Output Device (not just BlackHole)
- Check that BlackHole is included in the Multi-Output Device

**Only other people's audio is transcribed, not mine**
- The app records from both BlackHole (system audio) and your default microphone
- Check **System Settings → Privacy & Security → Microphone** and ensure the app (or Terminal) has permission

**Diarization error: "403 Client Error"**
- You need to accept the model terms on Hugging Face for **both** models:
  - https://huggingface.co/pyannote/speaker-diarization-3.1
  - https://huggingface.co/pyannote/segmentation-3.0
- Make sure you're logged in: `python -c "from huggingface_hub import login; login()"`

**"Google OAuth credentials not found"**
- Place your `credentials.json` at `~/.config/muesli/credentials.json`

**OAuth token keeps expiring**
- This is expected in testing mode (7-day expiry). The app handles re-auth automatically.

**Model download is slow**
- The `small.en` model is ~460MB, the summarization model is ~1GB. First-run download depends on your internet speed. After that, models are cached locally.
