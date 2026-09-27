import { useDeferredValue } from "react"

import { AppOutlet } from "@/components/app-outlet"
import { AppShell } from "@/components/app-shell/app-shell"
import { PageToolbar } from "@/components/app-shell/page-toolbar"
import { ComposerDialog } from "@/components/composer/composer-dialog"
import { GlobalIntake } from "@/components/composer/global-intake"
import { PwaStatus } from "@/components/pwa-status"
import { Toaster } from "@/components/ui/sonner"
import { useAppStartup } from "@/hooks/use-app-startup"
import { useLibraryControls } from "@/hooks/use-library-controls"
import { AppAccessBoundary } from "@/hooks/use-app-access"
import { useRoute } from "@/lib/router"

export default function App() {
  return (
    <>
      <AppAccessBoundary>
        <AuthenticatedApp />
      </AppAccessBoundary>
      <PwaStatus />
      <Toaster />
    </>
  )
}

function AuthenticatedApp() {
  const route = useRoute()
  const library = useLibraryControls()
  const deferredSearch = useDeferredValue(library.search)

  useAppStartup()

  const toolbar = route.page === "files" ? (
    <PageToolbar
      search={library.search}
      mediaFilter={library.mediaFilter}
      sourceFilter={library.sourceFilter}
      sort={library.sort}
      status={library.status}
      onSearchChange={library.setSearch}
      onMediaFilterChange={library.setMediaFilter}
      onSourceFilterChange={library.setSourceFilter}
      onSortChange={library.setSort}
      onStatusChange={library.setStatus}
    />
  ) : undefined

  return (
    <AppShell activePage={route.page} toolbar={toolbar}>
      <AppOutlet
        route={route}
        library={{
          search: deferredSearch,
          mediaFilter: library.mediaFilter,
          sourceFilter: library.sourceFilter,
          sort: library.sort,
          status: library.status,
          onStatusChange: library.setStatus,
          page: library.page,
          onPageChange: library.setPage,
        }}
      />
      <ComposerDialog />
      <GlobalIntake />
    </AppShell>
  )
}
