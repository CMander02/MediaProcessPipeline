# Configuration Commands

## Storage Model

Runtime settings are persisted to `data/settings.json` through `app.core.settings`.

Use:

```bash
mpp config
mpp config list
mpp config get openai_model
mpp config set data_root D:/Video/MediaProcessPipeline
```

Important distinction:

- `data/settings.json` stores runtime settings.
- Task data and `tasks.db` follow `settings.data_root`, not the repository `data/` directory.

## `config list`

Use `config list` to print settings:

```bash
mpp config list
mpp config list --group llm
mpp config list --group paths --json
```

Supported groups in the CLI:

- `llm`
- `asr`
- `diarization`
- `subtitle`
- `uvr`
- `paths`
- `security`
- `bilibili`
- `concurrency`

Notes:

- Bare `mpp config` behaves like `mpp config list`.
- `mpp config list` without `--group` shows every `RuntimeSettings` field.
- Grouped output is curated and does not cover every field. For example, some `deepseek_*`, YouTube, voiceprint, and overlap settings are not exposed through a named group even though they are valid keys.
- Secret fields are masked in human-readable output.

## `config get`

Use `config get` for a single key:

```bash
mpp config get llm_provider
mpp config get data_root --json
```

Behavior:

- Reject unknown keys and suggest close matches.
- Show the current value and, in text mode, show the default when the current value differs.

## `config set`

Use `config set` to persist one value:

```bash
mpp config set llm_provider openai
mpp config set openai_api_key sk-...
mpp config set qwen3_batch_size 16
mpp config set enable_diarization false
```

Type coercion rules:

- `true` and `false` become booleans.
- Integer-looking strings become integers.
- Float-looking strings become floats.
- Everything else stays a string.

Validation rules from `RuntimeSettings`:

- `asr_provider` must be `qwen3`.
- `bilibili_subtitle_engine` must be `native_wbi`.
- `bilibili_subtitle_min_coverage` must be between `0` and `1`.
- `data_root` must resolve to a path at least two directory levels deep.

## High-Value Keys

Use these keys most often:

- LLM basics: `llm_provider`, `anthropic_api_key`, `anthropic_model`, `openai_api_key`, `openai_model`, `custom_api_base`, `custom_model`
- Local LLM: `local_llm_model_path`, `local_llm_device`, `local_llm_dtype`, `polish_provider`
- ASR: `qwen3_asr_model_path`, `qwen3_aligner_model_path`, `qwen3_device`, `qwen3_batch_size`
- Diarization: `enable_diarization`, `hf_token`, `pyannote_model_path`
- Subtitles: `prefer_platform_subtitles`, `subtitle_languages`, `force_asr`
- Downloads: `youtube_cookies_file`, `youtube_cookies_browser`, `bilibili_sessdata`, `bilibili_bili_jct`, `bilibili_dede_user_id`
- Paths: `data_root`, `uvr_model_dir`

When the user is unsure which key to edit, start with `mpp config list --group <group>` and only then call `config get` or `config set`.
