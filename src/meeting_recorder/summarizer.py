"""Local meeting summarization using a small open-source LLM via llama-cpp-python."""

import logging
import threading
from pathlib import Path

from meeting_recorder.config import CONFIG_DIR

logger = logging.getLogger(__name__)

# Model settings
MODEL_REPO = "Qwen/Qwen2.5-3B-Instruct-GGUF"
MODEL_FILENAME = "qwen2.5-3b-instruct-q4_k_m.gguf"
MODEL_DIR = CONFIG_DIR / "models"

SUMMARY_PROMPT = """\
You are a meeting assistant. Summarize the following meeting transcript concisely.

Include:
- **Key decisions** made
- **Action items** with owners (if mentioned)
- **Main topics** discussed

Keep it brief — aim for 5-10 bullet points. Use markdown formatting.

Transcript:
{transcript}

Summary:"""


def _get_model_path() -> Path:
    return MODEL_DIR / MODEL_FILENAME


def _ensure_model() -> Path:
    """Download the GGUF model if not already cached. Returns the local path."""
    model_path = _get_model_path()
    if model_path.exists():
        return model_path

    from huggingface_hub import hf_hub_download

    logger.info("Downloading summarization model (%s) — this is a one-time ~2GB download...", MODEL_FILENAME)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    downloaded = hf_hub_download(
        repo_id=MODEL_REPO,
        filename=MODEL_FILENAME,
        local_dir=str(MODEL_DIR),
    )
    logger.info("Model downloaded: %s", downloaded)
    return Path(downloaded)


class Summarizer:
    """Generates meeting summaries using a local quantized LLM."""

    def __init__(self):
        self._model = None
        self._model_ready = threading.Event()
        self._loading_error: Exception | None = None

    def load_model(self):
        """Load the GGUF model. Safe to call from any thread."""
        try:
            from llama_cpp import Llama

            model_path = _ensure_model()
            logger.info("Loading summarization model...")
            self._model = Llama(
                model_path=str(model_path),
                n_ctx=4096,
                n_threads=4,
                n_gpu_layers=-1,  # Use Metal acceleration (all layers on GPU)
                verbose=False,
            )
            logger.info("Summarization model loaded.")
        except Exception as e:
            self._loading_error = e
            logger.error("Failed to load summarization model: %s", e)
        finally:
            self._model_ready.set()

    def load_model_async(self) -> threading.Thread:
        """Load the model in a background thread."""
        t = threading.Thread(target=self.load_model, name="SummarizerLoader", daemon=True)
        t.start()
        return t

    def is_ready(self) -> bool:
        return self._model_ready.is_set() and self._loading_error is None

    def summarize(self, transcript_text: str) -> str | None:
        """Generate a summary of the transcript. Returns markdown string or None on failure."""
        if not self._model_ready.wait(timeout=120):
            logger.error("Summarization model not ready after 120s")
            return None

        if self._loading_error:
            logger.error("Cannot summarize — model failed to load: %s", self._loading_error)
            return None

        # Truncate transcript if too long for context window (~3000 tokens budget for input)
        max_chars = 8000
        if len(transcript_text) > max_chars:
            transcript_text = transcript_text[:max_chars] + "\n\n[... transcript truncated for summarization ...]"

        prompt = SUMMARY_PROMPT.format(transcript=transcript_text)

        try:
            logger.info("Generating meeting summary...")
            response = self._model.create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=512,
                temperature=0.3,
                top_p=0.9,
            )
            summary = response["choices"][0]["message"]["content"].strip()
            logger.info("Summary generated (%d chars).", len(summary))
            return summary
        except Exception:
            logger.exception("Summarization failed")
            return None
