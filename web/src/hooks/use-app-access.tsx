import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react"

import { AppShell } from "@/components/app-shell/app-shell"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { LoadingState, OfflineState, UnauthorizedState } from "@/components/ui/page-state"
import { AppAccessContext } from "@/hooks/use-app-access-context"
import { api, type AuthStatus, type Capabilities } from "@/lib/api"
import { useRoute } from "@/lib/router"

export function AppAccessBoundary({ children }: { children: ReactNode }) {
  const route = useRoute()
  const [auth, setAuth] = useState<AuthStatus | null>(null)
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null)
  const [connectionDown, setConnectionDown] = useState(() => typeof navigator !== "undefined" && !navigator.onLine)
  const [unlockToken, setUnlockToken] = useState("")
  const [unlocking, setUnlocking] = useState(false)
  const [unlockError, setUnlockError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [nextAuth, nextCapabilities] = await Promise.all([api.auth.status(), api.capabilities()])
      setAuth(nextAuth)
      setCapabilities(nextCapabilities)
      setConnectionDown(false)
    } catch {
      setConnectionDown(true)
    }
  }, [])

  useEffect(() => {
    void refresh()
    const handleOnline = () => void refresh()
    const handleOffline = () => setConnectionDown(true)
    const handleApiError = (event: Event) => {
      const status = (event as CustomEvent<{ status?: number }>).detail?.status
      if (status === 401) setAuth((current) => current ? { ...current, authenticated: false } : current)
    }
    window.addEventListener("online", handleOnline)
    window.addEventListener("offline", handleOffline)
    window.addEventListener("mpp:offline", handleOffline)
    window.addEventListener("mpp:api-error", handleApiError)
    window.addEventListener("mpp:capabilities-change", handleOnline)
    return () => {
      window.removeEventListener("online", handleOnline)
      window.removeEventListener("offline", handleOffline)
      window.removeEventListener("mpp:offline", handleOffline)
      window.removeEventListener("mpp:api-error", handleApiError)
      window.removeEventListener("mpp:capabilities-change", handleOnline)
    }
  }, [refresh])

  const unlock = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setUnlocking(true)
    setUnlockError(null)
    try {
      const nextAuth = await api.auth.unlock(unlockToken)
      const nextCapabilities = await api.capabilities()
      setAuth(nextAuth)
      setCapabilities(nextCapabilities)
      setUnlockToken("")
    } catch (error) {
      setUnlockError(error instanceof Error ? error.message : String(error))
    } finally {
      setUnlocking(false)
    }
  }

  if (connectionDown) {
    return (
      <AppShell activePage={route.page} runtimeControls={false}>
        <OfflineState
          title="当前处于离线状态"
          description="应用外壳可以继续打开；连接恢复后即可读取归档和操作任务。"
          action={<Button className="h-11" variant="outline" onClick={() => void refresh()}>重新连接</Button>}
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
    <AppAccessContext.Provider value={{ auth, capabilities, refresh }}>
      {children}
    </AppAccessContext.Provider>
  )
}
