export interface MediaItem {
  id: string
  taskId?: string
  title: string
  contentSubtype: string | null
  thumbnailPath: string | null
  mediaPath: string | null
}
