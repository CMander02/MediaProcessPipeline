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
  const manual = tracks.filter((t) => t.kind !== "asr")
  if (manual.length > 0) return manual[0]
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
