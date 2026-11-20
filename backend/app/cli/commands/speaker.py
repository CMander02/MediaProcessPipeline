"""Text-first inspection and editing of task speaker labels."""

from __future__ import annotations

import re
from typing import Annotated, Any
from uuid import UUID

import typer

from app.cli.client import MppClient
from app.cli.commands.common import api_call, client
from app.cli.commands.read import TRANSCRIPT_FILES, ArtifactReader, task_archive_reader
from app.cli.context import get_cli_context
from app.cli.output import confirm_action, emit, emit_error, emit_event
from app.cli.renderers import (
    render_fields,
    render_pending_speaker_cues,
    render_speaker_dossier,
    render_speaker_repair_preview,
)

speaker_app = typer.Typer(help="查看人物依据与修改字幕归属", no_args_is_help=True)


def _target_id(ref: str, api: MppClient) -> str:
    reader = task_archive_reader(ref, api)
    assert reader.task_id is not None
    return reader.task_id


def _profile(ref: str) -> dict[str, Any]:
    api = client()
    connected = api if api.ping() else None
    reader = task_archive_reader(ref, connected)
    task_id = reader.task_id
    assert task_id is not None
    if connected:
        return api_call(lambda: connected.get_task_speakers(task_id))
    from app.services.analysis.speaker_cli_profile import build_task_speaker_profile

    try:
        return api_call(lambda: build_task_speaker_profile(UUID(task_id)))
    except LookupError as exc:
        emit_error("speaker_profile_not_found", str(exc), exit_code=4)


def _cue_indices(spec: str) -> list[int]:
    indices = set()
    for part in spec.split(","):
        match = re.fullmatch(r"([0-9]+)(?:\s*-\s*([0-9]+))?", part.strip())
        if not match:
            emit_error(
                "invalid_cue_spec",
                "Use cue IDs and ascending ranges, e.g. 12,15,20-22.",
                exit_code=2,
            )
        start, end = int(match[1]), int(match[2] or match[1])
        if end < start:
            emit_error("invalid_cue_spec", f"Cue range {part!r} must be ascending.", exit_code=2)
        indices.update(range(start, end + 1))
    return sorted(indices)


@speaker_app.command("inspect")
def speaker_inspect(ref: str, speaker_name: Annotated[str | None, typer.Argument()] = None):
    """查看字幕引文、画面文字和声纹候选。"""
    profile = _profile(ref)
    if speaker_name is not None:
        speakers = [
            row
            for row in profile["speakers"]
            if row["name"].casefold() == speaker_name.strip().casefold()
        ]
        if not speakers:
            emit_error("speaker_not_found", f"Speaker {speaker_name!r} was not found.", exit_code=4)
        profile = {**profile, "speakers": speakers}
    emit(profile, text=render_speaker_dossier(profile))


@speaker_app.command("list")
def speaker_list(ref: str):
    """列出任务中每位人物的依据。"""
    speaker_inspect(ref)


@speaker_app.command("pending")
def speaker_pending(ref: str):
    """查看待归属字幕、相邻对话和声音候选。"""
    profile = _profile(ref)
    emit(profile, text=render_pending_speaker_cues(profile["pending_cues"]))


@speaker_app.command("rename")
def rename_speaker(
    ref: str,
    old_name: str,
    new_name: str,
    on_conflict: str = typer.Option("ask", "--on-conflict", help="ask/merge/new"),
    regenerate: bool = typer.Option(False, "--regenerate"),
):
    """修改人物名称并同步声纹映射和产物。"""
    if on_conflict not in {"ask", "merge", "new"}:
        emit_error("invalid_conflict_mode", f"Unknown conflict mode: {on_conflict}", exit_code=2)
    api = client()
    task_id = _target_id(ref, api)
    result = api_call(lambda: api.rename_task_speaker(task_id, old_name, new_name, on_conflict))
    if (
        result.get("status") == "conflict"
        and on_conflict == "ask"
        and get_cli_context().interactive
    ):
        typer.echo(
            f"{result.get('conflict_person_name') or new_name} | "
            f"{result.get('conflict_sample_count', 0)} sample(s)",
            err=True,
        )
        choice = typer.prompt("Resolve name conflict (merge/new)", err=True).strip().lower()
        while choice not in {"merge", "new"}:
            choice = typer.prompt("Enter merge or new", err=True).strip().lower()
        result = api_call(lambda: api.rename_task_speaker(task_id, old_name, new_name, choice))
    if result.get("status") == "conflict":
        emit_error(
            "speaker_name_conflict",
            "The target speaker name already belongs to another voiceprint person.",
            detail=result,
            exit_code=4,
        )
    final_name = result.get("person_name") or new_name
    text = f"{result['status']}\t{old_name}\t{final_name}"
    if old_name != final_name:
        text += "\n[derived_content_stale] Summary and mind map need regeneration."
    if regenerate:
        rerun = api_call(lambda: api.checkpoint_rerun(task_id))
        result = {**result, "regeneration": rerun}
        text += "\nRegeneration queued."
    emit(result, text=text)


def _wait_for_repair(api: MppClient, task_id: str) -> dict:
    def wait_for_terminal():
        for event in api.stream_task_events(task_id):
            emit_event(event)
            event_type = event.get("type")
            data = event.get("data") or {}
            state = data.get("status") if event_type == "snapshot" else event_type
            if state in {"completed", "failed", "cancelled", "deleted", "paused"}:
                return state, data
            if get_cli_context().output_mode == "text" and data.get("message"):
                typer.echo(data["message"], err=True)
        return None, {}

    try:
        state, data = api_call(wait_for_terminal)
    except KeyboardInterrupt:
        raise typer.Exit(130)
    if state in {"failed", "cancelled", "deleted", "paused"}:
        emit_error(
            "speaker_repair_failed", data.get("error") or f"Speaker repair {state}.", detail=data
        )
    task = api_call(lambda: api.get_task(task_id))
    result = (task.get("result") or {}).get("speaker_repair") or {}
    if task.get("status") != "completed" or result.get("status") in {"failed", "cancelled"}:
        emit_error(
            "speaker_repair_failed",
            result.get("error") or task.get("error") or "Speaker repair did not complete.",
        )
    return result


def _repair(ref: str, mode: str, wait: bool) -> None:
    api = client()
    task_id = _target_id(ref, api)
    queued = api_call(lambda: api.repair_task_speakers(task_id, mode))
    if not wait:
        emit(queued, text=queued.get("message") or "Speaker repair queued.")
    elif mode == "auto":
        result = _wait_for_repair(api, task_id)
        emit(
            result or {"status": "completed"},
            text=render_fields(result) or "Speaker autofill completed.",
        )
    else:
        _wait_for_repair(api, task_id)
        preview = api_call(lambda: api.get_speaker_repair_preview(task_id))
        emit(preview, text=render_speaker_repair_preview(preview))


@speaker_app.command("autofill")
def speaker_autofill(ref: str, wait: bool = typer.Option(False, "--wait")):
    """自动补全待归属字幕和匿名人物姓名。"""
    _repair(ref, "auto", wait)


@speaker_app.command("repair")
def speaker_repair(
    ref: str,
    mode: str = typer.Option("all", "--mode", help="failed/all"),
    wait: bool = typer.Option(False, "--wait"),
):
    """运行人物复核并生成候选。"""
    if mode not in {"failed", "all"}:
        emit_error("invalid_repair_mode", "Choose --mode failed or all.", exit_code=2)
    _repair(ref, mode, wait)


@speaker_app.command("preview")
def speaker_preview(ref: str):
    """查看人物复核候选。"""
    api = client()
    task_id = _target_id(ref, api)
    preview = api_call(lambda: api.get_speaker_repair_preview(task_id))
    emit(preview, text=render_speaker_repair_preview(preview))


@speaker_app.command("assign")
def speaker_assign(
    ref: str,
    speaker_name: str,
    cue: str = typer.Option(..., "--cue"),
):
    """将选定字幕片段分配给一个人物。"""
    assignments = [{"index": index, "speaker": speaker_name} for index in _cue_indices(cue)]
    api = client()
    task_id = _target_id(ref, api)
    result = api_call(lambda: api.assign_task_speakers(task_id, {"assignments": assignments}))
    emit(result, text=render_fields(result))


@speaker_app.command("apply-preview")
def speaker_apply_preview(
    ref: str,
    cues: str | None = typer.Option(None, "--cues"),
    all_cues: bool = typer.Option(False, "--all"),
    yes: bool = typer.Option(False, "--yes"),
):
    """应用全部或指定的人物复核候选。"""
    if cues is not None and all_cues:
        emit_error("invalid_options", "Choose --cues or --all.", exit_code=2)
    indices = _cue_indices(cues) if cues is not None else None
    confirm_action(
        "Apply the selected speaker repair candidates?"
        if indices is not None
        else "Apply all speaker repair candidates?",
        explicit_yes=yes,
    )
    api = client()
    task_id = _target_id(ref, api)
    result = api_call(
        lambda: api.assign_task_speakers(
            task_id,
            {"apply_preview": True, "preview_indices": indices},
        )
    )
    emit(result, text=render_fields(result))


@speaker_app.command("confirm")
def speaker_confirm(ref: str):
    """确认当前字幕中的人物标签。"""
    api = client()
    reader = task_archive_reader(ref, api)
    task_id = reader.task_id
    assert task_id is not None
    if reader.task is None:
        reader = ArtifactReader.for_task(api, api_call(lambda: api.get_task(task_id)))
    _, srt = reader.required(*TRANSCRIPT_FILES, baseline=True)
    result = api_call(lambda: api.reconcile_task_speakers(task_id, srt))
    emit(result, text=render_fields(result))
