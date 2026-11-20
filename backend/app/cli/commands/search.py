"""Search archive titles and indexed transcript cues."""

from __future__ import annotations

import re
import shlex
from typing import Optional

import typer

from app.cli.commands.common import api_call, client
from app.cli.commands.read import clock_time, local_archive_index, require_local
from app.cli.context import get_cli_context
from app.cli.output import emit, emit_error


def _filter_speaker(groups: list[dict], speaker: str) -> list[dict]:
    wanted = speaker.casefold()
    result = []
    for group in groups:
        hits = [hit for hit in group["hits"] if (hit.get("speaker") or "").casefold() == wanted]
        if hits:
            result.append({**group, "hits": hits, "count": len(hits)})
    return result


def _hint_ref(value: str) -> str:
    if "!" in value:
        return shlex.quote(value)
    return '"' + re.sub(r'([\\\"$`])', r'\\\1', value) + '"'


def _render(groups: list[dict], titles: list[dict], *, indexing: bool) -> str:
    ctx = get_cli_context()
    read_command = "mpp"
    if ctx.explicit_server:
        read_command += f" --server {_hint_ref(ctx.server_url)}"
    elif ctx.remote_preference is not None:
        read_command += " --remote" if ctx.remote_preference else " --local"
    read_command += " read"
    rows = []
    if titles:
        rows.extend(["Title matches", *(
            f"  {item.get('title') or item.get('archive_id') or item.get('path')}"
            for item in titles
        )])
    for group in groups:
        if not group["hits"]:
            continue
        if rows:
            rows.append("")
        label = group.get("title") or group.get("path") or "Archive"
        count = group["count"]
        rows.append(f"{label} ({count} {'hit' if count == 1 else 'hits'})")
        for hit in group["hits"]:
            when = clock_time(hit["start_ms"] / 1000)
            speaker = f"[{hit['speaker']}] " if hit.get("speaker") else ""
            rows.append(f"  [{when}] {speaker}{hit['text']}")
        ref = _hint_ref(str(group.get("archive_id") or group.get("path") or label))
        when = clock_time(group["hits"][0]["start_ms"] / 1000)
        rows.append(f"  {read_command} {ref} --at {when}")
    if not rows:
        rows.append("No matches.")
    if indexing:
        rows.append("Transcript indexing is in progress.")
    return "\n".join(rows)


def search(
    query: str = typer.Argument(...),
    limit: int = typer.Option(20, "--limit", "-n", min=1, max=50),
    speaker: Optional[str] = typer.Option(None, "--speaker"),
    refresh: bool = typer.Option(False, "--refresh", help="同步刷新本地离线字幕索引"),
):
    """搜索归档标题和全文字幕。"""
    query = query.strip()
    if not query or len(query) > 200:
        emit_error("invalid_query", "Enter a query of 1 to 200 characters.", exit_code=2)
    api = client()
    if api.ping():
        filters = {"speaker": speaker} if speaker is not None else {}
        data = api_call(lambda: api.search_transcripts(query, limit=limit, **filters))
        page = api_call(lambda: api.list_archives_paged(page=1, page_size=min(limit, 50),
                                                       search=query))
        if speaker is not None and data.get("speaker_filter") != speaker:
            data = {**data, "groups": _filter_speaker(data.get("groups") or [], speaker)}
    else:
        require_local()
        from app.services.transcript_search import get_transcript_search_service

        service = get_transcript_search_service()
        data = api_call(lambda: service.search_offline(
            query, limit=limit, speaker=speaker, refresh=refresh,
        ))
        page = api_call(lambda: local_archive_index().list_page(
            page=1, page_size=min(limit, 50), search=query,
        ))
    titles = page.get("archives") or []
    ids = {item.get("path"): item.get("archive_id") for item in titles if item.get("archive_id")}
    groups = [
        {**group, "archive_id": ids[group.get("path")]} if group.get("path") in ids else group
        for group in data.get("groups") or []
    ]
    emit({**data, "groups": groups, "title_matches": titles},
         text=lambda: _render(groups, titles, indexing=bool(data.get("indexing"))))
