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
  const ai = subtitles.find((s) => s.lan.startsWith("ai-"))
  if (ai) return ai
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
