"""Root support for the mpp CLI."""

from __future__ import annotations

import typer


def _emit_json_compat(data, *, legacy_json: bool = False) -> None:
    """Emit through the shared envelope for legacy command-local --json flags."""
    from app.cli.context import configure_cli_context, get_cli_context
    from app.cli.output import emit

    previous_mode = get_cli_context().output_mode
    if legacy_json and previous_mode == "text":
        configure_cli_context(output_mode="json")
    try:
        emit(data)
    finally:
        if legacy_json and previous_mode == "text":
            configure_cli_context(output_mode=previous_mode)


def _get_client() -> "MppClient":  # noqa: F821
    from app.cli.client import MppClient

    return MppClient()


def _require_daemon(client=None, auto_start: bool = False) -> "MppClient":  # noqa: F821
    """Return a connected client, auto-starting the daemon in background if needed."""
    if client is None:
        client = _get_client()
    if client.ping():
        return client

    if not auto_start:
        from app.cli.context import get_cli_context
        from app.cli.output import emit_error

        emit_error(
            "daemon_unavailable",
            f"Cannot reach {get_cli_context().server_url}. Run mpp server start for localhost.",
            retryable=True,
            exit_code=3,
        )

    from app.cli.context import get_cli_context
    from app.cli.daemon import start_daemon
    from app.cli.output import emit_error

    cli_context = get_cli_context()
    if not cli_context.is_local:
        emit_error(
            "daemon_unavailable",
            f"Cannot reach remote daemon {cli_context.server_url}.",
            retryable=True,
            exit_code=3,
        )
    if cli_context.output_mode == "text":
        typer.echo("starting daemon", err=True)
    try:
        result = start_daemon(
            cli_context.server_url,
            api_token=cli_context.api_token,
            timeout=max(cli_context.timeout, 20.0),
        )
    except RuntimeError as exc:
        emit_error("daemon_start_failed", str(exc), retryable=True, exit_code=3)
    if cli_context.output_mode == "text":
        typer.echo(f"daemon started\t{result['server']}", err=True)
    return client


def _resolve_ref(ref: str, client=None) -> str:
    """Resolve @last / @fail / @run to a real task ID.

    Falls back to SQLite offline read when daemon is not reachable.
    Prefix-match for plain hex IDs.
    """
    if client is not None and client.ping():
        from app.cli.commands.common import resolve_task_ref

        return resolve_task_ref(ref, client)

    if not ref.startswith("@"):
        # Plain ID or prefix — resolve via list
        return _resolve_prefix(ref, client)

    keyword = ref.lstrip("@").lower()
    status_map = {
        "last": None,
        "fail": ["failed"],
        "run": ["processing"],
        "queued": ["queued"],
        "paused": ["paused"],
        "completed": ["completed"],
        "active": ["pending", "queued", "processing", "paused"],
    }
    if keyword not in status_map:
        from app.cli.output import emit_error

        emit_error(
            "invalid_task_ref",
            f"Unknown task reference: {ref}",
            detail={"supported": [f"@{name}" for name in status_map]},
            exit_code=2,
        )

    # Try daemon first, fall back to SQLite
    tasks = _list_tasks_any(limit=10000, client=client)
    statuses = status_map[keyword]
    if statuses:
        tasks = [task for task in tasks if task.get("status") in statuses]
    if not tasks:
        from app.cli.output import emit_error

        emit_error("task_not_found", f"No task matches {ref}.", exit_code=4)
    return tasks[0]["id"]


def _resolve_prefix(prefix: str, client=None) -> str:
    """Resolve a task ID prefix to a full ID."""
    tasks = _list_tasks_any(limit=10000, client=client)
    matches = [t for t in tasks if t["id"].startswith(prefix)]
    if not matches:
        from app.cli.output import emit_error

        emit_error("task_not_found", f"No task ID starts with {prefix!r}.", exit_code=4)
    if len(matches) > 1:
        from app.cli.output import emit_error

        emit_error(
            "ambiguous_task_ref",
            f"Task prefix {prefix!r} matches {len(matches)} tasks.",
            detail=[
                {"id": item["id"], "status": item.get("status"), "source": item.get("source")}
                for item in matches[:20]
            ],
            exit_code=4,
        )
    return matches[0]["id"]


def _list_tasks_any(
    status_filter: str | None = None,
    limit: int = 50,
    client=None,
) -> list[dict]:
    """List tasks from daemon if available, else from SQLite."""
    if client is None:
        client = _get_client()
    if client.ping():
        return client.list_tasks(status=status_filter, limit=limit)
    from app.cli.context import get_cli_context

    if not get_cli_context().is_local:
        return []
    # Offline fallback
    try:
        from app.core.database import get_task_store, init_db

        init_db()
        store = get_task_store()
        items = store.list(status=status_filter, limit=limit)
        return [_task_to_dict(t) for t in items]
    except Exception:
        return []


def _task_to_dict(task) -> dict:
    """Convert Task model to plain dict for display."""
    return task.model_dump(mode="json")
