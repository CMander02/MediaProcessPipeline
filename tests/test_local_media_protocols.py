"""Local media parsers must not follow network references in downloaded playlists."""
from __future__ import annotations

import io
import shutil
import subprocess
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.pipeline_steps.participant_evidence import _extract_frame
from app.core.pipeline_steps.sources import _extract_audio_from_video
from app.services.recognition.chunking import ASRChunker, AudioChunk
from app.services.recognition.moss_cpp_asr import MossCppASRService
from app.services.recognition.siliconflow_asr import SiliconFlowASRService

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required"
)


def wav_bytes() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x10" * 1600)
    return output.getvalue()


@pytest.fixture
def malicious_playlist(tmp_path):
    requests = []
    payload = wav_bytes()

    class Canary(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = HTTPServer(("localhost", 0), Canary)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    playlist = tmp_path / "下载音频.m3u8"
    playlist.write_text(
        "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:0.1,\n"
        f"http://localhost:{server.server_port}/private.ts\n#EXT-X-ENDLIST\n",
        encoding="utf-8",
    )
    try:
        yield playlist, requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_playlist_fixture_reaches_canary_when_http_is_explicitly_enabled(malicious_playlist):
    playlist, requests = malicious_playlist
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-protocol_whitelist", "file,http,tcp,crypto,data",
         "-show_entries", "format=duration", str(playlist)],
        capture_output=True, timeout=10,
    )
    assert requests, result.stderr.decode("utf-8", errors="replace")


def parse_local(case, source, target, monkeypatch):
    chunker = ASRChunker()
    if case == "chunker_probe":
        return chunker.probe_duration(source)
    if case == "siliconflow_probe":
        return SiliconFlowASRService()._probe_duration(str(source))
    if case == "moss_probe":
        return MossCppASRService._probe_duration(str(source))
    if case == "chunker_export":
        return chunker.export_wav(source, AudioChunk(0, .1), target)
    if case == "siliconflow_export":
        return SiliconFlowASRService()._export_chunk_ffmpeg(str(source), {"start": 0, "end": .1}, str(target))
    if case == "moss_export":
        return MossCppASRService._prepare_wav_chunk(str(source), target.parent, chunk_index=0, start_sec=0, duration_sec=.1)
    if case == "chunker_pcm":
        return chunker._load_audio_float32(source)
    if case == "source_extract":
        return _extract_audio_from_video(source, target)
    if case == "frame_extract":
        return _extract_frame(source, target.parent / "frames", 0)
    if case == "llama_decode":
        from app.services.recognition import llama_cpp_asr
        monkeypatch.setattr(llama_cpp_asr, "validate_llama_asr_config", lambda _config: "")
        return llama_cpp_asr.LlamaCppASRService().transcribe(str(source), runtime_config={})
    if case == "diarization_decode":
        from app.services.recognition.diarization import DiarizationService
        # The blocked decode precedes all tensor operations. No ML runtime is needed.
        monkeypatch.setitem(sys.modules, "torch", SimpleNamespace())
        monkeypatch.setitem(sys.modules, "torchaudio", SimpleNamespace())
        return DiarizationService._load_audio(source)
    if case == "sherpa_stream":
        model = target.parent / "fake-vad.onnx"
        model.write_bytes(b"test")
        monkeypatch.setitem(sys.modules, "sherpa_onnx", SimpleNamespace(
            VadModelConfig=lambda: SimpleNamespace(silero_vad=SimpleNamespace(window_size=512), validate=lambda: True),
            VoiceActivityDetector=lambda *_args, **_kwargs: SimpleNamespace(empty=lambda: True, flush=lambda: None),
        ))
        return chunker.sherpa_vad_chunks(source, model_path=model)
    raise AssertionError(case)


@pytest.mark.parametrize("case", [
    "chunker_probe", "siliconflow_probe", "moss_probe", "chunker_export",
    "siliconflow_export", "moss_export", "chunker_pcm", "source_extract",
    "frame_extract", "llama_decode", "diarization_decode", "sherpa_stream",
])
def test_local_media_calls_reject_http_playlists_without_network_access(case, malicious_playlist, tmp_path, monkeypatch):
    playlist, requests = malicious_playlist
    with pytest.raises((RuntimeError, subprocess.CalledProcessError)):
        parse_local(case, playlist, tmp_path / "output.wav", monkeypatch)
    assert requests == []


@pytest.mark.parametrize("case", [
    "chunker_probe", "siliconflow_probe", "moss_probe", "chunker_export",
    "siliconflow_export", "moss_export", "chunker_pcm", "source_extract",
])
def test_normal_local_audio_still_probes_and_decodes(case, tmp_path, monkeypatch):
    source = tmp_path / "正常音频 🎧.wav"
    source.write_bytes(wav_bytes())
    target = tmp_path / "output.wav"
    result = parse_local(case, source, target, monkeypatch)
    if case.endswith("probe"):
        assert result == pytest.approx(.1, abs=.001)
    elif case == "chunker_pcm":
        assert len(result) == 1600
        assert float(result[0]) == pytest.approx(.125, abs=.001)
    else:
        output = Path(result) if result else target
        with wave.open(str(output), "rb") as audio:
            assert audio.getframerate() == 16000
            assert audio.getnchannels() == 1
            assert audio.getnframes() == 1600


def test_normal_local_video_still_produces_participant_frame(tmp_path):
    video = tmp_path / "正常视频.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=c=blue:s=64x64:r=1:d=1", "-c:v", "mpeg4", str(video)],
        capture_output=True, check=True, timeout=10,
    )
    frame = _extract_frame(video, tmp_path / "frames", 0)
    assert Path(frame["path"]).read_bytes().startswith(b"\xff\xd8")
