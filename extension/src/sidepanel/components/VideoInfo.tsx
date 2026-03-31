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
