import { useDeferredValue, useMemo } from "react"

import { AppOutlet } from "@/components/app-outlet"
import { AppShell } from "@/components/app-shell/app-shell"
import { PageToolbar } from "@/components/app-shell/page-toolbar"
import { ComposerDialog } from "@/components/composer/composer-dialog"
import { GlobalIntake } from "@/components/composer/global-intake"
import { PwaStatus } from "@/components/pwa-status"
import { Toaster } from "@/components/ui/sonner"
import { useAppStartup } from "@/hooks/use-app-startup"
import { useLibraryControls } from "@/hooks/use-library-controls"
import { useTaskNotifications } from "@/hooks/use-task-notifications"
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
  useTaskNotifications()

  const toolbar = route.page === "files" ? <PageToolbar library={library} /> : undefined
  const filters = useMemo(() => ({
    search: deferredSearch,
    media: library.media,
    sources: library.sources,
    statuses: library.statuses,
    duplicates: library.duplicates,
    sort: library.sort,
  }), [deferredSearch, library.duplicates, library.media, library.sort, library.sources, library.statuses])

  return (
    <AppShell activePage={route.page} toolbar={toolbar}>
      <AppOutlet
        route={route}
        library={{ filters, page: library.page, onPageChange: library.setPage }}
      />
      <ComposerDialog />
      <GlobalIntake />
    </AppShell>
  )
}
