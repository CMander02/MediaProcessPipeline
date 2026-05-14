# Inspection And Control Commands

## `tasks`

Use `tasks` to inspect the queue and recent history:

```bash
mpp tasks
mpp tasks --watch
mpp tasks --status failed --limit 50
mpp tasks --all --json
```

Behavior:

- Without `--status`, merge active tasks, queued tasks, and recent history.
- With `--watch`, keep refreshing the table using the global SSE stream.
- With `--json`, print structured task data.
- If the daemon is offline, try reading the local SQLite task store instead.

## `show`

Use `show` for task details or file content:

```bash
mpp show @last
mpp show 2f0f6f0f --json
mpp show @last --summary
mpp show @last --transcript
```

Behavior:

- Resolve full IDs, prefixes, and `@last` / `@fail` / `@run`.
- Print task metadata, steps, options, and output paths by default.
- Print the summary markdown file with `--summary`.
- Print the transcript or subtitle file with `--transcript`.
- Fall back to offline SQLite reads when the daemon is down.

`show --summary` and `show --transcript` read files from the task output directory. If the task has no output directory or the expected files are missing, the command exits with an error.

## `open`

Use `open` to reveal a finished task in the OS file manager:

```bash
mpp open @last
```

Behavior:

- Require a live daemon.
- Resolve the task reference.
- Open the output directory with `explorer`, `open`, or `xdg-open` depending on platform.

Use `open` only when the task already has an output directory.

## `cancel`

Use `cancel` to stop a queued or running task:

```bash
mpp cancel @run
mpp cancel 2f0f6f0f
```

Behavior:

- Require a live daemon.
- Resolve the task reference.
- Send `POST /api/tasks/{id}/cancel`.

## `ping`

Use `ping` for a direct daemon health check:

```bash
mpp ping
```

Behavior:

- Return success when `GET /health` responds with HTTP 200.
- Exit non-zero when the daemon is not reachable.
