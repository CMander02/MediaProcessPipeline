import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react"

import { AppShell } from "@/components/app-shell/app-shell"
import { NativeConnectionScreen } from "@/components/native-connection"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { LoadingState, OfflineState, UnauthorizedState } from "@/components/ui/page-state"
import { AppAccessContext } from "@/hooks/use-app-access-context"
import { api, type AuthStatus, type Capabilities } from "@/lib/api"
import { useRoute } from "@/lib/router"
import { usePlatform } from "@/platform/use-platform"
import type { ServerConnection } from "@/platform"

const OFFLINE_CAPABILITIES: Capabilities = {
  mode: "remote",
  authenticated: true,
  url_submission: false,
  browser_file_upload: false,
  browser_folder_upload: false,
  task_control: false,
  settings: false,
  filesystem_browse: false,
  local_path_submission: false,
  open_local_folder: false,
  archive_mutation: false,
}

export function AppAccessBoundary({ children }: { children: ReactNode }) {
  const route = useRoute()
  const platform = usePlatform()
  const [auth, setAuth] = useState<AuthStatus | null>(null)
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [connection, setConnection] = useState<ServerConnection | null>(null)
  const [connectionDown, setConnectionDown] = useState(false)
  const [editingConnection, setEditingConnection] = useState(false)
  const [unlockToken, setUnlockToken] = useState("")
  const [unlocking, setUnlocking] = useState(false)
  const [unlockError, setUnlockError] = useState<string | null>(null)
  const [offlineArchiveCount, setOfflineArchiveCount] = useState(0)

  const refresh = useCallback(async () => {
    try {
      const [nextAuth, nextCapabilities] = await Promise.all([api.auth.status(), api.capabilities()])
      setAuth(nextAuth)
      setCapabilities(platform.applyCapabilities(nextCapabilities))
      setConnectionDown(false)
    } catch {
      setConnectionDown(true)
    }
  }, [platform])

  useEffect(() => {
    let active = true
    void platform.getConnection().then((nextConnection) => {
      if (!active) return
      setConnection(nextConnection)
      if (nextConnection.configured) void refresh()
    })
    void platform.getNetworkStatus().then((connected) => {
      if (active && !connected) setConnectionDown(true)
    })
    if (platform.isNative) {
      void platform.getOfflineSyncStatus().then((status) => {
        if (active) setOfflineArchiveCount(status.archiveCount)
      })
    }
    const handleOnline = () => void refresh()
    const handleOffline = () => setConnectionDown(true)
    const handleApiError = (event: Event) => {
      const status = (event as CustomEvent<{ status?: number }>).detail?.status
      if (status === 401) setAuth((current) => current ? { ...current, authenticated: false } : current)
    }
    const handleOfflineLibrary = (event: Event) => {
      const detail = (event as CustomEvent<{ archiveCount?: number }>).detail
      if (typeof detail?.archiveCount === "number") setOfflineArchiveCount(detail.archiveCount)
      else void platform.getOfflineSyncStatus().then((status) => setOfflineArchiveCount(status.archiveCount))
    }
    window.addEventListener("online", handleOnline)
    window.addEventListener("offline", handleOffline)
    window.addEventListener("mpp:offline", handleOffline)
    window.addEventListener("mpp:api-error", handleApiError)
    window.addEventListener("mpp:capabilities-change", handleOnline)
    window.addEventListener("mpp:app-resume", handleOnline)
    window.addEventListener("mpp:connection-change", handleOnline)
    window.addEventListener("mpp:offline-sync-change", handleOfflineLibrary)
    window.addEventListener("mpp:offline-library-change", handleOfflineLibrary)
    return () => {
      active = false
      window.removeEventListener("online", handleOnline)
      window.removeEventListener("offline", handleOffline)
      window.removeEventListener("mpp:offline", handleOffline)
      window.removeEventListener("mpp:api-error", handleApiError)
      window.removeEventListener("mpp:capabilities-change", handleOnline)
      window.removeEventListener("mpp:app-resume", handleOnline)
      window.removeEventListener("mpp:connection-change", handleOnline)
      window.removeEventListener("mpp:offline-sync-change", handleOfflineLibrary)
      window.removeEventListener("mpp:offline-library-change", handleOfflineLibrary)
    }
  }, [platform, refresh])

  const handleNativeConnected = async () => {
    setConnection(await platform.getConnection())
    setEditingConnection(false)
    await refresh()
  }

  const unlock = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setUnlocking(true)
    setUnlockError(null)
    try {
      const nextAuth = await api.auth.unlock(unlockToken)
      const nextCapabilities = await api.capabilities()
      setAuth(nextAuth)
      setCapabilities(platform.applyCapabilities(nextCapabilities))
      setUnlockToken("")
    } catch (error) {
      setUnlockError(error instanceof Error ? error.message : String(error))
    } finally {
      setUnlocking(false)
    }
  }

  if (!connection) {
    return (
      <AppShell activePage={route.page} runtimeControls={false}>
        <LoadingState title="正在准备 MPP" className="h-full" />
      </AppShell>
    )
  }

  if (platform.isNative && (!connection.configured || editingConnection || (auth && !auth.authenticated && offlineArchiveCount === 0))) {
    return (
      <AppShell activePage={route.page} runtimeControls={false}>
        <NativeConnectionScreen serverUrl={connection.serverUrl} onConnected={handleNativeConnected} />
      </AppShell>
    )
  }

  if (connectionDown) {
    if (platform.isNative && offlineArchiveCount > 0) {
      return (
        <AppAccessContext.Provider value={{
          auth: auth ?? { required: true, authenticated: true, mode: "remote" },
          capabilities: OFFLINE_CAPABILITIES,
          refresh,
          online: false,
          authExpired: Boolean(auth && !auth.authenticated),
        }}>
          <div className="relative h-full">
            <div className="pointer-events-none absolute inset-x-0 top-0 z-50 flex justify-center px-3 pt-[max(0.5rem,env(safe-area-inset-top))]">
              <div className="pointer-events-auto flex items-center gap-2 rounded-full border border-amber-500/30 bg-background/95 px-3 py-1.5 text-xs shadow-sm backdrop-blur">
                <span>{auth && !auth.authenticated ? "访问令牌已失效，正在使用离线资料" : "离线资料模式"}</span>
                <Button className="h-7 rounded-full px-2.5 text-xs" variant="outline" onClick={() => setEditingConnection(true)}>
                  连接设置
                </Button>
              </div>
            </div>
            {children}
          </div>
        </AppAccessContext.Provider>
      )
    }
    return (
      <AppShell activePage={route.page} runtimeControls={false}>
        <OfflineState
          title="当前处于离线状态"
          description="应用外壳可以继续打开；连接恢复后即可读取归档和操作任务。"
          action={(
            <div className="flex gap-2">
              <Button className="h-11" variant="outline" onClick={() => void refresh()}>重新连接</Button>
              {platform.isNative && (
                <Button className="h-11" onClick={() => setEditingConnection(true)}>连接设置</Button>
              )}
            </div>
          )}
          className="h-full"
        />
      </AppShell>
    )
  }

  if (!auth || !capabilities) {
    return (
      <AppShell activePage={route.page} runtimeControls={false}>
        <LoadingState title="正在连接 MPP 服务" className="h-full" />
      </AppShell>
    )
  }

  if (!auth.authenticated) {
    if (platform.isNative && offlineArchiveCount > 0) {
      return (
        <AppAccessContext.Provider value={{ auth, capabilities: OFFLINE_CAPABILITIES, refresh, online: false, authExpired: true }}>
          <div className="relative h-full">
            <div className="pointer-events-none absolute inset-x-0 top-0 z-50 flex justify-center px-3 pt-[max(0.5rem,env(safe-area-inset-top))]">
              <div className="pointer-events-auto flex items-center gap-2 rounded-full border border-amber-500/30 bg-background/95 px-3 py-1.5 text-xs shadow-sm backdrop-blur">
                <span>访问令牌已失效，正在使用离线资料</span>
                <Button className="h-7 rounded-full px-2.5 text-xs" variant="outline" onClick={() => setEditingConnection(true)}>
                  连接设置
                </Button>
              </div>
            </div>
            {children}
          </div>
        </AppAccessContext.Provider>
      )
    }
    return (
      <AppShell activePage={route.page} runtimeControls={false}>
        <UnauthorizedState
          title="输入访问令牌"
          description="服务器已启用访问保护，解锁后可继续使用任务、归档和设置。"
          action={(
            <form className="w-[min(22rem,calc(100vw-3rem))] space-y-2" onSubmit={unlock}>
              <Input
                className="h-11"
                type="password"
                value={unlockToken}
                onChange={(event) => setUnlockToken(event.target.value)}
                autoComplete="current-password"
                aria-label="访问令牌"
                placeholder="输入 API Token"
                required
              />
              {unlockError && <p className="text-sm text-destructive" role="alert">{unlockError}</p>}
              <Button className="h-11 w-full" type="submit" disabled={unlocking}>
                {unlocking ? "正在解锁…" : "解锁"}
              </Button>
            </form>
          )}
          className="h-full"
        />
      </AppShell>
    )
  }

  return (
    <AppAccessContext.Provider value={{ auth, capabilities, refresh, online: true, authExpired: false }}>
      {children}
    </AppAccessContext.Provider>
  )
}
