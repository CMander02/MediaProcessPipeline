import { useState } from "react"

import type { ArchiveSort, MediaFilter, SourceFilter } from "@/lib/archive-filters"

export function useLibraryControls() {
  const [search, setSearch] = useState("")
  const [mediaFilter, setMediaFilter] = useState<MediaFilter>("all")
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all")
  const [sort, setSort] = useState<ArchiveSort>("created_desc")

  return {
    search,
    setSearch,
    mediaFilter,
    setMediaFilter,
    sourceFilter,
    setSourceFilter,
    sort,
    setSort,
  }
}
