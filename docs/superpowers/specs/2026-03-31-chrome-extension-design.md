# MPP Chrome Extension — Video Subtitle Summarizer

## Goal

A Chrome extension that extracts subtitles from Bilibili and YouTube video pages, runs LLM analysis (summarize + outline) entirely client-side with the user's own API key, and exports structured results to a local directory compatible with MPP's archive format.

## Architecture

Chrome Extension (Manifest V3) with three components:

1. **Content Scripts** — per-platform scripts injected into Bilibili/YouTube pages. Automatically extract subtitles and video metadata on page load. Lightweight, no LLM logic.
2. **Side Panel** — React SPA displayed in Chrome's native side panel. Hosts all UI, LLM calls, caching, and file export. No timeout limits (unlike Service Worker).
3. **Background Service Worker** — minimal, handles message routing between content scripts and Side Panel, manages extension lifecycle.

```
Content Script (per tab)
  ├─ Detects video page, auto-extracts subtitles + metadata
  └─ Pushes data to Side Panel via chrome.runtime.message

Side Panel (React SPA)
  ├─ Displays video info, subtitles, summary, outline
  ├─ Calls LLM API directly (no CORS restrictions in extension context)
  ├─ File System Access API for local export
  └─ chrome.storage.local for result caching

Background Service Worker
  └─ Message routing + lifecycle management
```

## Tech Stack

- **Build**: Vite + CRXJS vite-plugin (Manifest V3 HMR dev experience)
- **UI**: React 18 + Tailwind CSS
- **Language**: TypeScript
- **Package Manager**: pnpm

## Project Structure

```
extension/
├─ manifest.json
├─ background.ts
├─ content/
│   ├─ bilibili.ts          # Bilibili subtitle/metadata extraction
│   └─ youtube.ts           # YouTube subtitle/metadata extraction
├─ sidepanel/
│   ├─ index.html
│   ├─ App.tsx
│   ├─ components/
│   │   ├─ VideoInfo.tsx     # Title, thumbnail, uploader, duration
│   │   ├─ SubtitleView.tsx  # Raw subtitle text (collapsible)
│   │   ├─ SummaryView.tsx   # tldr + key facts
│   │   ├─ OutlineView.tsx   # 3-level markdown outline (TOC style)
│   │   ├─ SettingsView.tsx  # LLM provider/key/model config
│   │   └─ ExportButton.tsx  # File System Access export
│   ├─ lib/
│   │   ├─ llm.ts           # LLM call wrapper (OpenAI-compatible)
│   │   ├─ prompts.ts       # Prompt templates (analyze, summarize, outline)
│   │   ├─ storage.ts       # chrome.storage.local cache read/write
│   │   └─ export.ts        # File System Access + MPP directory structure
│   └─ styles/
│       └─ index.css         # Tailwind
├─ package.json
├─ tsconfig.json
├─ vite.config.ts
└─ tailwind.config.ts
```

## Supported Platforms

### Bilibili

**Metadata extraction**: Extract `bvid` from URL, call `GET /x/web-interface/view?bvid={bvid}` (no auth needed) → title, desc, owner.name, duration, pic, pages[].cid. Pure fetch, no MAIN world injection needed.

**Subtitle extraction**:
1. Call `/x/player/v2?cid={cid}&bvid={bvid}` with `credentials: "include"` (uses session cookie)
2. Response `data.subtitle.subtitles[]` contains available tracks
3. Priority: AI subtitles (`lan` starts with `ai-`) > user-uploaded subtitles
4. Fetch `subtitle_url` → BCC JSON format: `body[].{from, to, content}` (float seconds)

**Requirements**: User must be logged in to Bilibili for AI subtitles. Extension needs `host_permissions` for `*.bilibili.com` and `*.hdslb.com`.

### YouTube

**Metadata extraction**: POST to `/youtubei/v1/player` with InnerTube client context + videoId. Response contains `videoDetails` (title, description, channelId, author, lengthSeconds, thumbnail).

**Subtitle extraction**:
1. Fetch watch page HTML → extract `INNERTUBE_CLIENT_NAME` and `INNERTUBE_CLIENT_VERSION`
2. POST `/youtubei/v1/player` → `captions.playerCaptionsTracklistRenderer.captionTracks[]`
3. Priority: manual subtitles (`kind !== "asr"`) > auto-generated (`kind === "asr"`)
4. Fetch `baseUrl + "&fmt=json3"` → JSON3 format: `events[].{tStartMs, dDurationMs, segs[].utf8}`

**Requirements**: No authentication needed. Extension needs `host_permissions` for `*.youtube.com`.

### Unified Data Format

Content scripts normalize platform-specific data into a common format:

```typescript
interface VideoData {
  platform: "bilibili" | "youtube"
  videoId: string
  title: string
  description: string
  uploader: string
  duration: number           // seconds
  thumbnailUrl: string
  subtitles: SubtitleEntry[]
  rawSubtitleLang: string
}

interface SubtitleEntry {
  start: number   // seconds (float)
  end: number     // seconds (float)
  text: string
}
```

## LLM Integration

### Settings

Stored in `chrome.storage.local`:

```typescript
interface LLMSettings {
  provider: "anthropic" | "openai" | "deepseek" | "custom"
  apiKey: string
  apiBase?: string      // custom endpoint URL
  model: string         // e.g. "claude-sonnet-4-20250514", "deepseek-chat"
  temperature: number   // default 0.1
}
```

Single key mode: one provider + key for all steps.

### Analysis Pipeline

Three serial steps, each updates UI immediately on completion:

1. **Analyze** — `analyze(subtitleText, metadata)` → `{language, content_type, keywords[], proper_nouns[]}`
   - Input: first 8000 chars of subtitle text + video metadata (title, description, uploader)
   - Purpose: extract context for downstream steps

2. **Summarize** — `summarize(subtitleText, analysisContext)` → `{tldr, key_facts[]}`
   - Input: full subtitle text + analysis output as context
   - Output: one-sentence tldr + bullet-point key facts

3. **Outline** — `outline(subtitleText, metadata)` → markdown string
   - Input: full subtitle text + metadata
   - Output: 3-level markdown bullet list (TOC style)

No polish step — platform subtitles are good enough quality.

### Error Handling

- Exponential backoff retry: delays 1s, 2s, 4s, max 3 attempts
- Retries on timeout and network errors only (not 4xx)
- UI shows error state with retry button per step

### Prompt Templates

Reuse MPP backend prompt patterns, adapted for pure text input (no SRT timestamps needed for analysis/summary). Prompts defined in `prompts.ts` as template functions.

## Caching

- **Key**: `cache:{platform}:{videoId}` in `chrome.storage.local`
- **Value**: `{ videoData, analysis, summary, outline, timestamp }`
- **Hit**: on "Analyze" click, check cache first; if fresh, display immediately
- **Eviction**: LRU-style, keep last 100 entries, drop oldest when exceeding limit
- **Manual**: user can force re-analyze to bypass cache

## Side Panel UI

Width ~400px, vertical scroll layout:

```
┌─────────────────────────┐
│ [Thumbnail]  Title       │  VideoInfo
│ Uploader · 12:34         │
├─────────────────────────┤
│ [▶ Analyze] [⬇ Export] [⚙]│  Action bar
├─────────────────────────┤
│ ▸ Summary                │  Collapsible, auto-expands on completion
│   One-line tldr...       │
│   • Key fact 1           │
│   • Key fact 2           │
├─────────────────────────┤
│ ▸ Outline                │  Collapsible, TOC style
│   1. Level 1 heading     │
│     1.1 Level 2          │
│       1.1.1 Level 3      │
├─────────────────────────┤
│ ▸ Subtitles              │  Collapsible, collapsed by default
│   00:01 First line...    │
│   00:05 Second line...   │
└─────────────────────────┘
```

**States**:
- **No video**: "Navigate to a Bilibili or YouTube video"
- **Video detected, no subtitles**: "No subtitles available for this video"
- **Subtitles ready**: VideoInfo shown, Analyze button enabled
- **Analyzing**: progress indicator per step, sections expand as each completes
- **Cached result**: all sections populated immediately
- **Settings**: gear icon opens LLM config panel (provider, key, model, temperature)

## Export

### Trigger

User clicks Export button. First time: File System Access API prompts directory picker. Handle persisted in IndexedDB for subsequent exports (restored via `handle.requestPermission()`).

### Output Structure

Written to `{selected_dir}/{safe_title}/`:

```
{safe_title}/
├─ metadata.json        # {title, source_url, platform, uploader, duration_seconds, ...}
├─ transcript.srt       # Subtitles converted to SRT format
├─ summary.md           # YAML frontmatter + tldr + key_facts
├─ outline.md           # 3-level markdown outline
└─ analysis.json        # {language, content_type, keywords, proper_nouns}
```

**MPP compatibility**: This structure matches MPP's `data/{title}/` archive format. `ArchiveService.list_archives()` will recognize these directories (has metadata.json or transcript.srt or summary.md).

### Safe Title

Strip characters `<>:"/\|?*`, truncate to 100 chars. Same logic as `ArchiveService._safe_name()`.

## Manifest

```json
{
  "manifest_version": 3,
  "name": "MPP Video Summarizer",
  "version": "0.1.0",
  "description": "Extract subtitles and generate AI summaries for Bilibili and YouTube videos",
  "permissions": ["storage", "sidePanel", "activeTab"],
  "host_permissions": [
    "https://*.bilibili.com/*",
    "https://*.hdslb.com/*",
    "https://*.youtube.com/*"
  ],
  "background": {
    "service_worker": "background.ts"
  },
  "content_scripts": [
    {
      "matches": ["*://*.bilibili.com/video/*"],
      "js": ["content/bilibili.ts"],
      "run_at": "document_idle"
    },
    {
      "matches": ["*://*.youtube.com/watch*"],
      "js": ["content/youtube.ts"],
      "run_at": "document_idle"
    }
  ],
  "side_panel": {
    "default_path": "sidepanel/index.html"
  },
  "action": {
    "default_title": "Open MPP Summarizer"
  }
}
```

## SPA Navigation Handling

Both platforms are SPAs — video changes don't trigger full page reloads.

- **Bilibili**: Monitor URL changes via `MutationObserver` on `document`, re-extract on `bvid` change
- **YouTube**: Same approach, re-extract on `v=` parameter change
- Content script sends `VIDEO_CHANGED` message to Side Panel on each navigation

## Scope Exclusions

- No video download functionality (handled by MPP backend)
- No ASR / speech recognition (this extension is for videos with existing subtitles)
- No subtitle polishing step
- No multi-key LLM routing (single provider for all steps)
- No cross-device sync (chrome.storage.local only, not sync)
- No translation features
