import { useDeferredValue } from "react"

import { AppOutlet } from "@/components/app-outlet"
import { AppShell } from "@/components/app-shell/app-shell"
import { PageToolbar } from "@/components/app-shell/page-toolbar"
import { PwaStatus } from "@/components/pwa-status"
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
      onSearchChange={library.setSearch}
      onMediaFilterChange={library.setMediaFilter}
      onSourceFilterChange={library.setSourceFilter}
      onSortChange={library.setSort}
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
        }}
      />
    </AppShell>
  )
}
