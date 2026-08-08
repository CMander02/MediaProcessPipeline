import { lazy, Suspense } from "react"

import { LoadingState } from "@/components/ui/page-state"
import type { ArchiveSort, MediaFilter, SourceFilter } from "@/lib/archive-filters"
import type { Route } from "@/lib/router"

const FilesPage = lazy(() => import("@/components/pages/files-page").then((module) => ({ default: module.FilesPage })))
const SubmitPage = lazy(() => import("@/components/pages/submit-page").then((module) => ({ default: module.SubmitPage })))
const BackendPage = lazy(() => import("@/components/pages/backend-page").then((module) => ({ default: module.BackendPage })))
const ResultPageWrapper = lazy(() => import("@/components/pages/result-page-wrapper").then((module) => ({ default: module.ResultPageWrapper })))
const SettingsPage = lazy(() => import("@/components/pages/settings-page").then((module) => ({ default: module.SettingsPage })))

interface AppOutletProps {
  route: Route
  library: {
    search: string
    mediaFilter: MediaFilter
    sourceFilter: SourceFilter
    sort: ArchiveSort
  }
}

export function AppOutlet({ route, library }: AppOutletProps) {
  return (
    <Suspense fallback={<LoadingState title="正在载入页面" className="h-full" />}>
      {route.page === "files" ? <FilesPage {...library} /> : null}
      {route.page === "submit" ? <SubmitPage /> : null}
      {route.page === "backend" ? <BackendPage /> : null}
      {route.page === "result" ? <ResultPageWrapper /> : null}
      {route.page === "settings" ? <SettingsPage /> : null}
    </Suspense>
  )
}
