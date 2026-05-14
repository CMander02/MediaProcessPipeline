# Task Lifecycle Commands

## `run`

Use `run` for the default interactive workflow:

```bash
mpp run <source>
mpp run ./media/demo.mp4 --speakers 2 --hotwords "OpenAI,Qwen" --force-asr
```

`run` does all of the following:

1. Ensure the daemon is available, auto-starting it if needed.
2. Submit the task.
3. Attach to live SSE progress.
4. Print the final output directory on success.

Options:

- `--no-sep`: skip vocal separation.
- `--speakers` or `-s`: set the speaker count explicitly.
- `--hotwords` or `-w`: pass a comma-separated hotword list.
- `--force-asr`: ignore platform subtitles and force ASR.
- `--quiet` or `-q`: suppress rich progress and print only the final output path.

If the user presses `Ctrl+C`, the CLI detaches and leaves the task running in the background.

## `submit`

Use `submit` for scripting:

```bash
TASK_ID=$(mpp submit https://example.com/video)
mpp attach "$TASK_ID"
```

Behavior:

- Submit the task and return immediately.
- Print the full task ID to stdout.
- Write a short queued status line to stderr.
- Reuse the same processing options as `run`.

When `--json` is active, print a small JSON object instead of a bare ID.

## `attach`

Use `attach` to rejoin an existing task:

```bash
mpp attach @last
mpp attach 2f0f6f0f
```

Behavior:

- Resolve full IDs, prefixes, and `@last` / `@fail` / `@run`.
- Show the final result immediately if the task is already completed, failed, or cancelled.
- Stream task events over SSE while the task is still active.
- Honor `--quiet` to suppress the live UI and print only the final result.

## `retry`

Use `retry` to resubmit a failed task with the original source and options:

```bash
mpp retry @fail
mpp retry 2f0f6f0f --quiet
```

Behavior:

- Read the original task.
- Recreate a new task with the same `source` and `options`.
- Attach to the new task immediately.

Use `retry` only when the user wants a fresh task. It does not mutate the original failed task.
