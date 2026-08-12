import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.services.recognition.sherpa_catalog import (  # noqa: E402
    catalog_status,
    model_ids,
    resolve_model,
)
from app.services.recognition.sherpa_onnx_asr import (  # noqa: E402
    SherpaOnnxASRService,
    _clean_text,
    _merge_timed_units,
)
from app.services.recognition.sherpa_runtime import (  # noqa: E402
    SherpaRuntime,
    SherpaRuntimeOptions,
)


def _fake_qwen_bundle(root: Path) -> Path:
    directory = root / "qwen3-asr-1.7b-onnx"
    directory.mkdir(parents=True)
    for filename in ("conv_frontend.onnx", "encoder.int8.onnx", "decoder.int8.onnx"):
        (directory / filename).write_bytes(b"model")
    (directory / "tokenizer").mkdir()
    checksums = {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
        for name in ("conv_frontend.onnx", "encoder.int8.onnx", "decoder.int8.onnx")
    }
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "id": "qwen3-asr-1.7b-onnx",
                "source": "https://example.test/model",
                "license": "Apache-2.0",
                "files": {
                    "conv_frontend": "conv_frontend.onnx",
                    "encoder": "encoder.int8.onnx",
                    "decoder": "decoder.int8.onnx",
                    "tokenizer": "tokenizer",
                },
                "checksums": checksums,
            }
        ),
        encoding="utf-8",
    )
    return directory


def test_catalog_exposes_four_default_models():
    assert model_ids() == (
        "qwen3-asr-1.7b-onnx",
        "sensevoice-small-int8",
        "paraformer-zh-int8",
        "whisper-small-multi-int8",
    )


def test_catalog_resolves_complete_bundle_and_reports_missing_ones(tmp_path):
    directory = _fake_qwen_bundle(tmp_path)

    model = resolve_model("qwen3-asr-1.7b-onnx", tmp_path)
    status = catalog_status(tmp_path)

    assert model.directory == directory.resolve()
    assert model.family == "qwen3_asr"
    assert model.files["decoder"] == (directory / "decoder.int8.onnx").resolve()
    assert status[0]["installed"] is True
    assert sum(item["installed"] for item in status) == 1


def test_catalog_rejects_incomplete_bundle(tmp_path):
    directory = _fake_qwen_bundle(tmp_path)
    (directory / "decoder.int8.onnx").unlink()

    with pytest.raises(FileNotFoundError, match="incomplete"):
        resolve_model("qwen3-asr-1.7b-onnx", tmp_path)


def test_runtime_reuses_same_recognizer_and_releases_on_option_change(tmp_path, monkeypatch):
    spec = resolve_model("qwen3-asr-1.7b-onnx", _fake_qwen_bundle(tmp_path).parent)
    created = []

    def create(_spec, options, provider):
        recognizer = object()
        created.append((recognizer, options.num_threads, provider))
        return recognizer

    monkeypatch.setattr(SherpaRuntime, "_create", staticmethod(create))
    runtime = SherpaRuntime()
    first, first_info = runtime.get(spec, SherpaRuntimeOptions(device="cpu", num_threads=2))
    second, _ = runtime.get(spec, SherpaRuntimeOptions(device="cpu", num_threads=2))
    third, third_info = runtime.get(spec, SherpaRuntimeOptions(device="cpu", num_threads=4))

    assert first is second
    assert third is not first
    assert len(created) == 2
    assert first_info.provider == "cpu"
    assert third_info.model_id == spec.id


def test_runtime_auto_falls_back_from_cuda_to_cpu(tmp_path, monkeypatch):
    spec = resolve_model("qwen3-asr-1.7b-onnx", _fake_qwen_bundle(tmp_path).parent)
    calls = []

    monkeypatch.setattr(
        SherpaRuntime,
        "_provider_candidates",
        staticmethod(lambda _device: ("cuda", "cpu")),
    )

    def create(_spec, _options, provider):
        calls.append(provider)
        if provider == "cuda":
            raise RuntimeError("CUDA unavailable")
        return object()

    monkeypatch.setattr(SherpaRuntime, "_create", staticmethod(create))
    _, info = SherpaRuntime().get(spec, SherpaRuntimeOptions(device="auto"))

    assert calls == ["cuda", "cpu"]
    assert info.provider == "cpu"


def test_native_segment_timestamps_are_offset_and_cleaned():
    result = SimpleNamespace(
        segment_timestamps=[0.1, 1.4],
        segment_durations=[1.0, 0.8],
        segment_texts=[" 你好 ", " 世界 "],
    )

    assert SherpaOnnxASRService._native_segments(result, 10.0) == [
        {"start": 10.1, "end": 11.1, "text": "你好"},
        {"start": 11.4, "end": 12.2, "text": "世界"},
    ]


def test_native_zero_duration_uses_next_segment_start():
    result = SimpleNamespace(
        segment_timestamps=[0.0, 1.5],
        segment_durations=[0.0, 0.0],
        segment_texts=["first", "second"],
    )

    assert SherpaOnnxASRService._native_segments(result, 3.0) == [
        {"start": 3.0, "end": 4.5, "text": "first"},
        {"start": 4.5, "end": 4.51, "text": "second"},
    ]


def test_qwen_prompt_marker_is_removed_from_text():
    assert _clean_text("language Chinese<asr_text>你好") == "你好"
    assert _clean_text("<message>") == ""
    assert _clean_text("<thöö") == ""


def test_token_timestamps_merge_into_readable_subtitle_cues():
    result = SimpleNamespace(
        tokens=["你", "好", "。", " hello", " world", "!"],
        timestamps=[0.0, 0.2, 0.4, 0.8, 1.1, 1.4],
        durations=[0.2] * 6,
    )

    assert SherpaOnnxASRService._token_segments(result, 2.0) == [
        {"start": 2.0, "end": 2.6, "text": "你好。"},
        {"start": 2.8, "end": 3.6, "text": "hello world!"},
    ]


def test_bpe_token_pieces_are_reconstructed_without_extra_spaces():
    result = SimpleNamespace(
        tokens=["The", " tri", "bal", " chief", "tain", "."],
        timestamps=[0.0, 0.2, 0.3, 0.5, 0.6, 0.8],
        durations=[0.1] * 6,
    )

    assert SherpaOnnxASRService._token_segments(result, 0.0) == [
        {"start": 0.0, "end": 0.9, "text": "The tribal chieftain."}
    ]


def test_forced_alignment_units_form_sentence_cues():
    units = [
        {"text": "这是", "start": 0.0, "end": 0.3},
        {"text": "测试。", "start": 0.3, "end": 0.8},
        {"text": "下一句", "start": 1.0, "end": 1.5},
    ]

    assert _merge_timed_units(units, offset=5.0) == [
        {"start": 5.0, "end": 5.8, "text": "这是测试。"},
        {"start": 6.0, "end": 6.5, "text": "下一句"},
    ]
