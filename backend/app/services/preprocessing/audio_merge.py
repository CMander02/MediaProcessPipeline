"""Bounded-memory WAV concatenation for audio-separator's chunk merger."""

from pathlib import Path


def merge_wav_chunks(chunk_paths, output_path):
    import soundfile as sf

    if not chunk_paths:
        raise ValueError("Cannot merge empty list of chunks")
    destination = Path(output_path)
    temporary = destination.with_name(destination.name + ".merging")
    try:
        with sf.SoundFile(str(chunk_paths[0])) as first:
            expected = (first.samplerate, first.channels, first.subtype)
        with sf.SoundFile(
            str(temporary), "w", samplerate=expected[0], channels=expected[1],
            subtype=expected[2], format="WAV",
        ) as output:
            for path in chunk_paths:
                with sf.SoundFile(str(path)) as source:
                    if (source.samplerate, source.channels, source.subtype) != expected:
                        raise ValueError("UVR chunks have incompatible audio formats")
                    for block in source.blocks(blocksize=65536, dtype="float32", always_2d=True):
                        output.write(block)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return str(destination)


def install_streaming_chunk_merger():
    """Adapt the library's WAV merge hook; retain its non-WAV exporters."""
    try:
        from audio_separator.separator.audio_chunking import AudioChunker
    except ModuleNotFoundError as exc:
        if exc.name == "audio_separator.separator.audio_chunking":
            return  # Older releases have no built-in chunker.
        raise

    original = AudioChunker.merge_chunks
    if getattr(original, "_mpp_streaming", False):
        return

    def merge_chunks(self, paths, output_path):
        if Path(output_path).suffix.lower() == ".wav" and all(
            Path(path).suffix.lower() == ".wav" for path in paths
        ):
            self.logger.info("Merging %d UVR chunks with bounded memory", len(paths))
            return merge_wav_chunks(paths, output_path)
        return original(self, paths, output_path)

    merge_chunks._mpp_streaming = True
    AudioChunker.merge_chunks = merge_chunks
