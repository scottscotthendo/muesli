# Hardware Compatibility

## MacBook M4 16GB

**Status: Fully Compatible**

Meeting Recorder runs comfortably on a MacBook M4 with 16GB unified memory.

### Memory Usage by Component

| Component | Disk | RAM (loaded) | Notes |
|---|---|---|---|
| faster-whisper (small.en, int8) | ~460MB | ~0.5-1GB | CPU mode, int8 quantized |
| Qwen2.5-1.5B (Q4_K_M GGUF) | ~1GB | ~1-1.5GB | Metal GPU acceleration |
| pyannote diarization (optional) | ~1GB | ~2-3GB | PyTorch, runs post-recording only |
| Audio buffers + Python runtime | -- | ~0.5GB | numpy arrays, 30s chunks |

### Peak Memory Scenarios

- **During recording** (real-time transcription): ~6-8GB
- **Post-recording summarization**: ~7-9GB (whisper unloaded, Qwen loaded)
- **With diarization enabled**: ~9-11GB (heaviest scenario, post-recording only)

All scenarios leave sufficient headroom within 16GB for macOS and typical apps.

### M4 Advantages

- **Unified memory**: The Qwen2.5 summarization model uses Metal GPU acceleration
  (`n_gpu_layers=-1`), which benefits from the M4's shared memory architecture.
- **Fast single-thread performance**: Whisper CPU inference (int8) runs efficiently
  on the M4's high-performance cores.

### Minimum Requirements

- **RAM**: 8GB minimum (without diarization), 16GB recommended
- **Disk**: ~2.5GB for models (downloaded on first run)
- **macOS**: 13.0+ (for Metal support)
