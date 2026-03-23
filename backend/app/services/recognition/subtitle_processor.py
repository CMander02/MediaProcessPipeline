"""
Platform subtitle processor — speaker identification + punctuation via LLM.

When platform subtitles are available (YouTube auto/manual, Bilibili, local SRT),
this module processes them through LLM to add:
  - Speaker identification (from metadata + text context)
  - Punctuation and sentence structuring
  - Paragraph segmentation with timestamp ranges

Output is compatible with the ASR path (segments + SRT format).
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from app.models import MediaMetadata

logger = logging.getLogger(__name__)

# Chunking parameters (validated in experiment)
CHUNK_SIZE = 150
CHUNK_OVERLAP = 15
MAX_CONCURRENCY = 1  # Sequential to preserve speaker context across chunks


# ---------------------------------------------------------------------------
# Subtitle parsing
# ---------------------------------------------------------------------------

def parse_json3(path: str | Path) -> list[dict]:
    """Parse YouTube json3 subtitle format into fine-grained segments."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    events = data.get("events", [])

    segments = []
    for e in events:
        segs = e.get("segs", [])
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text:
            continue
        segments.append({
            "start_ms": e.get("tStartMs", 0),
            "end_ms": e.get("tStartMs", 0) + e.get("dDurationMs", 0),
            "text": text,
        })
    return segments


def parse_srt(path_or_content: str | Path) -> list[dict]:
    """Parse SRT subtitle into segments.

    Accepts either a file path or raw SRT content string.
    """
    p = Path(path_or_content)
    if p.exists():
        content = p.read_text(encoding="utf-8")
    else:
        content = str(path_or_content)

    segments = []
    blocks = re.split(r"\n\n+", content.strip())

    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 2:
            continue

        # Find the timestamp line
        ts_line = None
        text_lines = []
        for line in lines:
            if "-->" in line:
                ts_line = line
            elif ts_line is not None:
                text_lines.append(line)

        if not ts_line or not text_lines:
            continue

        # Parse timestamp: 00:00:01,022 --> 00:00:02,042
        ts_match = re.match(
            r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})",
            ts_line.strip(),
        )
        if not ts_match:
            continue

        g = ts_match.groups()
        start_ms = int(g[0]) * 3600000 + int(g[1]) * 60000 + int(g[2]) * 1000 + int(g[3])
        end_ms = int(g[4]) * 3600000 + int(g[5]) * 60000 + int(g[6]) * 1000 + int(g[7])

        text = " ".join(text_lines).strip()
        # Remove HTML tags if present
        text = re.sub(r"<[^>]+>", "", text)
        if text:
            segments.append({"start_ms": start_ms, "end_ms": end_ms, "text": text})

    return segments


def parse_vtt(path: str | Path) -> list[dict]:
    """Parse WebVTT subtitle into segments (similar to SRT but with header)."""
    content = Path(path).read_text(encoding="utf-8")
    # Remove WebVTT header
    content = re.sub(r"^WEBVTT.*?\n\n", "", content, flags=re.DOTALL)
    return parse_srt(content)


def parse_subtitle_file(path: str, fmt: str) -> list[dict]:
    """Parse subtitle file based on format."""
    if fmt == "json3":
        return parse_json3(path)
    elif fmt == "srt":
        return parse_srt(path)
    elif fmt == "vtt":
        return parse_vtt(path)
    else:
        raise ValueError(f"Unsupported subtitle format: {fmt}")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt_ts(ms: int) -> str:
    """Format milliseconds to HH:MM:SS."""
    s = ms // 1000
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _ts_to_seconds(ts: str) -> float:
    parts = ts.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    elif len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return 0


def _ts_to_ms(ts: str) -> int:
    return int(_ts_to_seconds(ts) * 1000)


# ---------------------------------------------------------------------------
# LLM prompt construction
# ---------------------------------------------------------------------------

def _build_context_header(metadata: MediaMetadata) -> str:
    """Build metadata context for the LLM from MediaMetadata model."""
    chapters_str = ""
    if metadata.chapters:
        chapters_str = "\n".join(
            f"  - [{_fmt_ts(int(ch.start_time * 1000))}] {ch.title}"
            for ch in metadata.chapters
        )
        chapters_str = f"\n章节:\n{chapters_str}"

    duration_str = _fmt_ts(int(metadata.duration_seconds * 1000)) if metadata.duration_seconds else "未知"
    desc = (metadata.description or "")[:1000]

    return f"""## 视频信息
- 标题: {metadata.title}
- 频道/上传者: {metadata.uploader or '未知'}
- 时长: {duration_str}
{chapters_str}

## 简介（节选）
{desc}
"""


def _build_transcript_prompt(
    context_header: str,
    segments: list[dict],
    known_speakers: list[str] | None = None,
) -> str:
    """Build the speaker identification + punctuation prompt."""
    raw_block = "\n".join(
        f"{_fmt_ts(s['start_ms'])} {s['text']}"
        for s in segments
    )

    time_range_start = _fmt_ts(segments[0]["start_ms"])
    time_range_end = _fmt_ts(segments[-1]["end_ms"])

    known_str = ""
    if known_speakers:
        known_str = f"\n已知说话人: {', '.join(known_speakers)}\n"

    return f"""你是专业的字幕转录编辑。将下面的原始字幕转换为带说话人标注、正确标点、合理分段的结构化转录稿。

{context_header}{known_str}

## 核心规则

### 内容保真
- 保留每一个口语词，包括语气词（"嗯"、"啊"、"对"）和重复
- 不翻译、不改写、不总结
- 笑声等非语言内容用括号标注：（笑）（哈哈哈）

### 说话人识别
1. 从视频元数据推断说话人（标题、频道名、简介）
2. 从文本线索推断（自我介绍、称呼、问答模式）
3. 无法确认时用有意义的标签（主持人、嘉宾）
4. 同一人连续发言不重复标注说话人名

### 标点与分段
- 添加正确的中文标点（逗号、句号、问号、感叹号、省略号）
- 每2-4句话为一个段落
- 每个段落带一个时间戳范围

## 原始字幕（{time_range_start} ~ {time_range_end}）
{raw_block}

## 输出格式
严格按以下格式输出，不要输出其他内容：

第一行输出说话人列表：
SPEAKERS: 说话人1,说话人2

然后输出转录稿，每个段落格式为：
[HH:MM:SS → HH:MM:SS] **说话人:**
段落文本，带标点。

说话人连续发言时省略说话人标注：
[HH:MM:SS → HH:MM:SS]
继续的段落文本。

说话人切换时必须标注：
[HH:MM:SS → HH:MM:SS] **新说话人:**
新说话人的文本。"""


# ---------------------------------------------------------------------------
# Parse LLM output
# ---------------------------------------------------------------------------

def _parse_transcript_output(response: str) -> dict:
    """Parse the structured transcript output from LLM."""
    lines = response.strip().split("\n")

    speakers = []
    paragraphs = []
    current_speaker = None

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Parse SPEAKERS line
        if line.upper().startswith("SPEAKERS:") or line.upper().startswith("SPEAKERS："):
            sp_text = line.split(":", 1)[-1] if ":" in line else line.split("：", 1)[-1]
            speakers = [s.strip() for s in sp_text.split(",") if s.strip()]
            continue

        # Parse timestamp + speaker line
        ts_match = re.match(
            r"\[(\d{2}:\d{2}:\d{2})\s*[→\->]+\s*(\d{2}:\d{2}:\d{2})\]\s*(.*)",
            line,
        )
        if ts_match:
            start_ts = ts_match.group(1)
            end_ts = ts_match.group(2)
            rest = ts_match.group(3).strip()

            speaker_match = re.match(r"\*\*(.+?)[:：]\*\*\s*(.*)", rest)
            if speaker_match:
                current_speaker = speaker_match.group(1).strip()
                text = speaker_match.group(2).strip()
            else:
                text = rest

            paragraphs.append({
                "start": start_ts,
                "end": end_ts,
                "speaker": current_speaker or "Unknown",
                "text": text,
            })
            continue

        # Continuation text — append to last paragraph
        if paragraphs and not line.startswith("[") and not line.upper().startswith("SPEAKERS"):
            paragraphs[-1]["text"] += " " + line if paragraphs[-1]["text"] else line

    return {"speakers": speakers, "paragraphs": paragraphs}


# ---------------------------------------------------------------------------
# Convert paragraphs to SRT and Markdown
# ---------------------------------------------------------------------------

def _paragraphs_to_srt(paragraphs: list[dict]) -> str:
    """Convert structured paragraphs to SRT format."""
    srt_blocks = []
    for i, p in enumerate(paragraphs, 1):
        start = p["start"].replace(".", ",") + ",000" if "," not in p["start"] else p["start"]
        end = p["end"].replace(".", ",") + ",000" if "," not in p["end"] else p["end"]
        # Add SRT timestamp format: HH:MM:SS,mmm
        if len(start.split(",")[0].split(":")) == 3 and "," in start:
            start_srt = start
        else:
            start_srt = f"{p['start']},000"
        if len(end.split(",")[0].split(":")) == 3 and "," in end:
            end_srt = end
        else:
            end_srt = f"{p['end']},000"

        speaker_prefix = f"[{p['speaker']}] " if p.get("speaker") else ""
        srt_blocks.append(f"{i}\n{start_srt} --> {end_srt}\n{speaker_prefix}{p['text']}")

    return "\n\n".join(srt_blocks)


def _paragraphs_to_markdown(paragraphs: list[dict], title: str = "") -> str:
    """Convert structured paragraphs to a clean Markdown document."""
    lines = []
    if title:
        lines.append(f"# {title}")
        lines.append("")

    # Group consecutive paragraphs by speaker
    groups = []
    if paragraphs:
        current = {"speaker": paragraphs[0]["speaker"], "paragraphs": [paragraphs[0]]}
        for p in paragraphs[1:]:
            if p["speaker"] == current["speaker"]:
                current["paragraphs"].append(p)
            else:
                groups.append(current)
                current = {"speaker": p["speaker"], "paragraphs": [p]}
        groups.append(current)

    speakers = set(p["speaker"] for p in paragraphs if p.get("speaker"))
    multi_speaker = len(speakers) > 1

    for g in groups:
        if multi_speaker and g["speaker"]:
            lines.append(f"**{g['speaker']}:**")
            lines.append("")

        for p in g["paragraphs"]:
            lines.append(p["text"])
            lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main processing function
# ---------------------------------------------------------------------------

async def process_subtitles(
    subtitle_path: str,
    subtitle_format: str,
    metadata: MediaMetadata,
    on_progress: Any = None,
) -> dict[str, Any]:
    """
    Process platform subtitles through LLM for speaker identification and punctuation.

    Args:
        subtitle_path: Path to the subtitle file
        subtitle_format: Format of the subtitle file ("json3", "srt", "vtt")
        metadata: Video metadata for context
        on_progress: Optional callback for progress updates

    Returns:
        Dict compatible with ASR output:
        {
            "language": str,
            "segments": list[dict],
            "srt": str,              # Original subtitle as SRT
            "polished_srt": str,     # LLM-processed SRT with speakers + punctuation
            "polished_md": str,      # Markdown version
            "speakers": list[str],
            "subtitle_source": "platform",
        }
    """
    from app.services.analysis.llm import get_llm_service

    logger.info(f"Processing platform subtitle: {subtitle_path} (format={subtitle_format})")

    # Step 1: Parse subtitle file
    segments = parse_subtitle_file(subtitle_path, subtitle_format)
    if not segments:
        raise ValueError(f"No segments found in subtitle file: {subtitle_path}")

    logger.info(f"Parsed {len(segments)} raw segments from subtitle")

    # Build original SRT from raw segments
    original_srt = _segments_to_original_srt(segments)

    # Step 2: Build context
    context_header = _build_context_header(metadata)

    # Step 3: Chunk and process
    chunks = []
    i = 0
    while i < len(segments):
        end = min(i + CHUNK_SIZE, len(segments))
        chunks.append(segments[i:end])
        i += CHUNK_SIZE - CHUNK_OVERLAP

    logger.info(f"Processing {len(segments)} segments in {len(chunks)} chunks")

    llm_service = get_llm_service()
    all_paragraphs = []
    known_speakers = []

    for idx, chunk in enumerate(chunks):
        ts_start = _fmt_ts(chunk[0]["start_ms"])
        ts_end = _fmt_ts(chunk[-1]["end_ms"])
        logger.info(f"Chunk {idx+1}/{len(chunks)} [{ts_start} -> {ts_end}] ({len(chunk)} segs)")

        prompt = _build_transcript_prompt(context_header, chunk, known_speakers or None)

        t0 = time.time()
        try:
            response = await llm_service._call(prompt)
            result = _parse_transcript_output(response)

            for sp in result["speakers"]:
                if sp and sp not in known_speakers:
                    known_speakers.append(sp)

            paras = result["paragraphs"]
            if paras:
                if idx == 0:
                    all_paragraphs.extend(paras)
                else:
                    # Skip paragraphs in the overlap region
                    non_overlap_start_ms = chunk[CHUNK_OVERLAP]["start_ms"] if len(chunk) > CHUNK_OVERLAP else chunk[0]["start_ms"]
                    non_overlap_start_s = non_overlap_start_ms / 1000

                    for p in paras:
                        p_start = _ts_to_seconds(p["start"])
                        if p_start >= non_overlap_start_s - 5:
                            all_paragraphs.append(p)

                dt = time.time() - t0
                logger.info(f"Chunk {idx+1}: {len(paras)} paras in {dt:.1f}s, speakers={known_speakers}")
            else:
                dt = time.time() - t0
                logger.warning(f"Chunk {idx+1}: empty result in {dt:.1f}s")

        except Exception as e:
            logger.error(f"Chunk {idx+1} failed: {e}")

        # Report progress
        if on_progress:
            progress = (idx + 1) / len(chunks)
            await on_progress(progress)

    logger.info(f"Subtitle processing complete: {len(all_paragraphs)} paragraphs, speakers={known_speakers}")

    # Step 4: Convert to output formats
    polished_srt = _paragraphs_to_srt(all_paragraphs)
    polished_md = _paragraphs_to_markdown(all_paragraphs, metadata.title)

    # Build segments list (compatible with ASR output)
    result_segments = []
    for p in all_paragraphs:
        result_segments.append({
            "start": _ts_to_seconds(p["start"]),
            "end": _ts_to_seconds(p["end"]),
            "text": p["text"],
            "speaker": p.get("speaker"),
        })

    return {
        "language": "zh",
        "segments": result_segments,
        "srt": original_srt,
        "polished_srt": polished_srt,
        "polished_md": polished_md,
        "speakers": known_speakers,
        "subtitle_source": "platform",
    }


def _segments_to_original_srt(segments: list[dict]) -> str:
    """Convert raw segments to basic SRT format (without speaker labels)."""
    blocks = []
    for i, seg in enumerate(segments, 1):
        start = _ms_to_srt_ts(seg["start_ms"])
        end = _ms_to_srt_ts(seg["end_ms"])
        blocks.append(f"{i}\n{start} --> {end}\n{seg['text']}")
    return "\n\n".join(blocks)


def _ms_to_srt_ts(ms: int) -> str:
    """Convert milliseconds to SRT timestamp format: HH:MM:SS,mmm"""
    h = ms // 3600000
    ms %= 3600000
    m = ms // 60000
    ms %= 60000
    s = ms // 1000
    ms %= 1000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
