"""Benchmark the four sherpa-onnx backends against an existing subtitle task."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.recognition.chunking import ASRChunker  # noqa: E402
from app.services.recognition.quality import filter_asr_segments  # noqa: E402
from app.services.recognition.sherpa_catalog import model_ids  # noqa: E402
from app.services.recognition.sherpa_onnx_asr import (  # noqa: E402
    SherpaOnnxASRService,
)
from app.services.recognition.sherpa_runtime import release_sherpa_runtime  # noqa: E402

_TIMECODE = re.compile(
    r"(?P<sh>\d+):(?P<sm>\d+):(?P<ss>\d+)[,.](?P<sms>\d+)\s+-->\s+"
    r"(?P<eh>\d+):(?P<em>\d+):(?P<es>\d+)[,.](?P<ems>\d+)"
)


def _seconds(match: re.Match[str], prefix: str) -> float:
    return (
        int(match[f"{prefix}h"]) * 3600
        + int(match[f"{prefix}m"]) * 60
        + int(match[f"{prefix}s"])
        + int(match[f"{prefix}ms"]) / 1000
    )


def parse_srt(path: Path) -> list[dict[str, Any]]:
    blocks = re.split(r"\r?\n\s*\r?\n", path.read_text(encoding="utf-8-sig").strip())
    segments: list[dict[str, Any]] = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_index = next(
            (index for index, line in enumerate(lines) if "-->" in line),
            None,
        )
        if timing_index is None:
            continue
        match = _TIMECODE.fullmatch(lines[timing_index])
        if match is None:
            continue
        segments.append(
            {
                "start": _seconds(match, "s"),
                "end": _seconds(match, "e"),
                "text": " ".join(lines[timing_index + 1 :]),
            }
        )
    return segments


def normalized_text(segments: list[dict[str, Any]]) -> str:
    text = "".join(str(segment.get("text") or "") for segment in segments).lower()
    return "".join(character for character in text if character.isalnum())


def segment_metrics(
    segments: list[dict[str, Any]],
    baseline: list[dict[str, Any]],
    duration: float,
) -> dict[str, Any]:
    starts = [float(segment.get("start") or 0.0) for segment in segments]
    ends = [float(segment.get("end") or 0.0) for segment in segments]
    valid = all(end > start for start, end in zip(starts, ends))
    monotonic = all(starts[index] >= starts[index - 1] for index in range(1, len(starts)))
    generated_text = normalized_text(segments)
    baseline_text = normalized_text(baseline)
    return {
        "segment_count": len(segments),
        "character_count": len(generated_text),
        "baseline_character_count": len(baseline_text),
        "character_count_ratio": (
            round(len(generated_text) / len(baseline_text), 4) if baseline_text else None
        ),
        "sequence_similarity": round(
            SequenceMatcher(None, baseline_text, generated_text, autojunk=False).ratio(),
            4,
        ),
        "timestamps_valid": valid,
        "timestamps_monotonic": monotonic,
        "last_timestamp_sec": round(max(ends, default=0.0), 3),
        "timeline_reach": round(max(ends, default=0.0) / duration, 4) if duration else None,
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# sherpa-onnx 历史任务基准",
        "",
        f"- 音频：`{report['audio_path']}`",
        f"- 参考字幕：`{report['baseline_srt']}`",
        f"- 时长：{report['audio_duration_sec']} 秒",
        f"- 设备：{report['device']}",
        "",
        "| 模型 | 状态 | CUDA | 时间源 | RTF | 字符相似度 | 字符量比 | 字幕段 | 时间轴 |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in report["results"]:
        if item["status"] != "ok":
            lines.append(
                f"| {item['model_id']} | 失败 |  |  |  |  |  |  | {item['error']} |"
            )
            continue
        metrics = item["metrics"]
        timeline = (
            "有效"
            if metrics["timestamps_valid"] and metrics["timestamps_monotonic"]
            else "异常"
        )
        lines.append(
            f"| {item['model_id']} | 通过 | {item['runtime_provider']} | "
            f"{item['timestamp_source']} | {item['metadata']['rtf']} | "
            f"{metrics['sequence_similarity']} | {metrics['character_count_ratio']} | "
            f"{metrics['segment_count']} | {timeline} |"
        )
    lines.extend(
        [
            "",
            "字符相似度以历史任务字幕为参照，反映文本接近程度；"
            "它用于回归比较，不等同于人工标注 WER。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("baseline_srt", type=Path)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--vad-model", type=Path, required=True)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--clip-sec", type=float, default=0.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    baseline = parse_srt(args.baseline_srt.resolve())
    service = SherpaOnnxASRService()
    source_audio = args.audio.resolve()
    audio_path = source_audio
    clip_directory: tempfile.TemporaryDirectory[str] | None = None
    if args.clip_sec > 0:
        clip_directory = tempfile.TemporaryDirectory(prefix="mpp-sherpa-benchmark-")
        audio_path = Path(clip_directory.name) / "clip.wav"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-t",
                str(args.clip_sec),
                "-i",
                str(source_audio),
                "-ac",
                "1",
                "-ar",
                "16000",
                str(audio_path),
            ],
            check=True,
        )
        baseline = [
            segment for segment in baseline if float(segment["start"]) < args.clip_sec
        ]
    duration = ASRChunker().probe_duration(audio_path)
    report: dict[str, Any] = {
        "audio_path": str(source_audio),
        "baseline_srt": str(args.baseline_srt.resolve()),
        "audio_duration_sec": round(duration, 3),
        "device": args.device,
        "model_root": str(args.model_root.resolve()),
        "results": [],
    }

    for model_id in model_ids():
        print(json.dumps({"running": model_id}, ensure_ascii=True), flush=True)
        try:
            result = service.transcribe(
                str(audio_path),
                language=args.language,
                diarize=False,
                chunk_strategy="vad",
                runtime_config={
                    "model_id": model_id,
                    "model_root": str(args.model_root.resolve()),
                    "device": args.device,
                    "num_threads": 4,
                    "chunk_strategy": "vad",
                    "max_chunk_sec": 30,
                    "vad_model_path": str(args.vad_model.resolve()),
                    "timestamp_mode": "auto",
                },
            )
            filtered_segments, quality_diagnostics = filter_asr_segments(
                result["segments"]
            )
            report["results"].append(
                {
                    "model_id": model_id,
                    "status": "ok",
                    "runtime_provider": result["runtime_provider"],
                    "runtime_version": result["runtime_version"],
                    "timestamp_source": result["timestamp_source"],
                    "language": result["language"],
                    "metadata": result["metadata"],
                    "quality_diagnostics": quality_diagnostics,
                    "metrics": segment_metrics(filtered_segments, baseline, duration),
                    "segments": filtered_segments,
                }
            )
        except Exception as exc:
            report["results"].append(
                {"model_id": model_id, "status": "error", "error": str(exc)}
            )
        finally:
            release_sherpa_runtime()

    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output.with_suffix(".md").write_text(markdown_report(report), encoding="utf-8")
    if clip_directory is not None:
        clip_directory.cleanup()
    print(json.dumps({"report": str(output)}, ensure_ascii=True))
    return 0 if all(item["status"] == "ok" for item in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
