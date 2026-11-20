"""Virtual library folders and preview-driven content operations."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Optional
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

import typer

from app.cli.client import MppClient, MppClientError
from app.cli.commands.archive import _archive_text
from app.cli.commands.common import api_call, client, resolve_archive_ref, resolve_task_ref
from app.cli.commands.read import ArtifactReader, local_archive_index
from app.cli.context import get_cli_context
from app.cli.output import confirm_action, emit, emit_error, print_debug_exception
from app.cli.renderers import _row

folder_app = typer.Typer(help="管理虚拟文件夹与批量处理归档", no_args_is_help=True)

_BATCH_ACTIONS = {
    "retry": "retry",
    "checkpoint-rerun": "checkpoint_rerun",
    "full-rerun": "full_rerun",
    "slim": "retention",
    "export": "export",
    "delete": "delete",
}


def resolve_folder_ref(ref: str, directory_data: dict) -> dict:
    folders = directory_data["folders"]
    for folder in folders:
        if folder["id"] == ref:
            return folder
    matches = (
        [folder for folder in folders if folder["name"] == ref]
        or [folder for folder in folders if folder["name"].casefold() == ref.casefold()]
        or [folder for folder in folders if ref and folder["id"].startswith(ref)]
    )
    if len(matches) == 1:
        return matches[0]
    if matches:
        emit_error(
            "ambiguous_folder_ref",
            f"Folder reference {ref!r} matches {len(matches)} folders.",
            detail=[{key: folder[key] for key in ("id", "name", "count")} for folder in matches],
            exit_code=4,
        )
    emit_error("folder_not_found", f"No folder matches {ref!r}.", exit_code=4)


def resolve_content_keys(refs: list[str], api: MppClient, directory_data: dict) -> list[str]:
    archive_keys = directory_data["archive_keys"]

    def archive_content_key(archive: dict) -> str | None:
        return archive_keys.get(archive.get("archive_id")) or archive.get("content_key")

    keys: dict[str, None] = {}
    for ref in dict.fromkeys(ref.strip() for ref in refs):
        if ref in directory_data["members"] or ref in directory_data["processed_at"]:
            keys[ref] = None
            continue
        key = archive_keys.get(ref)
        if key is None:
            archive = resolve_archive_ref(
                ref, api, missing_ok=True,
                group_key=lambda item: archive_content_key(item) or item["path"],
            )
            if archive is not None:
                key = archive_content_key(archive)
        if not key:
            key = api.task_library_content(resolve_task_ref(ref, api))["content_key"]
        keys[key] = None
    return list(keys)


def _collection(folder: dict) -> str | None:
    collection = folder.get("collection") or {}
    if collection.get("owner_name") and collection.get("title"):
        return f"{collection['owner_name']}: {collection['title']}"
    return folder.get("source_collection_key")


def _local_directory() -> dict:
    from app.core.database import init_db
    from app.core.library import get_library_service

    init_db()
    return get_library_service().directory()


@folder_app.command("list")
def list_folders():
    """列出资料库中的文件夹。"""
    api = client()
    offline = get_cli_context().is_local and not api.ping()
    data = api_call(_local_directory if offline else api.list_library_folders)

    def render() -> str:
        rows = [
            f"folders={len(data['folders'])}  unclassified={data['unclassified_count']}  "
            f"revision={data['library_revision']}",
            "ID\tCOUNT\tCOLLECTION\tUPDATED\tNAME",
        ]
        rows.extend(
            _row(
                folder["id"],
                folder["count"],
                _collection(folder),
                folder["updated_at"],
                folder["name"],
            )
            for folder in data["folders"]
        )
        return "\n".join(rows)

    emit(data, text=render)


@folder_app.command("show")
def show_folder(
    folder_ref: Optional[str] = typer.Argument(None),
    limit: int = typer.Option(50, "--limit", "-n", min=1, max=500),
    page: int = typer.Option(1, "--page", min=1),
    search: str = typer.Option("", "--search", "-q"),
    sort: str = typer.Option("processed_desc", "--sort"),
    unclassified: bool = typer.Option(False, "--unclassified"),
):
    """分页列出文件夹或未分类的归档。"""
    unclassified = unclassified or folder_ref == "unclassified"
    if not (folder_ref or unclassified):
        emit_error("invalid_options", "Pass a folder reference or --unclassified.", exit_code=2)
    if sort not in {"processed_desc", "created_desc", "created_asc", "published_desc", "title_asc"}:
        emit_error("invalid_sort", f"Unknown archive sort {sort!r}.", exit_code=2)
    api = client()
    offline = get_cli_context().is_local and not api.ping()
    list_page = api_call(local_archive_index).list_page if offline else api.list_archives_paged
    directory = api_call(_local_directory if offline else api.list_library_folders)
    folder = None if unclassified else resolve_folder_ref(folder_ref, directory)
    data = api_call(
        lambda: list_page(
            page=page,
            page_size=limit,
            search=search,
            sort=sort,
            folder_id=folder["id"] if folder else None,
            unclassified=unclassified,
        )
    )

    def render() -> str:
        if folder:
            header = _row(
                "folder",
                folder["name"],
                folder["id"],
                f"count={folder['count']}",
                _collection(folder),
                folder["updated_at"],
            )
        else:
            header = f"unclassified  count={directory['unclassified_count']}"
        return "\n".join(
            [
                header,
                f"page={data['page']}  limit={data['page_size']}  total={data['total']}",
                "ID\tCREATED\tMEDIA\tSOURCE\tTITLE\tPATH",
                *map(_archive_text, data["archives"]),
            ]
        )

    emit({**data, "folder": folder, "unclassified": unclassified}, text=render)


@folder_app.command("create")
def create_folder(name: str = typer.Argument(...)):
    """创建虚拟文件夹。"""
    api = client()
    folder = api_call(lambda: api.create_library_folder(name))
    emit(folder, text=_row("Created folder", folder["id"], folder["name"]))


@folder_app.command("rename")
def rename_folder(folder_ref: str = typer.Argument(...), new_name: str = typer.Argument(...)):
    """修改虚拟文件夹名称。"""
    api = client()
    folder = resolve_folder_ref(folder_ref, api_call(api.list_library_folders))
    renamed = api_call(lambda: api.rename_library_folder(folder["id"], new_name))
    emit(renamed, text=_row("Renamed folder", renamed["id"], renamed["name"]))


@folder_app.command("delete")
def delete_folder(
    folder_ref: str = typer.Argument(...),
    yes: bool = typer.Option(False, "--yes"),
):
    """删除文件夹并将内容移至未分类，保留归档。"""
    api = client()
    folder = resolve_folder_ref(folder_ref, api_call(api.list_library_folders))
    confirm_action(
        f"Delete folder {folder['name']!r}? Items move to unclassified. Archives are kept.",
        explicit_yes=yes,
    )
    result = api_call(lambda: api.delete_library_folder(folder["id"]))
    emit(
        result,
        text=(
            f"Deleted folder {folder['name']!r}. "
            f"Moved {result['released']} items to unclassified. Archives were kept."
        ),
    )


@folder_app.command("move")
def move_members(
    refs: list[str] = typer.Argument(...),
    to: Optional[str] = typer.Option(None, "--to"),
    unclassified: bool = typer.Option(False, "--unclassified"),
):
    """将内容移入指定文件夹或移至未分类。"""
    if (to is not None) == unclassified:
        emit_error("invalid_options", "Choose exactly one of --to or --unclassified.", exit_code=2)
    api = client()
    directory = api_call(api.list_library_folders)
    folder = resolve_folder_ref(to, directory) if to is not None else None
    keys = api_call(lambda: resolve_content_keys(refs, api, directory))
    result = api_call(lambda: api.move_library_members(keys, folder["id"] if folder else None))
    emit(
        result,
        text=lambda: "\n".join(
            [
                _row("Destination", folder["name"] if folder else "unclassified"),
                "CONTENT_KEY\tOUTCOME\tREASON",
                *(
                    _row(item["content_key"], item["outcome"], item.get("reason"))
                    for item in result["items"]
                ),
            ]
        ),
    )
    if any(item["outcome"] in {"failed", "skipped"} for item in result["items"]):
        raise typer.Exit(5)


def _render_preview(preview: dict) -> str:
    skipped = preview["skipped_items"]
    rows = [
        f"operation_id={preview['operation_id']}  runnable={preview['runnable']}  "
        f"skipped={len(skipped)}",
        "CONTENT_KEY\tSTATUS\tRECORDS\tTITLE\tSKIPPED_REASON",
    ]
    rows.extend(
        _row(
            item["content_key"],
            item.get("status"),
            item.get("records"),
            item.get("title"),
            item.get("reason"),
        )
        for item in [*preview["targets"], *skipped]
    )
    return "\n".join(rows)


def _export_target(api: MppClient, target: dict, output_dir: Path) -> dict:
    archive = target["archive"]
    destination = output_dir / f"{UUID(archive['archive_id'])}.zip"
    output_dir.mkdir(parents=True, exist_ok=True)
    task_id = target["task_id"]
    task_path = None
    if task_id:
        result = api.get_task(task_id).get("result") or {}
        task_path = result.get("output_dir") or (result.get("archive") or {}).get("output_dir")
    historical = task_id is not None and archive["path"] != task_path
    with TemporaryDirectory(dir=output_dir, prefix=".mpp-export-") as directory:
        staged = Path(directory) / destination.name
        if task_id and not historical and target["status"] == "completed":
            api.export_task_archive(task_id, staged, include_media=False)
        else:
            reader = ArtifactReader(api, archive)
            with ZipFile(staged, "w", ZIP_DEFLATED) as bundle:
                for variants in (
                    ("summary.md", "summary.json"),
                    ("transcript_polished.srt", "transcript.srt", "transcript_polished.md"),
                ):
                    for name in variants:
                        if historical:
                            # The task's SQLite mirror can belong to a different archive.
                            try:
                                content = api.sync_file(archive["archive_id"], name)
                            except MppClientError as exc:
                                if exc.status_code != 404:
                                    raise
                                continue
                        else:
                            content = reader.read(name)
                        if content:
                            bundle.writestr(name, content)
                            break
                if not bundle.namelist():
                    raise ValueError(
                        f"No summary or transcript is available for {archive['path']}."
                    )
        size = staged.stat().st_size
        staged.replace(destination)
    return {"output": str(destination), "size": size, "format": "zip"}


def _apply_target(api: MppClient, action: str, target: dict, policy: str, output_dir: Path) -> dict:
    if action == "delete":
        return api.delete_library_content(
            {
                "operation_id": target["operation_id"],
                "content_key": target["content_key"],
                "folder_id": target.get("folder_id"),
                "task_ids": target["task_ids"],
                "archive_ids": target["archive_ids"],
                "records": target["records"],
            }
        )
    if action == "retention":
        path = target["archive"]["path"]
        preview = api.media_retention(path, policy)
        files = [item["path"] for item in preview["entries"] if item["delete"]]
        if files:
            return api.media_retention(path, preview["policy"], files)
        return {**preview, "cleaned": [], "reclaimed_bytes": 0, "errors": []}
    if action == "export":
        return _export_target(api, target, output_dir)
    if action == "full_rerun":
        return api.full_rerun_task(target["task_id"])
    if action == "retry" and target["status"] == "paused":
        return api.resume_task(target["task_id"])
    return api.checkpoint_rerun(target["task_id"])


@folder_app.command("batch")
def batch(
    action: str = typer.Argument(..., help=" | ".join(_BATCH_ACTIONS)),
    refs: Optional[list[str]] = typer.Argument(None),
    folder: Optional[str] = typer.Option(None, "--folder"),
    policy: str = typer.Option("text", "--policy", help="text / playback / all"),
    output_dir: Path = typer.Option(Path("exports"), "--output-dir", "-o"),
    apply: bool = typer.Option(False, "--apply"),
    yes: bool = typer.Option(False, "--yes"),
):
    """预览或执行以内容为单位的批量操作。"""
    if (folder is not None) == bool(refs):
        emit_error("invalid_options", "Pass either --folder or content references.", exit_code=2)
    if action not in _BATCH_ACTIONS:
        emit_error("invalid_action", f"Choose one of {', '.join(_BATCH_ACTIONS)}.", exit_code=2)
    if policy not in {"text", "playback", "all"}:
        emit_error("invalid_policy", "Choose text, playback, or all.", exit_code=2)
    api = client()
    directory = api_call(api.list_library_folders)
    api_action = _BATCH_ACTIONS[action]
    if folder is not None:
        scope = {"type": "folder", "folder_id": resolve_folder_ref(folder, directory)["id"]}
    else:
        keys = api_call(lambda: resolve_content_keys(refs, api, directory))
        scope = {"type": "selected", "content_keys": keys}
    preview = api_call(lambda: api.preview_library_batch(api_action, scope))
    if not apply:
        emit(preview, text=lambda: _render_preview(preview))
        return

    ctx = get_cli_context()
    if ctx.output_mode == "text" and ctx.interactive and not (yes or ctx.assume_yes):
        typer.echo(_render_preview(preview), err=True)
    prompt = f"Apply {action} to {preview['runnable']} contents?"
    if action == "delete":
        prompt += (
            " This deletes all task records and archive directories for the selected contents."
        )
    confirm_action(prompt, explicit_yes=yes)

    applied: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for target in preview["targets"]:
        key = target["content_key"]
        try:
            result = _apply_target(api, api_action, target, policy, output_dir)
        except MppClientError as exc:
            print_debug_exception(exc)
            errors.append(
                {"content_key": key, "code": exc.code, "error": exc.message, "detail": exc.detail}
            )
        except (OSError, ValueError) as exc:
            print_debug_exception(exc)
            errors.append({"content_key": key, "code": "local_error", "error": str(exc)})
        else:
            if (
                result.get("errors")
                or result.get("success") is False
                or result.get("outcome") in {"partial", "failed", "skipped"}
                or (
                    api_action == "retention" and policy != "all" and result.get("protected_reason")
                )
            ):
                errors.append({"content_key": key, "result": result})
            else:
                applied.append({**result, "content_key": key})
    emit({"preview": preview, "applied": applied, "errors": errors})
    if errors:
        raise typer.Exit(5)
