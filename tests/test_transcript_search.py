from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.paths import get_workspace_paths, reset_workspace_paths  # noqa: E402
from app.services.transcript_search import TranscriptSearchService, parse_cues  # noqa: E402


def _srt(*cues: tuple[str, str]) -> str:
    blocks = []
    for index, (start, text) in enumerate(cues, start=1):
        blocks.append(f"{index}\n{start} --> {start[:-3]}999\n{text}\n")
    return "\n".join(blocks)


def _archive(root: Path, name: str, title: str, srt: str, file: str = "transcript_polished.srt"):
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "metadata.json").write_text(json.dumps({"title": title}), encoding="utf-8")
    (directory / file).write_text(srt, encoding="utf-8")
    return directory


@pytest.fixture
def library(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    (root / "tasks.db").write_bytes(b"")  # an existing (legacy layout) library
    reset_workspace_paths()
    _archive(root, "anthropic", "Building Anthropic", _srt(
        ("00:00:00,229", "[Jared Kaplan] Why are we working on AI in the first place?"),
        ("00:01:05,000", "[Dario Amodei] 我们为什么要研究人工智能？"),
    ))
    _archive(root, "podcast", "一期播客", _srt(
        ("00:00:10,000", "今天聊聊显存和模型"),
        ("00:00:20,000", "人工智能的未来"),
    ), file="transcript.srt")
    yield root
    reset_workspace_paths()


def test_parse_cues_reads_times_speakers_and_text():
    cues = parse_cues("﻿1\r\n00:01:02,500 --> 00:01:04,000\r\n[Dario] Hello\r\nthere\r\n")
    assert cues == [(62_500, "Dario", "Hello there")]


def test_search_finds_substrings_in_any_language(library: Path):
    service = TranscriptSearchService(library)
    assert service.refresh() == {"archives": 2, "indexed": 2, "removed": 0}

    english = service.search("working on ai")
    assert [group["title"] for group in english["groups"]] == ["Building Anthropic"]
    assert english["groups"][0]["hits"] == [{
        "start_ms": 229, "speaker": "Jared Kaplan",
        "text": "Why are we working on AI in the first place?",
    }]

    chinese = service.search("人工智能")
    assert {group["title"] for group in chinese["groups"]} == {"Building Anthropic", "一期播客"}

    # Two characters are below the trigram length and use a plain scan.
    short = service.search("显存")
    assert [(group["title"], group["count"]) for group in short["groups"]] == [("一期播客", 1)]


def test_search_matches_speakers(library: Path):
    service = TranscriptSearchService(library)
    service.refresh()
    speakers = service.search("dario")["speakers"]
    assert speakers == [{
        "path": str((library / "anthropic").resolve()), "title": "Building Anthropic",
        "speaker": "Dario Amodei",
    }]


def test_refresh_follows_edits_and_removals(library: Path):
    service = TranscriptSearchService(library)
    service.refresh()
    transcript = library / "podcast" / "transcript.srt"
    transcript.write_text(_srt(("00:00:10,000", "换了一句新的话")), encoding="utf-8")
    stat = transcript.stat()
    os.utime(transcript, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    (library / "anthropic" / "transcript_polished.srt").unlink()

    assert service.refresh() == {"archives": 1, "indexed": 1, "removed": 1}
    assert service.search("人工智能")["groups"] == []
    assert service.search("新的话")["groups"][0]["title"] == "一期播客"
    assert get_workspace_paths(library).temporary("search").joinpath("transcripts.db").exists()


def test_query_syntax_is_taken_literally(library: Path):
    service = TranscriptSearchService(library)
    service.refresh()
    assert service.search('AI" OR "x')["groups"] == []
    assert service.search("100%")["groups"] == []
