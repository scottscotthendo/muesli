#!/usr/bin/env bash
# Full setup for Muesli on a fresh Mac.
#
# What it does:
#   1. Installs Homebrew (if missing)
#   2. Installs Python 3.12 and BlackHole 2ch via brew
#   3. Creates a virtualenv and installs all dependencies
#   4. Pre-downloads the whisper and summarization models
#   5. Builds the .app bundle and copies it to /Applications
#   6. Prints manual steps (Audio MIDI setup, Google Calendar)
#
# Usage:
#   ./scripts/setup.sh

set -euo pipefail
cd "$(dirname "$0")/.."

BOLD="\033[1m"
GREEN="\033[32m"
YELLOW="\033[33m"
RESET="\033[0m"

step() { echo -e "\n${GREEN}${BOLD}==> $1${RESET}"; }
warn() { echo -e "${YELLOW}    $1${RESET}"; }

# -------------------------------------------------------------------
# 1. Homebrew
# -------------------------------------------------------------------
step "Checking Homebrew..."
if command -v brew &>/dev/null; then
    echo "    Homebrew found."
else
    echo "    Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
fi

# -------------------------------------------------------------------
# 2. Python 3.12
# -------------------------------------------------------------------
step "Checking Python 3.12..."
if brew list python@3.12 &>/dev/null; then
    echo "    python@3.12 already installed."
else
    echo "    Installing python@3.12..."
    brew install python@3.12
fi

PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
if [ ! -x "$PYTHON" ]; then
    echo "Error: python3.12 not found at $PYTHON"
    exit 1
fi
echo "    Using: $PYTHON"

# -------------------------------------------------------------------
# 3. Build ScreenCaptureKit audio helper
# -------------------------------------------------------------------
step "Building audio capture helper..."
AUDIO_TAP_SRC="src/meeting_recorder/audio_tap.swift"
AUDIO_TAP_BIN="src/meeting_recorder/audio_tap"
if [ -f "$AUDIO_TAP_BIN" ] && [ "$AUDIO_TAP_BIN" -nt "$AUDIO_TAP_SRC" ]; then
    echo "    audio_tap binary is up to date."
else
    echo "    Compiling audio_tap..."
    swiftc -O -o "$AUDIO_TAP_BIN" "$AUDIO_TAP_SRC" \
        -framework ScreenCaptureKit -framework CoreMedia -framework AVFoundation
    echo "    audio_tap compiled successfully."
fi

# -------------------------------------------------------------------
# 4. Virtual environment + dependencies
# -------------------------------------------------------------------
step "Setting up Python environment..."
if [ -d .venv ]; then
    echo "    .venv already exists."
else
    echo "    Creating virtualenv..."
    "$PYTHON" -m venv .venv
fi

source .venv/bin/activate
echo "    Installing dependencies..."
pip install --upgrade pip --quiet
pip install -e . --quiet
pip install py2app --quiet

# -------------------------------------------------------------------
# 5. Pre-download models
# -------------------------------------------------------------------
step "Pre-downloading models (this may take a few minutes on first run)..."

echo "    Downloading whisper model (small.en, ~460MB)..."
python -c "
from faster_whisper import WhisperModel
WhisperModel('small.en', device='cpu', compute_type='int8')
print('    Whisper model ready.')
"

echo "    Downloading summarization model (Qwen2.5-1.5B, ~1GB)..."
python -c "
from meeting_recorder.summarizer import _ensure_model
_ensure_model()
print('    Summarization model ready.')
"

# -------------------------------------------------------------------
# 6. Build .app and install
# -------------------------------------------------------------------
step "Building Muesli.app..."
./scripts/build_app.sh

step "Installing to /Applications..."
if [ -d "/Applications/Muesli.app" ]; then
    echo "    Removing previous version..."
    rm -rf "/Applications/Muesli.app"
fi
cp -R dist/app.app "/Applications/Muesli.app"
echo "    Installed: /Applications/Muesli.app"

# -------------------------------------------------------------------
# 7. Add to Login Items (auto-launch on boot)
# -------------------------------------------------------------------
step "Adding to Login Items (auto-launch on boot)..."
osascript -e 'tell application "System Events" to make login item at end with properties {path:"/Applications/Muesli.app", hidden:true}' 2>/dev/null && \
    echo "    Muesli will launch automatically on login." || \
    warn "Could not add to Login Items — you can do this manually in System Settings → General → Login Items."

# -------------------------------------------------------------------
# 8. Manual steps
# -------------------------------------------------------------------
step "Speaker diarization (optional)..."
echo ""
echo "    Diarization identifies who said what in the transcript."
echo "    It requires ~2GB of additional downloads (torch + pyannote.audio)."
echo ""
read -p "    Install diarization support? [y/N] " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "    Installing torch and pyannote.audio..."
    pip install "pyannote.audio>=3.1" "torch>=2.0" --quiet
    echo ""
    echo "    To complete diarization setup, you need to:"
    echo "    1. Create a Hugging Face token at https://huggingface.co/settings/tokens"
    echo "    2. Accept model terms at:"
    echo "       - https://huggingface.co/pyannote/speaker-diarization-3.1"
    echo "       - https://huggingface.co/pyannote/segmentation-3.0"
    echo "    3. Log in:"
    echo '       source .venv/bin/activate && python -c "from huggingface_hub import login; login()"'
    echo ""
fi

# -------------------------------------------------------------------
# 9. Manual steps
# -------------------------------------------------------------------
step "Almost done! Optional setup:"

echo ""
echo -e "  ${BOLD}1. Screen Recording permission (required on first run):${RESET}"
echo "     - macOS will prompt you to grant Screen Recording access"
echo "     - This is needed for ScreenCaptureKit to capture system audio"
echo "     - Go to System Settings → Privacy & Security → Screen Recording if needed"
echo ""
echo -e "  ${BOLD}2. Google Calendar integration (optional):${RESET}"
echo "     - Create OAuth credentials at https://console.cloud.google.com"
echo "     - Enable the Google Calendar API"
echo "     - Download credentials.json to ~/.config/muesli/"
echo "     - The app will prompt you to authorize on first calendar check"
echo ""
echo -e "  ${BOLD}3. Notion sync (optional):${RESET}"
echo "     - Create a Notion integration at https://www.notion.so/profile/integrations"
echo "     - Save token: echo 'ntn_YOUR_TOKEN' > ~/.config/muesli/notion_token"
echo "     - Share your Notion database with the integration"
echo ""

step "Done! Launch with:"
echo "    open '/Applications/Muesli.app'"
echo ""
