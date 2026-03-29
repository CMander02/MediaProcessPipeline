"""Preprocessing service - UVR5 vocal separation and VAD splitting.

Heavy dependencies (torch, torchaudio, audio_separator) are loaded lazily
when the service functions are first called — not at import time.
"""


def separate_vocals(*args, **kwargs):
    from app.services.preprocessing.uvr import separate_vocals as _fn
    return _fn(*args, **kwargs)


def split_long_audio(*args, **kwargs):
    from app.services.preprocessing.vad_splitter import split_long_audio as _fn
    return _fn(*args, **kwargs)


def merge_srt_segments(*args, **kwargs):
    from app.services.preprocessing.vad_splitter import merge_srt_segments as _fn
    return _fn(*args, **kwargs)


__all__ = [
    "separate_vocals",
    "split_long_audio",
    "merge_srt_segments",
]
