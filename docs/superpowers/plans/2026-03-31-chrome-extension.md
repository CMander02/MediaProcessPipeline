# MPP Chrome Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Chrome extension that extracts subtitles from Bilibili/YouTube, runs LLM analysis in the Side Panel, and exports results to local directories compatible with MPP's archive format.

**Architecture:** Content scripts auto-extract subtitles + metadata from video pages, push to Side Panel via chrome.runtime messaging. Side Panel is a React SPA that hosts UI, LLM calls (no CORS in extension context), caching (chrome.storage.local), and file export (File System Access API). Background service worker does minimal message routing.

**Tech Stack:** Vite + CRXJS, React 18, Tailwind CSS, TypeScript, pnpm, Chrome Manifest V3

---

## File Map

```
extension/
├─ manifest.json                    # Task 1: Manifest V3 config
├─ src/
│   ├─ background.ts                # Task 2: Service worker — message routing
│   ├─ content/
│   │   ├─ types.ts                 # Task 3: Shared VideoData/SubtitleEntry types
│   │   ├─ bilibili.ts              # Task 4: Bilibili extraction
│   │   └─ youtube.ts               # Task 5: YouTube extraction
│   ├─ sidepanel/
│   │   ├─ index.html               # Task 6: Side Panel entry
│   │   ├─ index.tsx                # Task 6: React mount
│   │   ├─ App.tsx                  # Task 6: Root component + message listener
│   │   ├─ components/
│   │   │   ├─ VideoInfo.tsx        # Task 7: Video metadata display
│   │   │   ├─ ActionBar.tsx        # Task 7: Analyze/Export/Settings buttons
│   │   │   ├─ SummaryView.tsx      # Task 7: tldr + key facts
│   │   │   ├─ OutlineView.tsx      # Task 7: 3-level markdown TOC
│   │   │   ├─ SubtitleView.tsx     # Task 7: Raw subtitle list
│   │   │   └─ SettingsView.tsx     # Task 8: LLM config panel
│   │   └─ lib/
│   │       ├─ llm.ts              # Task 9: LLM call wrapper
│   │       ├─ prompts.ts          # Task 9: Prompt templates
│   │       ├─ storage.ts          # Task 10: Cache read/write
│   │       └─ export.ts           # Task 11: File System Access export
│   └─ styles/
│       └─ index.css               # Task 6: Tailwind base
├─ package.json                     # Task 1: Dependencies
├─ tsconfig.json                    # Task 1: TS config
├─ vite.config.ts                   # Task 1: Vite + CRXJS
├─ tailwind.config.ts               # Task 1: Tailwind
└─ postcss.config.js                # Task 1: PostCSS for Tailwind
```

---

### Task 1: Project Scaffolding

**Files:**
- Create: `extension/package.json`
- Create: `extension/tsconfig.json`
- Create: `extension/vite.config.ts`
- Create: `extension/tailwind.config.ts`
- Create: `extension/postcss.config.js`
- Create: `extension/manifest.json`
- Create: `extension/src/styles/index.css`

- [ ] **Step 1: Create package.json**

```json
{
  "name": "mpp-video-summarizer",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build"
  }
}
```

- [ ] **Step 2: Install dependencies**

Run:
```bash
cd extension && pnpm add react react-dom && pnpm add -D @crxjs/vite-plugin@beta @types/chrome @types/react @types/react-dom typescript vite tailwindcss @tailwindcss/vite postcss
```

- [ ] **Step 3: Create tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "noEmit": true,
    "baseUrl": ".",
    "paths": {
      "@/*": ["src/*"]
    }
  },
  "include": ["src"]
}
```

- [ ] **Step 4: Create vite.config.ts**

```typescript
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import { crx } from "@crxjs/vite-plugin"
import tailwindcss from "@tailwindcss/vite"
import manifest from "./manifest.json"
import { resolve } from "path"

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    crx({ manifest }),
  ],
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
})
```

- [ ] **Step 5: Create tailwind.config.ts**

```typescript
import type { Config } from "tailwindcss"

export default {
  content: ["src/**/*.{tsx,ts,html}"],
} satisfies Config
```

- [ ] **Step 6: Create postcss.config.js**

```javascript
export default {
  plugins: {
    tailwindcss: {},
  },
}
```

- [ ] **Step 7: Create src/styles/index.css**

```css
@import "tailwindcss";
```

- [ ] **Step 8: Create manifest.json**

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
    "service_worker": "src/background.ts"
  },
  "content_scripts": [
    {
      "matches": ["*://*.bilibili.com/video/*"],
      "js": ["src/content/bilibili.ts"],
      "run_at": "document_idle"
    },
    {
      "matches": ["*://*.youtube.com/watch*"],
      "js": ["src/content/youtube.ts"],
      "run_at": "document_idle"
    }
  ],
  "side_panel": {
    "default_path": "src/sidepanel/index.html"
  },
  "action": {
    "default_title": "Open MPP Summarizer"
  }
}
```

- [ ] **Step 9: Verify build runs**

Run: `cd extension && pnpm run build`
Expected: Build completes (may warn about missing entry files, that's OK at this stage)

- [ ] **Step 10: Commit**

```bash
git add extension/
git commit -m "feat(extension): scaffold project with Vite + CRXJS + React + Tailwind"
```

---

### Task 2: Background Service Worker

**Files:**
- Create: `extension/src/background.ts`

- [ ] **Step 1: Create background.ts**

```typescript
// Open side panel when extension icon is clicked
chrome.action.onClicked.addListener((tab) => {
  if (tab.id) {
    chrome.sidePanel.open({ tabId: tab.id })
  }
})

// Relay messages from content scripts to side panel
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "VIDEO_DATA" || message.type === "VIDEO_CHANGED") {
    // Forward to all extension pages (side panel will pick it up)
    chrome.runtime.sendMessage(message).catch(() => {
      // Side panel not open — ignore
    })
  }
  // Allow async response
  return false
})
```

- [ ] **Step 2: Commit**

```bash
git add extension/src/background.ts
git commit -m "feat(extension): add background service worker with message relay"
```

---

### Task 3: Shared Types

**Files:**
- Create: `extension/src/content/types.ts`

- [ ] **Step 1: Create types.ts**

```typescript
export interface SubtitleEntry {
  start: number   // seconds (float)
  end: number     // seconds (float)
  text: string
}

export interface VideoData {
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

/**
 * Messages sent from content scripts to background/sidepanel.
 */
export type ContentMessage =
  | { type: "VIDEO_DATA"; data: VideoData }
  | { type: "VIDEO_CHANGED"; videoId: string; platform: "bilibili" | "youtube" }
  | { type: "NO_SUBTITLES"; videoId: string; platform: "bilibili" | "youtube"; title: string }
```

- [ ] **Step 2: Commit**

```bash
git add extension/src/content/types.ts
git commit -m "feat(extension): add shared VideoData and message types"
```

---

### Task 4: Bilibili Content Script

**Files:**
- Create: `extension/src/content/bilibili.ts`

- [ ] **Step 1: Create bilibili.ts**

```typescript
import type { VideoData, SubtitleEntry, ContentMessage } from "./types"

interface BilibiliViewResponse {
  code: number
  data: {
    bvid: string
    aid: number
    title: string
    desc: string
    owner: { name: string; mid: number }
    duration: number
    pic: string
    pages: Array<{ cid: number; part: string; page: number }>
  }
}

interface BilibiliPlayerResponse {
  code: number
  data: {
    subtitle: {
      subtitles: Array<{
        id: number
        lan: string
        lan_doc: string
        subtitle_url: string
      }>
    }
  }
}

interface BilibiliBCCBody {
  from: number
  to: number
  content: string
}

interface BilibiliBCC {
  body: BilibiliBCCBody[]
}

function extractBvid(): string | null {
  const match = location.pathname.match(/\/video\/(BV[\w]+)/)
  return match ? match[1] : null
}

async function fetchMetadata(bvid: string): Promise<BilibiliViewResponse["data"] | null> {
  try {
    const resp = await fetch(
      `https://api.bilibili.com/x/web-interface/view?bvid=${bvid}`,
      { credentials: "include" }
    )
    const json: BilibiliViewResponse = await resp.json()
    if (json.code === 0) return json.data
  } catch (e) {
    console.error("[MPP] Bilibili metadata fetch failed:", e)
  }
  return null
}

async function fetchSubtitleList(
  bvid: string,
  cid: number
): Promise<BilibiliPlayerResponse["data"]["subtitle"]["subtitles"]> {
  try {
    const resp = await fetch(
      `https://api.bilibili.com/x/player/v2?bvid=${bvid}&cid=${cid}`,
      { credentials: "include" }
    )
    const json: BilibiliPlayerResponse = await resp.json()
    if (json.code === 0) return json.data.subtitle.subtitles
  } catch (e) {
    console.error("[MPP] Bilibili subtitle list fetch failed:", e)
  }
  return []
}

async function fetchSubtitleContent(url: string): Promise<SubtitleEntry[]> {
  try {
    // Bilibili subtitle URLs may start with "//" — add protocol
    const fullUrl = url.startsWith("//") ? `https:${url}` : url
    const resp = await fetch(fullUrl)
    const bcc: BilibiliBCC = await resp.json()
    return bcc.body.map((item) => ({
      start: item.from,
      end: item.to,
      text: item.content,
    }))
  } catch (e) {
    console.error("[MPP] Bilibili subtitle content fetch failed:", e)
    return []
  }
}

function pickBestSubtitle(
  subtitles: BilibiliPlayerResponse["data"]["subtitle"]["subtitles"]
): (typeof subtitles)[0] | null {
  if (subtitles.length === 0) return null
  // Prefer AI subtitles (lan starts with "ai-")
  const ai = subtitles.find((s) => s.lan.startsWith("ai-"))
  if (ai) return ai
  // Fallback to first available
  return subtitles[0]
}

async function extract() {
  const bvid = extractBvid()
  if (!bvid) return

  const meta = await fetchMetadata(bvid)
  if (!meta) return

  const cid = meta.pages[0]?.cid
  if (!cid) return

  const subtitleList = await fetchSubtitleList(bvid, cid)
  const best = pickBestSubtitle(subtitleList)

  if (!best) {
    const msg: ContentMessage = {
      type: "NO_SUBTITLES",
      videoId: bvid,
      platform: "bilibili",
      title: meta.title,
    }
    chrome.runtime.sendMessage(msg)
    return
  }

  const subtitles = await fetchSubtitleContent(best.subtitle_url)

  const videoData: VideoData = {
    platform: "bilibili",
    videoId: bvid,
    title: meta.title,
    description: meta.desc,
    uploader: meta.owner.name,
    duration: meta.duration,
    thumbnailUrl: meta.pic,
    subtitles,
    rawSubtitleLang: best.lan,
  }

  const msg: ContentMessage = { type: "VIDEO_DATA", data: videoData }
  chrome.runtime.sendMessage(msg)
}

// Initial extraction
extract()

// SPA navigation: re-extract when URL changes
let lastBvid = extractBvid()
const observer = new MutationObserver(() => {
  const currentBvid = extractBvid()
  if (currentBvid && currentBvid !== lastBvid) {
    lastBvid = currentBvid
    chrome.runtime.sendMessage({
      type: "VIDEO_CHANGED",
      videoId: currentBvid,
      platform: "bilibili",
    } satisfies ContentMessage)
    extract()
  }
})
observer.observe(document, { subtree: true, childList: true })
```

- [ ] **Step 2: Commit**

```bash
git add extension/src/content/bilibili.ts
git commit -m "feat(extension): add Bilibili content script — metadata + subtitle extraction"
```

---

### Task 5: YouTube Content Script

**Files:**
- Create: `extension/src/content/youtube.ts`

- [ ] **Step 1: Create youtube.ts**

```typescript
import type { VideoData, SubtitleEntry, ContentMessage } from "./types"

interface InnerTubePlayerResponse {
  videoDetails: {
    videoId: string
    title: string
    shortDescription: string
    author: string
    lengthSeconds: string
    thumbnail: { thumbnails: Array<{ url: string; width: number; height: number }> }
  }
  captions?: {
    playerCaptionsTracklistRenderer?: {
      captionTracks?: Array<{
        baseUrl: string
        name: { simpleText?: string; runs?: Array<{ text: string }> }
        vssId: string
        languageCode: string
        kind?: string
      }>
    }
  }
}

interface JSON3Event {
  tStartMs: number
  dDurationMs: number
  segs?: Array<{ utf8: string; tOffsetMs?: number }>
}

interface JSON3Response {
  events: JSON3Event[]
}

function extractVideoId(): string | null {
  const params = new URLSearchParams(location.search)
  return params.get("v")
}

async function fetchPlayerResponse(videoId: string): Promise<InnerTubePlayerResponse | null> {
  try {
    // Extract InnerTube client info from page HTML
    const pageResp = await fetch(`https://www.youtube.com/watch?v=${videoId}`)
    const html = await pageResp.text()

    const clientNameMatch = html.match(/"INNERTUBE_CLIENT_NAME":\s*"([^"]+)"/)
    const clientVersionMatch = html.match(/"INNERTUBE_CLIENT_VERSION":\s*"([^"]+)"/)

    const clientName = clientNameMatch?.[1] || "WEB"
    const clientVersion = clientVersionMatch?.[1] || "2.20240101.00.00"

    const resp = await fetch("https://www.youtube.com/youtubei/v1/player", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        context: { client: { clientName, clientVersion } },
        videoId,
      }),
    })
    return await resp.json()
  } catch (e) {
    console.error("[MPP] YouTube player response fetch failed:", e)
    return null
  }
}

function pickBestTrack(
  tracks: NonNullable<
    InnerTubePlayerResponse["captions"]
  >["playerCaptionsTracklistRenderer"]["captionTracks"]
): (typeof tracks)[number] | null {
  if (!tracks || tracks.length === 0) return null
  // Prefer manual subtitles (kind !== "asr")
  const manual = tracks.filter((t) => t.kind !== "asr")
  if (manual.length > 0) return manual[0]
  // Fallback to auto-generated
  return tracks[0]
}

function parseJSON3(data: JSON3Response): SubtitleEntry[] {
  const entries: SubtitleEntry[] = []
  for (const event of data.events) {
    if (!event.segs) continue
    const text = event.segs.map((s) => s.utf8).join("").trim()
    if (!text || text === "\n") continue
    entries.push({
      start: event.tStartMs / 1000,
      end: (event.tStartMs + event.dDurationMs) / 1000,
      text,
    })
  }
  return entries
}

async function fetchSubtitles(baseUrl: string): Promise<SubtitleEntry[]> {
  try {
    const url = baseUrl + "&fmt=json3"
    const resp = await fetch(url)
    const data: JSON3Response = await resp.json()
    return parseJSON3(data)
  } catch (e) {
    console.error("[MPP] YouTube subtitle fetch failed:", e)
    return []
  }
}

async function extract() {
  const videoId = extractVideoId()
  if (!videoId) return

  const player = await fetchPlayerResponse(videoId)
  if (!player) return

  const details = player.videoDetails
  const tracks = player.captions?.playerCaptionsTracklistRenderer?.captionTracks
  const best = pickBestTrack(tracks)

  if (!best) {
    const msg: ContentMessage = {
      type: "NO_SUBTITLES",
      videoId,
      platform: "youtube",
      title: details.title,
    }
    chrome.runtime.sendMessage(msg)
    return
  }

  const subtitles = await fetchSubtitles(best.baseUrl)

  const thumbnails = details.thumbnail.thumbnails
  const bestThumb = thumbnails[thumbnails.length - 1]

  const videoData: VideoData = {
    platform: "youtube",
    videoId,
    title: details.title,
    description: details.shortDescription,
    uploader: details.author,
    duration: parseInt(details.lengthSeconds, 10),
    thumbnailUrl: bestThumb?.url || "",
    subtitles,
    rawSubtitleLang: best.languageCode,
  }

  const msg: ContentMessage = { type: "VIDEO_DATA", data: videoData }
  chrome.runtime.sendMessage(msg)
}

// Initial extraction
extract()

// SPA navigation: YouTube uses History API
let lastVideoId = extractVideoId()
const observer = new MutationObserver(() => {
  const currentId = extractVideoId()
  if (currentId && currentId !== lastVideoId) {
    lastVideoId = currentId
    chrome.runtime.sendMessage({
      type: "VIDEO_CHANGED",
      videoId: currentId,
      platform: "youtube",
    } satisfies ContentMessage)
    extract()
  }
})
observer.observe(document, { subtree: true, childList: true })
```

- [ ] **Step 2: Commit**

```bash
git add extension/src/content/youtube.ts
git commit -m "feat(extension): add YouTube content script — InnerTube + JSON3 subtitle extraction"
```

---

### Task 6: Side Panel Shell (React + Routing + Message Listener)

**Files:**
- Create: `extension/src/sidepanel/index.html`
- Create: `extension/src/sidepanel/index.tsx`
- Create: `extension/src/sidepanel/App.tsx`

- [ ] **Step 1: Create index.html**

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>MPP Summarizer</title>
  </head>
  <body class="w-[400px] min-h-screen bg-white text-gray-900">
    <div id="root"></div>
    <script type="module" src="./index.tsx"></script>
  </body>
</html>
```

- [ ] **Step 2: Create index.tsx**

```tsx
import React from "react"
import ReactDOM from "react-dom/client"
import { App } from "./App"
import "@/styles/index.css"

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
```

- [ ] **Step 3: Create App.tsx**

```tsx
import { useEffect, useState, useCallback } from "react"
import type { VideoData, ContentMessage } from "@/content/types"
import { VideoInfo } from "./components/VideoInfo"
import { ActionBar } from "./components/ActionBar"
import { SummaryView } from "./components/SummaryView"
import { OutlineView } from "./components/OutlineView"
import { SubtitleView } from "./components/SubtitleView"
import { SettingsView } from "./components/SettingsView"
import { getCache } from "./lib/storage"

export interface AnalysisResult {
  analysis: { language: string; content_type: string; keywords: string[]; proper_nouns: string[] } | null
  summary: { tldr: string; key_facts: string[] } | null
  outline: string | null
}

type ViewState = "empty" | "no-subtitles" | "ready" | "analyzing" | "done" | "error"

export function App() {
  const [videoData, setVideoData] = useState<VideoData | null>(null)
  const [viewState, setViewState] = useState<ViewState>("empty")
  const [result, setResult] = useState<AnalysisResult>({ analysis: null, summary: null, outline: null })
  const [analysisStep, setAnalysisStep] = useState<string>("")
  const [error, setError] = useState<string>("")
  const [showSettings, setShowSettings] = useState(false)
  const [noSubTitle, setNoSubTitle] = useState("")

  // Listen for messages from content scripts (relayed via background)
  useEffect(() => {
    const handler = (message: ContentMessage) => {
      if (message.type === "VIDEO_DATA") {
        setVideoData(message.data)
        setViewState("ready")
        setResult({ analysis: null, summary: null, outline: null })
        setError("")
        // Check cache
        getCache(message.data.platform, message.data.videoId).then((cached) => {
          if (cached) {
            setResult(cached)
            setViewState("done")
          }
        })
      } else if (message.type === "NO_SUBTITLES") {
        setVideoData(null)
        setNoSubTitle(message.title)
        setViewState("no-subtitles")
      } else if (message.type === "VIDEO_CHANGED") {
        setVideoData(null)
        setViewState("empty")
        setResult({ analysis: null, summary: null, outline: null })
      }
    }
    chrome.runtime.onMessage.addListener(handler)
    return () => chrome.runtime.onMessage.removeListener(handler)
  }, [])

  const handleAnalyze = useCallback(async (force?: boolean) => {
    if (!videoData) return

    // Check cache unless force
    if (!force) {
      const cached = await getCache(videoData.platform, videoData.videoId)
      if (cached) {
        setResult(cached)
        setViewState("done")
        return
      }
    }

    setViewState("analyzing")
    setError("")

    try {
      const { runAnalysisPipeline } = await import("./lib/llm")
      await runAnalysisPipeline(videoData, {
        onStep: (step) => setAnalysisStep(step),
        onAnalysis: (analysis) => setResult((r) => ({ ...r, analysis })),
        onSummary: (summary) => setResult((r) => ({ ...r, summary })),
        onOutline: (outline) => setResult((r) => ({ ...r, outline })),
      })
      setViewState("done")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Analysis failed")
      setViewState("error")
    }
  }, [videoData])

  if (showSettings) {
    return <SettingsView onBack={() => setShowSettings(false)} />
  }

  if (viewState === "empty") {
    return (
      <div className="flex h-screen items-center justify-center p-6 text-center text-sm text-gray-400">
        Navigate to a Bilibili or YouTube video to get started
      </div>
    )
  }

  if (viewState === "no-subtitles") {
    return (
      <div className="flex h-screen flex-col items-center justify-center gap-2 p-6 text-center">
        <p className="text-sm font-medium">{noSubTitle}</p>
        <p className="text-sm text-gray-400">No subtitles available for this video</p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-0">
      {videoData && <VideoInfo data={videoData} />}
      <ActionBar
        canAnalyze={viewState === "ready" || viewState === "done" || viewState === "error"}
        canExport={viewState === "done"}
        analyzing={viewState === "analyzing"}
        analysisStep={analysisStep}
        videoData={videoData}
        result={result}
        onAnalyze={() => handleAnalyze(false)}
        onReanalyze={() => handleAnalyze(true)}
        onSettings={() => setShowSettings(true)}
      />
      {error && (
        <div className="border-b border-red-200 bg-red-50 px-4 py-2 text-xs text-red-600">
          {error}
        </div>
      )}
      <SummaryView summary={result.summary} />
      <OutlineView outline={result.outline} />
      <SubtitleView subtitles={videoData?.subtitles ?? []} />
    </div>
  )
}
```

- [ ] **Step 4: Verify build**

Run: `cd extension && pnpm run build`
Expected: Build will fail because component files don't exist yet — that's expected. Verify manifest and entry files are picked up.

- [ ] **Step 5: Commit**

```bash
git add extension/src/sidepanel/
git commit -m "feat(extension): add Side Panel shell — React root, App with message listener"
```

---

### Task 7: Side Panel UI Components

**Files:**
- Create: `extension/src/sidepanel/components/VideoInfo.tsx`
- Create: `extension/src/sidepanel/components/ActionBar.tsx`
- Create: `extension/src/sidepanel/components/SummaryView.tsx`
- Create: `extension/src/sidepanel/components/OutlineView.tsx`
- Create: `extension/src/sidepanel/components/SubtitleView.tsx`

- [ ] **Step 1: Create VideoInfo.tsx**

```tsx
import type { VideoData } from "@/content/types"

function formatDuration(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
  return `${m}:${String(s).padStart(2, "0")}`
}

export function VideoInfo({ data }: { data: VideoData }) {
  return (
    <div className="flex gap-3 border-b p-3">
      <img
        src={data.thumbnailUrl}
        alt=""
        className="h-16 w-28 shrink-0 rounded object-cover"
      />
      <div className="flex min-w-0 flex-col justify-center gap-0.5">
        <h2 className="line-clamp-2 text-sm font-medium leading-tight">{data.title}</h2>
        <p className="text-xs text-gray-400">
          {data.uploader} · {formatDuration(data.duration)}
        </p>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Create ActionBar.tsx**

```tsx
import type { VideoData } from "@/content/types"
import type { AnalysisResult } from "../App"
import { exportToLocal } from "../lib/export"

interface ActionBarProps {
  canAnalyze: boolean
  canExport: boolean
  analyzing: boolean
  analysisStep: string
  videoData: VideoData | null
  result: AnalysisResult
  onAnalyze: () => void
  onReanalyze: () => void
  onSettings: () => void
}

export function ActionBar({
  canAnalyze, canExport, analyzing, analysisStep,
  videoData, result, onAnalyze, onReanalyze, onSettings,
}: ActionBarProps) {
  const handleExport = async () => {
    if (!videoData) return
    try {
      await exportToLocal(videoData, result)
    } catch (e) {
      console.error("[MPP] Export failed:", e)
    }
  }

  return (
    <div className="flex items-center gap-2 border-b px-3 py-2">
      {analyzing ? (
        <div className="flex flex-1 items-center gap-2 text-xs text-gray-500">
          <svg className="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
            <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" className="opacity-20" />
            <path d="M12 2a10 10 0 0 1 10 10" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
          </svg>
          {analysisStep}
        </div>
      ) : (
        <>
          <button
            onClick={canExport ? onReanalyze : onAnalyze}
            disabled={!canAnalyze}
            className="flex items-center gap-1.5 rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-40"
          >
            {canExport ? "Re-analyze" : "Analyze"}
          </button>
          <button
            onClick={handleExport}
            disabled={!canExport}
            className="rounded-md border px-3 py-1.5 text-xs transition-colors hover:bg-gray-50 disabled:opacity-40"
          >
            Export
          </button>
        </>
      )}
      <button
        onClick={onSettings}
        className="ml-auto rounded-md p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
        title="Settings"
      >
        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
          <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
        </svg>
      </button>
    </div>
  )
}
```

- [ ] **Step 3: Create SummaryView.tsx**

```tsx
import { useState } from "react"

interface SummaryData {
  tldr: string
  key_facts: string[]
}

export function SummaryView({ summary }: { summary: SummaryData | null }) {
  const [open, setOpen] = useState(true)

  if (!summary) return null

  return (
    <div className="border-b">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium hover:bg-gray-50"
      >
        <span className="text-xs text-gray-400">{open ? "▾" : "▸"}</span>
        Summary
      </button>
      {open && (
        <div className="space-y-2 px-3 pb-3">
          <p className="text-sm leading-relaxed text-gray-700">{summary.tldr}</p>
          {summary.key_facts.length > 0 && (
            <ul className="space-y-1">
              {summary.key_facts.map((fact, i) => (
                <li key={i} className="flex gap-2 text-xs leading-relaxed text-gray-600">
                  <span className="mt-0.5 shrink-0 text-gray-300">•</span>
                  {fact}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Create OutlineView.tsx**

```tsx
import { useState } from "react"

export function OutlineView({ outline }: { outline: string | null }) {
  const [open, setOpen] = useState(true)

  if (!outline) return null

  // Parse markdown bullet list into structured items
  const lines = outline.split("\n").filter((l) => l.trim())

  return (
    <div className="border-b">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium hover:bg-gray-50"
      >
        <span className="text-xs text-gray-400">{open ? "▾" : "▸"}</span>
        Outline
      </button>
      {open && (
        <div className="space-y-0.5 px-3 pb-3">
          {lines.map((line, i) => {
            // Count leading spaces to determine indent level
            const stripped = line.replace(/^[\s]*[-*]\s*/, "")
            const indent = (line.length - line.trimStart().length) / 2
            return (
              <p
                key={i}
                className="text-xs leading-relaxed text-gray-600"
                style={{ paddingLeft: `${indent * 16}px` }}
              >
                {stripped}
              </p>
            )
          })}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 5: Create SubtitleView.tsx**

```tsx
import { useState } from "react"
import type { SubtitleEntry } from "@/content/types"

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
}

export function SubtitleView({ subtitles }: { subtitles: SubtitleEntry[] }) {
  const [open, setOpen] = useState(false)

  if (subtitles.length === 0) return null

  return (
    <div className="border-b">
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm font-medium hover:bg-gray-50"
      >
        <span className="text-xs text-gray-400">{open ? "▾" : "▸"}</span>
        Subtitles
        <span className="text-xs font-normal text-gray-400">({subtitles.length})</span>
      </button>
      {open && (
        <div className="max-h-[50vh] overflow-y-auto px-3 pb-3">
          {subtitles.map((sub, i) => (
            <div key={i} className="flex gap-2 py-0.5">
              <span className="shrink-0 text-[10px] tabular-nums text-gray-300">
                {formatTime(sub.start)}
              </span>
              <span className="text-xs leading-relaxed text-gray-600">{sub.text}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 6: Commit**

```bash
git add extension/src/sidepanel/components/
git commit -m "feat(extension): add Side Panel UI components — VideoInfo, Summary, Outline, Subtitles"
```

---

### Task 8: Settings View

**Files:**
- Create: `extension/src/sidepanel/components/SettingsView.tsx`

- [ ] **Step 1: Create SettingsView.tsx**

```tsx
import { useEffect, useState } from "react"

export interface LLMSettings {
  provider: "anthropic" | "openai" | "deepseek" | "custom"
  apiKey: string
  apiBase: string
  model: string
  temperature: number
}

const DEFAULT_SETTINGS: LLMSettings = {
  provider: "openai",
  apiKey: "",
  apiBase: "",
  model: "",
  temperature: 0.1,
}

const PROVIDER_DEFAULTS: Record<string, { model: string; apiBase: string }> = {
  anthropic: { model: "claude-sonnet-4-20250514", apiBase: "" },
  openai: { model: "gpt-4o", apiBase: "" },
  deepseek: { model: "deepseek-chat", apiBase: "https://api.deepseek.com/v1" },
  custom: { model: "", apiBase: "" },
}

export async function loadSettings(): Promise<LLMSettings> {
  const { llmSettings } = await chrome.storage.local.get("llmSettings")
  return llmSettings ? { ...DEFAULT_SETTINGS, ...llmSettings } : DEFAULT_SETTINGS
}

export async function saveSettings(settings: LLMSettings): Promise<void> {
  await chrome.storage.local.set({ llmSettings: settings })
}

export function SettingsView({ onBack }: { onBack: () => void }) {
  const [settings, setSettings] = useState<LLMSettings>(DEFAULT_SETTINGS)
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    loadSettings().then(setSettings)
  }, [])

  const handleProviderChange = (provider: LLMSettings["provider"]) => {
    const defaults = PROVIDER_DEFAULTS[provider]
    setSettings((s) => ({
      ...s,
      provider,
      model: defaults.model,
      apiBase: defaults.apiBase,
    }))
  }

  const handleSave = async () => {
    await saveSettings(settings)
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  return (
    <div className="flex flex-col gap-4 p-4">
      <div className="flex items-center gap-2">
        <button onClick={onBack} className="text-sm text-blue-600 hover:text-blue-800">
          ← Back
        </button>
        <h2 className="text-sm font-medium">LLM Settings</h2>
      </div>

      <div className="space-y-3">
        <div>
          <label className="mb-1 block text-xs text-gray-500">Provider</label>
          <select
            value={settings.provider}
            onChange={(e) => handleProviderChange(e.target.value as LLMSettings["provider"])}
            className="w-full rounded border px-2 py-1.5 text-sm"
          >
            <option value="openai">OpenAI</option>
            <option value="anthropic">Anthropic</option>
            <option value="deepseek">DeepSeek</option>
            <option value="custom">Custom (OpenAI-compatible)</option>
          </select>
        </div>

        <div>
          <label className="mb-1 block text-xs text-gray-500">API Key</label>
          <input
            type="password"
            value={settings.apiKey}
            onChange={(e) => setSettings((s) => ({ ...s, apiKey: e.target.value }))}
            placeholder="sk-..."
            className="w-full rounded border px-2 py-1.5 text-sm"
          />
        </div>

        {(settings.provider === "custom" || settings.provider === "deepseek") && (
          <div>
            <label className="mb-1 block text-xs text-gray-500">API Base URL</label>
            <input
              type="url"
              value={settings.apiBase}
              onChange={(e) => setSettings((s) => ({ ...s, apiBase: e.target.value }))}
              placeholder="https://api.example.com/v1"
              className="w-full rounded border px-2 py-1.5 text-sm"
            />
          </div>
        )}

        <div>
          <label className="mb-1 block text-xs text-gray-500">Model</label>
          <input
            value={settings.model}
            onChange={(e) => setSettings((s) => ({ ...s, model: e.target.value }))}
            placeholder="gpt-4o"
            className="w-full rounded border px-2 py-1.5 text-sm"
          />
        </div>

        <div>
          <label className="mb-1 block text-xs text-gray-500">
            Temperature ({settings.temperature})
          </label>
          <input
            type="range"
            min="0"
            max="1"
            step="0.1"
            value={settings.temperature}
            onChange={(e) => setSettings((s) => ({ ...s, temperature: parseFloat(e.target.value) }))}
            className="w-full"
          />
        </div>

        <button
          onClick={handleSave}
          className="w-full rounded-md bg-blue-600 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-700"
        >
          {saved ? "Saved ✓" : "Save Settings"}
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add extension/src/sidepanel/components/SettingsView.tsx
git commit -m "feat(extension): add Settings view — LLM provider/key/model config"
```

---

### Task 9: LLM Integration (Call Wrapper + Prompts)

**Files:**
- Create: `extension/src/sidepanel/lib/llm.ts`
- Create: `extension/src/sidepanel/lib/prompts.ts`

- [ ] **Step 1: Create prompts.ts**

Ported from MPP backend `backend/app/services/analysis/prompts/`, adapted for plain text input (no SRT format).

```typescript
export function getAnalyzePrompt(
  title: string,
  text: string,
  uploader?: string,
  description?: string,
): string {
  const metaParts = [`- 标题: ${title}`]
  if (uploader) metaParts.push(`- 作者: ${uploader}`)
  if (description) {
    const desc = description.length > 1000 ? description.slice(0, 1000) + "..." : description
    metaParts.push(`- 简介: ${desc}`)
  }
  const metaSection = metaParts.join("\n")

  return `请分析以下转录文本，提取关键信息。

## 视频/音频元信息
${metaSection}

## 转录内容
${text}

请根据上述元信息和转录内容，返回 JSON 格式:
{
    "language": "检测到的主要语言代码，如 zh-CN, en-US, ja-JP",
    "content_type": "内容类型，如 技术讲座/访谈/播客/会议/教程/演讲/评测/新闻",
    "keywords": ["关键词1", "关键词2", "关键词3", "关键词4", "关键词5"],
    "proper_nouns": ["专有名词/人名/产品名/术语1", "专有名词2", "专有名词3"]
}

注意:
1. 充分利用简介中的信息来辅助识别专有名词和话题
2. 只返回 JSON，不要其他内容`
}

export function getSummarizePrompt(text: string): string {
  return `分析以下转录文本，生成结构化摘要。

转录内容:
${text}

请返回 JSON 格式:
{
    "tldr": "一句话总结（不超过100字）",
    "key_facts": [
        "关键要点1",
        "关键要点2",
        "关键要点3"
    ]
}

注意:
1. key_facts 应包含 3-10 个最重要的信息点
2. 只返回 JSON，不要其他内容`
}

export function getOutlinePrompt(text: string): string {
  return `你是一个内容整理专家。请阅读以下文本（可能是会议录音、访谈、讲座的转录），提炼其中的核心内容，生成一份结构化的思维导图大纲。

## 关键要求
1. **归纳提炼**，不是逐句搬运。将零散的口语对话提炼为有信息量的要点
2. 一级节点为讨论的主要话题板块（3-8 个），二级为该话题下的要点，三级为具体细节
3. 每个节点应是一个完整的、有信息量的短句，而不是口语碎片
4. 过滤掉语气词、重复、无意义的对话
5. 保留关键人名、术语、数字、决策、结论
6. 合并表达同一意思的多句话为一个节点

## 格式要求
- 使用 \`- \` 标记，2 空格缩进表示层级（最多 3 层深度）
- 纯文本，禁止使用任何 markdown 格式（不要加粗、斜体、链接、代码块、标题符号）
- 直接输出列表，不要任何前言或总结

## 待提炼内容
${text}

请直接输出纯文本列表:`
}
```

- [ ] **Step 2: Create llm.ts**

```typescript
import type { VideoData } from "@/content/types"
import type { AnalysisResult } from "../App"
import { loadSettings, type LLMSettings } from "../components/SettingsView"
import { getAnalyzePrompt, getSummarizePrompt, getOutlinePrompt } from "./prompts"
import { setCache } from "./storage"

interface ChatResponse {
  choices: Array<{
    message: { content: string }
  }>
}

function getApiUrl(settings: LLMSettings): string {
  if (settings.provider === "anthropic") {
    return (settings.apiBase || "https://api.anthropic.com/v1") + "/chat/completions"
  }
  if (settings.provider === "deepseek") {
    return (settings.apiBase || "https://api.deepseek.com/v1") + "/chat/completions"
  }
  if (settings.provider === "custom") {
    return settings.apiBase + "/chat/completions"
  }
  // openai
  return (settings.apiBase || "https://api.openai.com/v1") + "/chat/completions"
}

async function callLLM(prompt: string, settings: LLMSettings, maxRetries = 3): Promise<string> {
  const url = getApiUrl(settings)

  for (let attempt = 0; attempt < maxRetries; attempt++) {
    try {
      const resp = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${settings.apiKey}`,
        },
        body: JSON.stringify({
          model: settings.model,
          messages: [{ role: "user", content: prompt }],
          temperature: settings.temperature,
        }),
      })

      if (!resp.ok) {
        const body = await resp.text()
        throw new Error(`API error ${resp.status}: ${body.slice(0, 200)}`)
      }

      const data: ChatResponse = await resp.json()
      return data.choices[0]?.message?.content || ""
    } catch (e) {
      const isRetryable =
        e instanceof TypeError || // network error
        (e instanceof Error && e.message.includes("timeout"))
      if (isRetryable && attempt < maxRetries - 1) {
        await new Promise((r) => setTimeout(r, 2 ** attempt * 1000))
        continue
      }
      throw e
    }
  }
  throw new Error("Max retries exceeded")
}

function parseJSON(text: string): Record<string, unknown> {
  const start = text.indexOf("{")
  const end = text.lastIndexOf("}") + 1
  if (start >= 0 && end > start) {
    return JSON.parse(text.slice(start, end))
  }
  throw new Error("No JSON found in response")
}

interface PipelineCallbacks {
  onStep: (step: string) => void
  onAnalysis: (analysis: AnalysisResult["analysis"]) => void
  onSummary: (summary: AnalysisResult["summary"]) => void
  onOutline: (outline: string) => void
}

export async function runAnalysisPipeline(
  videoData: VideoData,
  callbacks: PipelineCallbacks,
): Promise<void> {
  const settings = await loadSettings()
  if (!settings.apiKey) {
    throw new Error("Please configure your LLM API key in Settings")
  }

  const subtitleText = videoData.subtitles.map((s) => s.text).join("\n")
  const truncatedText = subtitleText.slice(0, 8000)

  // Step 1: Analyze
  callbacks.onStep("Analyzing content...")
  const analyzePrompt = getAnalyzePrompt(
    videoData.title,
    truncatedText,
    videoData.uploader,
    videoData.description,
  )
  const analyzeResp = await callLLM(analyzePrompt, settings)
  const analysis = parseJSON(analyzeResp) as AnalysisResult["analysis"]
  callbacks.onAnalysis(analysis)

  // Step 2: Summarize
  callbacks.onStep("Generating summary...")
  const summarizePrompt = getSummarizePrompt(subtitleText)
  const summarizeResp = await callLLM(summarizePrompt, settings)
  const summary = parseJSON(summarizeResp) as AnalysisResult["summary"]
  callbacks.onSummary(summary)

  // Step 3: Outline
  callbacks.onStep("Generating outline...")
  const outlinePrompt = getOutlinePrompt(subtitleText)
  const outline = await callLLM(outlinePrompt, settings)
  callbacks.onOutline(outline)

  // Cache result
  await setCache(videoData.platform, videoData.videoId, {
    analysis,
    summary,
    outline,
  })
}
```

- [ ] **Step 3: Commit**

```bash
git add extension/src/sidepanel/lib/llm.ts extension/src/sidepanel/lib/prompts.ts
git commit -m "feat(extension): add LLM call wrapper + prompt templates (analyze/summarize/outline)"
```

---

### Task 10: Cache Storage

**Files:**
- Create: `extension/src/sidepanel/lib/storage.ts`

- [ ] **Step 1: Create storage.ts**

```typescript
import type { AnalysisResult } from "../App"

interface CacheEntry {
  analysis: AnalysisResult["analysis"]
  summary: AnalysisResult["summary"]
  outline: AnalysisResult["outline"]
  timestamp: number
}

const MAX_CACHE_ENTRIES = 100

function cacheKey(platform: string, videoId: string): string {
  return `cache:${platform}:${videoId}`
}

export async function getCache(
  platform: string,
  videoId: string,
): Promise<AnalysisResult | null> {
  const key = cacheKey(platform, videoId)
  const result = await chrome.storage.local.get(key)
  const entry: CacheEntry | undefined = result[key]
  if (!entry) return null
  return {
    analysis: entry.analysis,
    summary: entry.summary,
    outline: entry.outline,
  }
}

export async function setCache(
  platform: string,
  videoId: string,
  result: Omit<CacheEntry, "timestamp">,
): Promise<void> {
  const key = cacheKey(platform, videoId)
  const entry: CacheEntry = { ...result, timestamp: Date.now() }
  await chrome.storage.local.set({ [key]: entry })

  // Evict oldest entries if over limit
  await evictOldEntries()
}

async function evictOldEntries(): Promise<void> {
  const all = await chrome.storage.local.get(null)
  const cacheEntries: Array<{ key: string; timestamp: number }> = []

  for (const [key, value] of Object.entries(all)) {
    if (key.startsWith("cache:") && typeof value === "object" && value !== null && "timestamp" in value) {
      cacheEntries.push({ key, timestamp: (value as CacheEntry).timestamp })
    }
  }

  if (cacheEntries.length <= MAX_CACHE_ENTRIES) return

  // Sort oldest first, remove excess
  cacheEntries.sort((a, b) => a.timestamp - b.timestamp)
  const toRemove = cacheEntries.slice(0, cacheEntries.length - MAX_CACHE_ENTRIES)
  await chrome.storage.local.remove(toRemove.map((e) => e.key))
}
```

- [ ] **Step 2: Commit**

```bash
git add extension/src/sidepanel/lib/storage.ts
git commit -m "feat(extension): add cache storage — LRU eviction, 100-entry limit"
```

---

### Task 11: File Export (File System Access API)

**Files:**
- Create: `extension/src/sidepanel/lib/export.ts`

- [ ] **Step 1: Create export.ts**

```typescript
import type { VideoData, SubtitleEntry } from "@/content/types"
import type { AnalysisResult } from "../App"

const DIR_HANDLE_DB = "mpp-export-handle"
const DIR_HANDLE_STORE = "handles"

function safeName(name: string): string {
  let safe = name
  for (const c of '<>:"/\\|?*') {
    safe = safe.replaceAll(c, "_")
  }
  return safe.slice(0, 100).trim()
}

function subtitlesToSRT(subtitles: SubtitleEntry[]): string {
  return subtitles
    .map((sub, i) => {
      const startH = Math.floor(sub.start / 3600)
      const startM = Math.floor((sub.start % 3600) / 60)
      const startS = Math.floor(sub.start % 60)
      const startMs = Math.floor((sub.start % 1) * 1000)
      const endH = Math.floor(sub.end / 3600)
      const endM = Math.floor((sub.end % 3600) / 60)
      const endS = Math.floor(sub.end % 60)
      const endMs = Math.floor((sub.end % 1) * 1000)

      const fmt = (h: number, m: number, s: number, ms: number) =>
        `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")},${String(ms).padStart(3, "0")}`

      return `${i + 1}\n${fmt(startH, startM, startS, startMs)} --> ${fmt(endH, endM, endS, endMs)}\n${sub.text}`
    })
    .join("\n\n")
}

async function getPersistedHandle(): Promise<FileSystemDirectoryHandle | null> {
  try {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const req = indexedDB.open(DIR_HANDLE_DB, 1)
      req.onupgradeneeded = () => req.result.createObjectStore(DIR_HANDLE_STORE)
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => reject(req.error)
    })
    const tx = db.transaction(DIR_HANDLE_STORE, "readonly")
    const store = tx.objectStore(DIR_HANDLE_STORE)
    const handle = await new Promise<FileSystemDirectoryHandle | undefined>((resolve) => {
      const req = store.get("exportDir")
      req.onsuccess = () => resolve(req.result)
      req.onerror = () => resolve(undefined)
    })
    db.close()
    if (!handle) return null

    // Verify permission
    const perm = await handle.requestPermission({ mode: "readwrite" })
    return perm === "granted" ? handle : null
  } catch {
    return null
  }
}

async function persistHandle(handle: FileSystemDirectoryHandle): Promise<void> {
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const req = indexedDB.open(DIR_HANDLE_DB, 1)
    req.onupgradeneeded = () => req.result.createObjectStore(DIR_HANDLE_STORE)
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
  const tx = db.transaction(DIR_HANDLE_STORE, "readwrite")
  tx.objectStore(DIR_HANDLE_STORE).put(handle, "exportDir")
  await new Promise<void>((resolve) => { tx.oncomplete = () => resolve() })
  db.close()
}

async function getExportDir(): Promise<FileSystemDirectoryHandle> {
  // Try restoring persisted handle
  const persisted = await getPersistedHandle()
  if (persisted) return persisted

  // Ask user to pick directory
  const handle = await window.showDirectoryPicker({ mode: "readwrite" })
  await persistHandle(handle)
  return handle
}

async function writeFile(
  dir: FileSystemDirectoryHandle,
  name: string,
  content: string,
): Promise<void> {
  const file = await dir.getFileHandle(name, { create: true })
  const writable = await file.createWritable()
  await writable.write(content)
  await writable.close()
}

export async function exportToLocal(
  videoData: VideoData,
  result: AnalysisResult,
): Promise<void> {
  const rootDir = await getExportDir()
  const dirName = safeName(videoData.title)
  const subDir = await rootDir.getDirectoryHandle(dirName, { create: true })

  // metadata.json
  const metadata = {
    title: videoData.title,
    source_url:
      videoData.platform === "bilibili"
        ? `https://www.bilibili.com/video/${videoData.videoId}`
        : `https://www.youtube.com/watch?v=${videoData.videoId}`,
    platform: videoData.platform,
    uploader: videoData.uploader,
    duration_seconds: videoData.duration,
    subtitle_lang: videoData.rawSubtitleLang,
    exported_at: new Date().toISOString(),
  }
  await writeFile(subDir, "metadata.json", JSON.stringify(metadata, null, 2))

  // transcript.srt
  if (videoData.subtitles.length > 0) {
    await writeFile(subDir, "transcript.srt", subtitlesToSRT(videoData.subtitles))
  }

  // analysis.json
  if (result.analysis) {
    await writeFile(subDir, "analysis.json", JSON.stringify(result.analysis, null, 2))
  }

  // summary.md
  if (result.summary) {
    const summaryMd = `---
title: "${videoData.title}"
source: "${metadata.source_url}"
date: ${new Date().toISOString().split("T")[0]}
tags: [media-pipeline]
---

# ${videoData.title}

## Summary
${result.summary.tldr}

### Key Facts
${result.summary.key_facts.map((f) => `- ${f}`).join("\n")}
`
    await writeFile(subDir, "summary.md", summaryMd)
  }

  // outline.md
  if (result.outline) {
    await writeFile(subDir, "outline.md", result.outline)
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add extension/src/sidepanel/lib/export.ts
git commit -m "feat(extension): add File System Access export — MPP-compatible directory structure"
```

---

### Task 12: Build Verification + Load in Chrome

- [ ] **Step 1: Build the extension**

Run: `cd extension && pnpm run build`
Expected: Build completes successfully, output in `extension/dist/`

- [ ] **Step 2: Verify dist output**

Run: `ls extension/dist/`
Expected: `manifest.json`, JS bundles for background, content scripts, and sidepanel HTML

- [ ] **Step 3: Add .gitignore**

Create `extension/.gitignore`:
```
node_modules/
dist/
```

- [ ] **Step 4: Commit**

```bash
git add extension/.gitignore
git commit -m "feat(extension): verify build passes, add .gitignore"
```

- [ ] **Step 5: Manual test**

1. Open Chrome → `chrome://extensions/` → Enable Developer mode
2. Click "Load unpacked" → Select `extension/dist/`
3. Navigate to a Bilibili video → Click extension icon → Side Panel opens
4. Verify: video info displayed, subtitle count shown
5. Configure LLM API key in Settings
6. Click Analyze → verify 3-step pipeline runs
7. Click Export → pick a directory → verify files written
8. Repeat with a YouTube video
