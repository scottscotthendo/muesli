"""Tests for the audio recorder module."""

import numpy as np

from meeting_recorder.recorder import _resample_to_16k


def test_resample_mono():
    # 1 second of 44100 Hz mono silence
    audio = np.zeros(44100, dtype=np.float32)
    result = _resample_to_16k(audio, 44100)
    assert result.shape == (16000,)
    assert result.dtype == np.float32


def test_resample_stereo():
    # 1 second of 44100 Hz stereo
    audio = np.zeros((44100, 2), dtype=np.float32)
    result = _resample_to_16k(audio, 44100)
    assert result.ndim == 1  # Should be mono
    assert result.shape == (16000,)


def test_resample_already_16k():
    audio = np.ones(16000, dtype=np.float32) * 0.5
    result = _resample_to_16k(audio, 16000)
    assert result.shape == (16000,)
    assert np.allclose(result, 0.5, atol=0.01)
