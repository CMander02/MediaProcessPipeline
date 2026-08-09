import { useEffect, useState, type FormEvent } from "react"
import { LinkSquare02Icon, ServerStack01Icon } from "@hugeicons/core-free-icons"
import { HugeiconsIcon } from "@hugeicons/react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useAppAccess } from "@/hooks/use-app-access-context"
import type { ConnectionCheck } from "@/platform"
import { usePlatform } from "@/platform/use-platform"

interface NativeConnectionFormProps {
  initialServerUrl?: string
  submitLabel?: string
  onConnected: (connection: ConnectionCheck) => void | Promise<void>
  onCancel?: () => void
}

export function NativeConnectionForm({
  initialServerUrl = "",
  submitLabel = "连接并保存",
  onConnected,
  onCancel,
}: NativeConnectionFormProps) {
  const platform = usePlatform()
  const [serverUrl, setServerUrl] = useState(initialServerUrl)
  const [token, setToken] = useState("")
  const [connecting, setConnecting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => setServerUrl(initialServerUrl), [initialServerUrl])

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setConnecting(true)
    setError(null)
    try {
      const connection = await platform.connect({ serverUrl, token })
      setToken("")
      await onConnected(connection)
    } catch (connectError) {
      setError(connectError instanceof Error ? connectError.message : String(connectError))
    } finally {
      setConnecting(false)
    }
  }

  return (
    <form className="space-y-4" onSubmit={submit}>
      <div className="space-y-2">
        <Label htmlFor="native-server-url">服务器地址</Label>
        <Input
          id="native-server-url"
          value={serverUrl}
          onChange={(event) => setServerUrl(event.target.value)}
          inputMode="url"
          autoCapitalize="none"
          autoCorrect="off"
          autoComplete="url"
          placeholder="https://mpp.example.com"
          required
        />
        <p className="text-xs leading-5 text-muted-foreground">
          局域网调试可填写 http://192.168.x.x:18000；ADB 反向连接填写 http://localhost:18000。
        </p>
      </div>
      <div className="space-y-2">
        <Label htmlFor="native-api-token">API Token</Label>
        <Input
          id="native-api-token"
          type="password"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          autoComplete="current-password"
          placeholder="输入服务器 API Token"
        />
        <p className="text-xs text-muted-foreground">Token 由 Android Keystore 加密保护。</p>
      </div>
      {error && (
        <p className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive" role="alert">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        {onCancel && (
          <Button className="h-11 flex-1" type="button" variant="outline" onClick={onCancel} disabled={connecting}>
            取消
          </Button>
        )}
        <Button className="h-11 flex-1" type="submit" disabled={connecting}>
          {connecting ? "正在验证…" : submitLabel}
        </Button>
      </div>
    </form>
  )
}

export function NativeConnectionScreen({
  serverUrl,
  onConnected,
}: {
  serverUrl?: string
  onConnected: (connection: ConnectionCheck) => void | Promise<void>
}) {
  return (
    <div className="flex h-full items-center justify-center overflow-y-auto px-4 py-8">
      <Card className="w-full max-w-md shadow-none">
        <CardHeader className="space-y-3 text-center">
          <div className="mx-auto flex size-12 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <HugeiconsIcon icon={ServerStack01Icon} className="size-6" />
          </div>
          <div>
            <CardTitle>连接 MPP 服务</CardTitle>
            <p className="mt-1 text-sm leading-6 text-muted-foreground">验证服务地址和访问令牌后进入统一工作台。</p>
          </div>
        </CardHeader>
        <CardContent>
          <NativeConnectionForm initialServerUrl={serverUrl} onConnected={onConnected} />
        </CardContent>
      </Card>
    </div>
  )
}

export function NativeConnectionSettings() {
  const platform = usePlatform()
  if (!platform.isNative) return null
  return <NativeConnectionSettingsContent />
}

function NativeConnectionSettingsContent() {
  const platform = usePlatform()
  const { refresh, online, authExpired } = useAppAccess()
  const [serverUrl, setServerUrl] = useState("")
  const [editing, setEditing] = useState(false)

  useEffect(() => {
    if (!platform.isNative) return
    void platform.getConnection().then((connection) => setServerUrl(connection.serverUrl))
  }, [platform])

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <HugeiconsIcon icon={LinkSquare02Icon} className="size-4" />
          服务器连接
        </CardTitle>
      </CardHeader>
      <CardContent>
        {editing ? (
          <NativeConnectionForm
            initialServerUrl={serverUrl}
            submitLabel="验证并更新"
            onCancel={() => setEditing(false)}
            onConnected={async (connection) => {
              setServerUrl(connection.serverUrl)
              setEditing(false)
              await refresh()
            }}
          />
        ) : (
          <div className="flex items-center justify-between gap-3">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{serverUrl}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {authExpired ? "访问令牌需要更新" : online ? "在线服务已连接" : "服务器暂时不可达"}
              </p>
            </div>
            <Button className="h-11 shrink-0 md:h-9" type="button" variant="outline" onClick={() => setEditing(true)}>
              更换服务器
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
