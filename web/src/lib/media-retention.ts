export type MediaPolicy = "all" | "playback" | "text"

export const mediaPolicies: Record<MediaPolicy, string> = {
  all: "保留全部",
  playback: "保留播放文件及文字、图片",
  text: "仅保留文字、图片和封面",
}

export interface MediaRetentionEntry {
  path: string
  role: string
  bytes: number
  delete: boolean
  reason: string
  recovery: string
  impact?: string
}

export interface MediaRetentionPreview {
  path: string
  policy: MediaPolicy
  entries: MediaRetentionEntry[]
  reclaimable_bytes: number
  protected_reason: string
  cleaned?: MediaRetentionEntry[]
  reclaimed_bytes?: number
  errors?: { path: string; error: string }[]
}
