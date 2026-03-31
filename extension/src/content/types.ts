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
