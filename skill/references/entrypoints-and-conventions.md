# Entrypoints And Conventions

## Entrypoints

Use any of these equivalent front doors:

```bash
mpp --help
./scripts/mpp --help
cd backend && uv run python -m app.cli --help
```

On Windows PowerShell, use:

```powershell
.\scripts\mpp.ps1 --help
```

The wrapper scripts do not add logic. They only `cd` into `backend` and run `uv run python -m app.cli`.

## Global Flags

Apply these before the subcommand:

```bash
mpp --plain tasks
mpp --no-color run video.mp4
mpp --json show @last
```

- `--plain`: use plain text, no color, and ASCII-safe symbols.
- `--no-color`: keep structured output but remove color.
- `--json`: print machine-readable JSON to stdout when the command supports structured output.

On Windows, the bootstrap in `backend/app/cli/__main__.py` attempts to force UTF-8. If stdout still cannot represent Unicode, it enables plain output automatically via `MPP_PLAIN_OUTPUT=1`.

## Task Reference Syntax

Several commands accept a task reference instead of a full UUID:

- Full task ID: `mpp show 2f0f6f0f-...`
- Prefix: `mpp attach 2f0f6f0f`
- `@last`: most recent task
- `@fail`: most recent failed task
- `@run`: most recent processing task

Prefix resolution is optimistic. If multiple IDs share the same prefix, the CLI warns and picks the most recent match.

## Behavioral Conventions

- The default daemon URL is `http://127.0.0.1:18000`.
- `run`, `submit`, `attach`, and `retry` auto-start the daemon when it is not reachable.
- `tasks` and `show` try the daemon first, then fall back to local SQLite reads if the daemon is offline.
- `ping`, `cancel`, and `open` require a live daemon.
- The canonical command set is:
  - `serve`, `ping`, `run`, `submit`, `attach`, `retry`
  - `tasks`, `show`, `open`, `cancel`, `doctor`
  - `config list|get|set`

Do not suggest legacy `status` or `list` top-level commands. The actual CLI surface uses `tasks`.
