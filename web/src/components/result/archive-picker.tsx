import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import type { ArchiveItem } from "@/hooks/use-archives"

interface ArchivePickerProps {
  archives: ArchiveItem[]
  selectedPath: string
  onSelect: (path: string) => void
}

export function ArchivePicker({ archives, selectedPath, onSelect }: ArchivePickerProps) {
  return (
    <Select value={selectedPath} onValueChange={(v) => v && onSelect(v)}>
      <SelectTrigger className="w-full">
        <SelectValue placeholder="选择已完成的归档查看结果..." />
      </SelectTrigger>
      <SelectContent>
        {archives.length === 0 ? (
          <SelectItem value="_" disabled>
            暂无归档结果
          </SelectItem>
        ) : (
          archives.map((a) => (
            <SelectItem key={a.path} value={a.path}>
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">{a.date}</span>
                <span className="truncate">{a.title}</span>
                <div className="flex gap-1 ml-auto">
                  {a.has_video && (
                    <span className="text-[10px] px-1 rounded bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300">
                      视频
                    </span>
                  )}
                  {a.has_audio && !a.has_video && (
                    <span className="text-[10px] px-1 rounded bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300">
                      音频
                    </span>
                  )}
                </div>
              </div>
            </SelectItem>
          ))
        )}
      </SelectContent>
    </Select>
  )
}
