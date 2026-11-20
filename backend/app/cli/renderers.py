"""Plain text views of CLI response data and archive artifacts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def fmt_ms_timestamp(milliseconds: int | float) -> str:
    value = int(milliseconds)
    seconds, remainder = divmod(abs(value), 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    sign = "-" if value < 0 else ""
    return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}.{remainder:03d}"


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, dict):
        return f"{{{len(value)} keys}}" if value else "{}"
    if isinstance(value, list):
        if any(isinstance(item, (dict, list)) for item in value):
            return f"[{len(value)} items]"
        return ", ".join(_cell(item) for item in value) if value else "[]"
    return " ".join(str(value).replace("\t", " ").splitlines())


def _row(*values: Any) -> str:
    return "\t".join(_cell(value) for value in values)


def _table(header: str, rows: Iterable[str]) -> str:
    return "\n".join([header, *rows])


def render_fields(data: Mapping[str, Any], prefix: str = "") -> str:
    rows = []
    for key, value in data.items():
        label = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value:
            rows.append(render_fields(value, label))
        else:
            rows.append(_row(label, value))
    return "\n".join(rows)


def _seconds_timestamp(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return fmt_ms_timestamp(round(value * 1000))
    return None


def render_source_probe(data: dict[str, Any]) -> str:
    fields = dict(data)
    if "duration" in fields:
        fields["duration"] = _seconds_timestamp(fields["duration"]) or fields["duration"]
    return render_fields(fields)


def render_source_collection(data: dict[str, Any]) -> str:
    return _table(
        "ID\tDURATION\tTITLE",
        (
            _row(item.get("id"), _seconds_timestamp(item.get("duration")), item.get("title"))
            for item in data.get("items") or []
        ),
    )


def render_storage_usage(data: dict[str, Any]) -> str:
    fields = {key: value for key, value in data.items() if key != "by_type"}
    types = data.get("by_type") or {}
    return "\n".join([
        render_fields(fields),
        _table("TYPE\tBYTES\tSIZE", (
            _row(name, usage.get("bytes"), usage.get("formatted"))
            for name, usage in types.items()
        )),
    ])


def render_storage_media(preview: dict[str, Any], applied: dict[str, Any] | None = None) -> str:
    fields = {key: value for key, value in preview.items() if key != "entries"}
    rows = [
        render_fields(fields),
        _table("ACTION\tBYTES\tROLE\tPATH\tREASON", (
            _row("delete" if item["delete"] else "keep", item.get("bytes"),
                 item.get("role"), item["path"], item.get("reason"))
            for item in preview.get("entries") or []
        )),
    ]
    if applied is not None:
        rows.append(_row("reclaimed_bytes", applied.get("reclaimed_bytes")))
        rows.extend(_row("deleted", item["path"]) for item in applied.get("cleaned") or [])
        rows.extend(_render_errors(applied.get("errors") or []))
    return "\n".join(rows)


def _render_errors(errors: Iterable[dict[str, Any]]) -> Iterable[str]:
    for item in errors:
        yield _row("error", item.get("path") or item.get("task_id"), item.get("error"))


def render_storage_clean(data: dict[str, Any]) -> str:
    fields = {
        key: value for key, value in data.items()
        if key not in {"candidates", "cleaned", "errors", "skipped", "unclassified"}
    }
    rows = [
        render_fields(fields),
        _table("CANDIDATE\tBYTES\tREASON", (
            _row(item["path"], item.get("bytes"), item.get("reason"))
            for item in data.get("candidates") or []
        )),
    ]
    for key in ("cleaned", "skipped", "unclassified"):
        rows.extend(_row(key, path) for path in data.get(key) or [])
    rows.extend(_render_errors(data.get("errors") or []))
    return "\n".join(rows)


def render_timeline_event(event: dict[str, Any]) -> str:
    data = event.get("data") or {}
    return _row(
        event.get("timestamp"),
        event.get("event_type") or event.get("type"),
        event.get("step_id") or event.get("stage") or data.get("step"),
        event.get("message") or data.get("message") or data.get("error"),
    )


def render_timeline(events: list[dict[str, Any]]) -> str:
    return _table("TIME\tEVENT\tSTEP\tMESSAGE", map(render_timeline_event, events))


def render_task(data: dict[str, Any]) -> str:
    task = data["task"]
    result = task.get("result") or {}
    fields = {key: value for key, value in task.items() if key != "result"}
    if isinstance(fields.get("progress"), (int, float)):
        fields["progress"] = f"{fields['progress']:.0%}"
    for key in ("output_dir", "warnings"):
        if key in result:
            fields[key] = result[key]
    title = (result.get("metadata") or {}).get("title")
    if title:
        fields = {"title": title, **fields}
    sections = [render_fields(fields)]
    if "timeline" in data:
        sections.append(render_timeline(data["timeline"]))
    if "files" in data:
        sections.append(_table("FILE\tBYTES", (
            _row(item.get("name") or item.get("path"), item.get("size", item.get("bytes")))
            for item in data["files"]
        )))
    return "\n\n".join(sections)


def render_archive(data: dict[str, Any]) -> str:
    fields = {key: value for key, value in data.items() if key not in {"metadata", "analysis"}}
    if "duration_seconds" in fields:
        fields["duration"] = _seconds_timestamp(fields.pop("duration_seconds"))
    metadata = dict(data.get("metadata") or {})
    chapters = metadata.pop("chapters", None)
    if metadata:
        fields["metadata"] = metadata
    if data.get("analysis"):
        fields["analysis"] = data["analysis"]
    sections = [render_fields(fields)]
    if isinstance(chapters, list) and chapters:
        sections.append(render_chapters([
            chapter for chapter in chapters if isinstance(chapter, dict)
        ]))
    return "\n\n".join(sections)


def _node_time(node: Mapping[str, Any]) -> str | None:
    if node.get("start_ms") is not None:
        return fmt_ms_timestamp(node["start_ms"])
    return _seconds_timestamp(node.get("start", node.get("start_time")))


def render_mindmap_tree(
    root: Mapping[str, Any], *, plain: bool = False, depth: int | None = None,
    timestamps: bool = True,
) -> str:
    branch, last_branch, vertical = ("|-- ", "`-- ", "|   ") if plain else ("├── ", "└── ", "│   ")
    lines = []

    def visit(node: Mapping[str, Any], connector: str, child_indent: str, level: int) -> None:
        start = _node_time(node) if timestamps else None
        title = _cell(node.get("title"))
        lines.append(f"{connector}{title}" + (f"\t{start}" if start else ""))
        if depth is not None and level >= depth:
            return
        children = node.get("children") or []
        for index, child in enumerate(children):
            last = index == len(children) - 1
            visit(child, child_indent + (last_branch if last else branch),
                  child_indent + ("    " if last else vertical), level + 1)

    visit(root, "", "", 0)
    return "\n".join(lines)


def render_chapter_table(chapters: Sequence[Mapping[str, Any]], *, timestamps: bool = True) -> str:
    return _table("INDEX\tSTART\tEND\tTITLE\tSUMMARY", (
        _row(item["index"], _seconds_timestamp(item["start"]) if timestamps else None,
             _seconds_timestamp(item["end"]) if timestamps else None,
             "  " * item["depth"] + _cell(item["title"]), item["summary"])
        for item in chapters
    ))


def render_chapters(chapters: Sequence[Mapping[str, Any]]) -> str:
    def rows(nodes: Sequence[Mapping[str, Any]], depth: int = 0) -> Iterable[str]:
        for node in nodes:
            yield _row(_node_time(node), "  " * depth + _cell(node.get("title")))
            yield from rows(node.get("children") or [], depth + 1)

    return _table("START\tTITLE", rows(chapters))


def render_note_with_image_descriptions(
    note: str, descriptions: Sequence[Mapping[str, Any]],
) -> str:
    sections = [note.rstrip()]
    for image in descriptions:
        text = (image.get("text") or "").strip()
        if text:
            index = image.get("index")
            label = f"Image {index}" if index is not None else "Image"
            sections.append(f"{label}\n{text}")
    return "\n\n".join(section for section in sections if section)


def render_speaker_dossier(profile: dict[str, Any]) -> str:
    lines = [
        _row(profile.get("title"), profile.get("task_id"), profile.get("source_file")),
        _row(f"Cues {profile['total_cues']}", f"Speakers {profile['speaker_count']}",
             f"Anonymous {profile['anonymous_count']}", f"Pending {profile['pending_count']}"),
    ]
    if profile.get("derived_content_stale"):
        reason = (
            profile.get("derived_content_stale_reason") or "Summary and mind map need regeneration."
        )
        lines.append(f"[derived_content_stale] {_cell(reason)}")
    if (quality := profile.get("speaker_quality")) and quality.get("status"):
        lines.append(_row("Speaker quality", quality["status"], quality.get("reason")))
    visual = profile.get("visual_participants") or {}
    if visual.get("participants") or visual.get("count_estimate") is not None:
        lines.extend(["", "Visual participant evidence"])
        if visual.get("count_estimate") is not None:
            lines.append(_row("Estimated participants", visual["count_estimate"]))
        lines.append("NAME\tROLE\tEVIDENCE")
        for participant in visual.get("participants", []):
            evidence = participant.get("evidence") or []
            observed = " | ".join(
                f"{_cell(row.get('source'))} [{_cell(row.get('quote'))}]" for row in evidence
            )
            lines.append(_row(participant.get("name"), participant.get("role"), observed))
    for key, label in (("conflicts", "Conflict"), ("uncertainties", "Uncertainty")):
        lines.extend(_row(label, value) for value in visual.get(key, []))
    for speaker in profile.get("speakers") or []:
        lines.extend(["", _row(
            speaker["name"], f"{speaker['share_pct']:.2f}%", f"{speaker['duration_seconds']:.3f}s",
            f"{speaker['cue_count']} cues", speaker["first_timestamp"], speaker["last_timestamp"],
        )])
        lines.append(_row("Source label", speaker.get("source_label"), speaker.get("status"),
                          speaker.get("provenance")))
        for key, label in (
            ("identity_quotes", "Identity quote"), ("sample_quotes", "Sample quote"),
        ):
            for quote in speaker.get(key, []):
                lines.append(_row(label, f"#{quote['index']}", quote["timestamp"], quote["text"]))
        for suggestion in speaker.get("voiceprint_suggestions", []):
            name = suggestion.get("current_name") or suggestion.get("person_name")
            lines.append(_row("Voiceprint candidate", name, suggestion.get("similarity"),
                              suggestion.get("reason")))
    return "\n".join(lines)


def render_pending_speaker_cues(cues: list[dict[str, Any]]) -> str:
    def turn(cue: dict | None) -> str:
        if cue is None:
            return "-"
        speaker = _cell(cue.get("speaker") or "UNKNOWN")
        return f"#{cue['index']} [{speaker}] {_cell(cue['text'])}"

    lines = []
    for cue in cues:
        lines.extend([
            f"#{cue['index']} [{_cell(cue['timestamp'])}]",
            f"{turn(cue['prev_cue'])} -> >>> [UNKNOWN] {_cell(cue['text'])} <<<"
            f" -> {turn(cue['next_cue'])}",
        ])
        for candidate in cue["candidates"]:
            lines.append(_row(
                "Candidate", candidate["speaker"],
                f"overlap {candidate['acoustic_seconds']:g}s"
                if candidate.get("acoustic_seconds") is not None else None,
                f"voice similarity {candidate['voice_similarity']:g}"
                if candidate.get("voice_similarity") is not None else None,
            ))
    return "\n".join(lines) if lines else "No pending cues."


def render_speaker_repair_preview(preview: dict[str, Any]) -> str:
    fields = ("index", "timestamp", "old_speaker", "proposed_speaker", "text")
    table = _table(
        "INDEX\tTIMESTAMP\tOLD\tPROPOSED\tTEXT",
        (_row(*(row.get(key) for key in fields)) for row in preview.get("candidates") or []),
    )
    if preview.get("index_scope") == "raw_cues":
        return "Cue IDs refer to the raw transcript.\n" + table
    return table


def render_voiceprint_person(person: dict[str, Any]) -> str:
    return "\n".join(_row(label, person.get(key)) for label, key in (
        ("Name", "name"), ("ID", "id"), ("Samples", "sample_count"),
        ("Notes", "notes"), ("Created", "created_at"),
    ))


def render_voiceprint_samples(samples: list[dict[str, Any]]) -> str:
    keys = (
        "sample_id", "duration_sec", "quality_score", "task_id", "created_at", "audio_clip_path",
    )
    return _table("SAMPLE ID\tSECONDS\tQUALITY\tTASK ID\tCREATED\tCLIP", (
        _row(*(sample.get(key) for key in keys)) for sample in samples
    ))
