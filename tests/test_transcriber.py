"""Tests for the transcriber module."""

import queue
import threading

from meeting_recorder.transcriber import Transcriber


def test_transcriber_init():
    audio_q = queue.Queue()
    results_q = queue.Queue()
    stop = threading.Event()
    t = Transcriber(audio_q, results_q, stop)
    assert not t.is_model_ready()
    assert t._model is None
