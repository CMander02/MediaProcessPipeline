"""KB indexer — chunks subtitles and summaries, embeds, and upserts into KBStore."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _sliding_window(text: str, chunk_size: int, overlap: int) -> list[str]:
    if not text.strip():
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = end - overlap
    return [c for c in chunks if c]


def _srt_to_chunks(srt_text: str, chunk_size: int, overlap: int) -> list[dict]:
    """Convert SRT text to sliding-window chunks, preserving timestamp spans."""
    # Parse SRT blocks: index, time range, text
    blocks: list[dict] = []
    pattern = re.compile(
        r"\d+\s*\n(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*\n([\s\S]*?)(?=\n\d+\s*\n|\Z)",
        re.MULTILINE,
    )
    for m in pattern.finditer(srt_text):
        start_ts = _ts_to_seconds(m.group(1))
        end_ts = _ts_to_seconds(m.group(2))
        text = m.group(3).strip()
        if text:
            blocks.append({"start_ts": start_ts, "end_ts": end_ts, "text": text})

    if not blocks:
        return []

    # Group blocks into sliding-window chunks by character count
    full_text = " ".join(b["text"] for b in blocks)
    text_chunks = _sliding_window(full_text, chunk_size, overlap)

    # Assign approximate timestamps by matching chunk start/end in the full text
    char_pos = 0
    block_positions: list[tuple[int, int, float, float]] = []
    for b in blocks:
        pos = full_text.find(b["text"], char_pos)
        if pos >= 0:
            block_positions.append((pos, pos + len(b["text"]), b["start_ts"], b["end_ts"]))
            char_pos = pos + len(b["text"])

    chunks: list[dict] = []
    consumed = 0
    for i, chunk_text in enumerate(text_chunks):
        start_ts = None
        end_ts = None
        for bp_start, bp_end, bp_ts_start, bp_ts_end in block_positions:
            if bp_start <= consumed + len(chunk_text) and bp_end >= consumed:
                if start_ts is None:
                    start_ts = bp_ts_start
                end_ts = bp_ts_end
        chunks.append({"chunk_index": i, "start_ts": start_ts, "end_ts": end_ts, "text": chunk_text})
        consumed += len(chunk_text) - overlap

    return chunks


def _md_to_chunks(md_text: str, chunk_size: int, overlap: int) -> list[dict]:
    """Split markdown by heading sections, then apply sliding window within sections."""
    sections = re.split(r"(?m)^#{1,3}\s+", md_text)
    chunks: list[dict] = []
    idx = 0
    for section in sections:
        section = section.strip()
        if not section:
            continue
        sub_chunks = _sliding_window(section, chunk_size, overlap)
        for text in sub_chunks:
            chunks.append({"chunk_index": idx, "start_ts": None, "end_ts": None, "text": text})
            idx += 1
    return chunks


def _ts_to_seconds(ts: str) -> float:
    ts = ts.replace(",", ".")
    parts = ts.split(":")
    h, m, s = int(parts[0]), int(parts[1]), float(parts[2])
    return h * 3600 + m * 60 + s


def index_task(task_id: str, archive_path: str | Path) -> None:
    """Index a single task's archive directory into the KB."""
    from app.core.settings import get_runtime_settings
    rt = get_runtime_settings()
    if not rt.kb_enabled or not rt.kb_embedding_api_base:
        return

    archive_dir = Path(archive_path)
    chunk_size = rt.kb_chunk_size_chars
    overlap = rt.kb_chunk_overlap_chars

    raw_chunks: list[dict] = []

    # Index subtitles (prefer polished)
    for srt_name in ("transcript_polished.srt", "transcript.srt"):
        srt_path = archive_dir / srt_name
        if srt_path.exists():
            srt_text = srt_path.read_text(encoding="utf-8", errors="replace")
            for c in _srt_to_chunks(srt_text, chunk_size, overlap):
                c["source_type"] = "subtitle"
                raw_chunks.append(c)
            break

    # Index summary
    summary_path = archive_dir / "summary.md"
    if summary_path.exists():
        md_text = summary_path.read_text(encoding="utf-8", errors="replace")
        for c in _md_to_chunks(md_text, chunk_size, overlap):
            c["source_type"] = "summary"
            raw_chunks.append(c)

    if not raw_chunks:
        logger.debug(f"KB: no indexable content found in {archive_dir}")
        return

    # Embed
    from app.services.kb.embedding import get_embedding_service
    emb_service = get_embedding_service()
    texts = [c["text"] for c in raw_chunks]
    try:
        embeddings = emb_service.embed_batch(texts)
    except Exception as e:
        logger.warning(f"KB embedding failed for task {task_id}: {e}")
        return

    if len(embeddings) != len(raw_chunks):
        logger.warning(f"KB embedding count mismatch: expected {len(raw_chunks)}, got {len(embeddings)}")
        return

    for c, emb in zip(raw_chunks, embeddings):
        c["embedding"] = emb
        c["task_id"] = task_id
        c["archive_path"] = str(archive_dir)

    from app.services.kb.store import get_kb_store
    get_kb_store().upsert_task(task_id, raw_chunks)
    logger.info(f"KB: indexed {len(raw_chunks)} chunks for task {task_id}")


def reindex_all() -> int:
    """Reindex all completed tasks with archive directories. Returns chunk count."""
    from app.core.database import get_task_store
    from app.core.settings import get_runtime_settings
    rt = get_runtime_settings()
    store = get_task_store()
    tasks = store.list_by_statuses(["completed"])
    indexed = 0
    for task in tasks:
        if task.result and task.result.get("output_dir"):
            archive_path = task.result["output_dir"]
            try:
                index_task(str(task.id), archive_path)
                indexed += 1
            except Exception as e:
                logger.warning(f"KB reindex failed for task {task.id}: {e}")
    return indexed
