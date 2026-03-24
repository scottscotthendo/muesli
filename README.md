# Meeting Recorder

A macOS menubar app that records system audio and transcribes meetings locally using [faster-whisper](https://github.com/SYSTRAN/faster-whisper). Integrates with Google Calendar to auto-detect upcoming meetings and pre-populate transcript headers.

## Features

- **Menubar-only app** — no dock icon, stays out of the way
- **Local transcription** — all audio is transcribed on-device using faster-whisper (small.en model)
- **Rolling 30-second chunks** — transcript updates in near real-time
- **Google Calendar integration** — detects meetings starting within 10 minutes and prompts you to record
- **AI-powered summaries** — after recording stops, a local LLM (Qwen2.5-1.5B) generates key decisions, action items, and topics
- **Markdown output** — clean, timestamped transcripts saved to `~/meetings/`

## Prerequisites

### 1. BlackHole Virtual Audio Driver

Install [BlackHole](https://existential.audio/blackhole/) (2ch version recommended):

```bash
brew install blackhole-2ch
```

After installing, set up audio routing so you can both hear and record system audio:

1. Open **Audio MIDI Setup** (search in Spotlight)
2. Click **+** → **Create Multi-Output Device**
3. Check both your speakers/headphones AND **BlackHole 2ch**
4. Set this Multi-Output Device as your system audio output in **System Settings → Sound**

### 2. Python 3.12

```bash
brew install python@3.12
```

> **Note:** Python 3.13 is not supported (faster-whisper lacks wheels).

### 3. Google Calendar Credentials

To enable calendar integration:

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use an existing one)
3. Enable the **Google Calendar API**
4. Go to **APIs & Services → Credentials**
5. Click **+ Create Credentials → OAuth client ID**
6. Application type: **Desktop app**
7. Download the JSON file and save it as:
   ```
   ~/.config/meeting-recorder/credentials.json
   ```

#### OAuth Consent Screen Setup

1. Go to **APIs & Services → OAuth consent screen**
2. Select **External** user type
3. Fill in the required fields (app name, support email)
4. Add scope: `https://www.googleapis.com/auth/calendar.readonly`
5. Add your Gmail address as a **test user**

> **Important:** Since this app is in "testing mode", you'll see an "unverified app" warning on first auth. Click **Advanced** → **Go to Meeting Recorder (unsafe)** to proceed. This is expected for personal apps.

#### Work Calendar on Personal Gmail

If your work Google Calendar is shared to your personal Gmail account with read-only access:

1. Auth with your personal Gmail account
2. The app will check **all visible calendars** (including shared ones)
3. Work meetings will be detected automatically

> **Note:** OAuth tokens in testing mode expire after 7 days. The app will automatically re-open the browser for re-authentication when this happens.

## Installation

### From Source

```bash
git clone https://github.com/your-repo/meeting-recorder.git
cd meeting-recorder
pip install -e .
```

Run:

```bash
meeting-recorder
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
# Output: dist/MeetingRecorder-0.1.0.dmg
```

## Usage

1. Launch the app — a microphone icon appears in the menubar
2. On first run, the faster-whisper model (~460MB) and summarization model (~1GB) will download automatically
3. If Google credentials are configured, the app checks for upcoming calendar events every 60 seconds
4. When a meeting is detected: a prompt appears asking if you want to start recording
5. Click **Start Recording** (or accept the calendar prompt)
6. The menubar shows elapsed time while recording
7. Click **Stop Recording** when done
8. The menubar shows "Summarizing..." while the LLM generates a summary
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
- The app searches for devices starting with "BlackHole"

**No audio being captured**
- Make sure your system audio output is set to the Multi-Output Device (not just BlackHole)
- Check that BlackHole is included in the Multi-Output Device

**"Google OAuth credentials not found"**
- Place your `credentials.json` at `~/.config/meeting-recorder/credentials.json`

**OAuth token keeps expiring**
- This is expected in testing mode (7-day expiry). The app handles re-auth automatically.

**Model download is slow**
- The `small.en` model is ~460MB. First-run download depends on your internet speed. After that, the model is cached locally.
