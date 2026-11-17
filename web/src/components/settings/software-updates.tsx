import { useEffect, useId, useRef, useState } from "react"
import { api } from "@/lib/api"
import type { UpdateCheckResponse, UpdateDownloadResponse } from "@/lib/software-update"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { usePlatform } from "@/platform/use-platform"

type UpdateState =
  | { kind: "idle" }
  | { kind: "checking" }
  | { kind: "ready"; release: UpdateCheckResponse }
  | { kind: "downloading"; release: UpdateCheckResponse }
  | { kind: "downloaded"; release: UpdateCheckResponse; download: UpdateDownloadResponse }
  | { kind: "error"; release: UpdateCheckResponse | null; message: string }

export function SoftwareUpdates({ mobile }: { mobile: boolean }) {
  const platform = usePlatform()
  const [version, setVersion] = useState("读取中")
  const [state, setState] = useState<UpdateState>({ kind: "idle" })
  const [assetName, setAssetName] = useState("")
  const operation = useRef(false)
  const assetId = useId()
  const release = "release" in state ? state.release : null
  const busy = state.kind === "checking" || state.kind === "downloading"
  const externalDownload = mobile || platform.isNative

  useEffect(() => {
    let active = true
    api.health().then((health) => {
      if (active) setVersion(health.version || "未提供版本号")
    }).catch(() => {
      if (active) setVersion("无法读取当前版本")
    })
    return () => { active = false }
  }, [])

  const check = async () => {
    if (operation.current) return
    operation.current = true
    setState({ kind: "checking" })
    try {
      const result = await api.updates.check(true)
      setVersion(result.current_version)
      setAssetName(result.assets.find((asset) => asset.recommended && asset.name !== "SHA256SUMS.txt")?.name || "")
      setState({ kind: "ready", release: result })
    } catch (error) {
      setState({ kind: "error", release: null, message: error instanceof Error ? error.message : "检查更新失败" })
    } finally {
      operation.current = false
    }
  }

  const download = async () => {
    if (!release || !assetName || operation.current) return
    operation.current = true
    setState({ kind: "downloading", release })
    try {
      const result = await api.updates.download(assetName)
      setState({ kind: "downloaded", release, download: result })
    } catch (error) {
      setState({ kind: "error", release, message: error instanceof Error ? error.message : "下载更新失败" })
    } finally {
      operation.current = false
    }
  }

  const openRelease = async (url: string) => {
    try {
      await platform.openExternal(url)
    } catch (error) {
      setState({ kind: "error", release, message: error instanceof Error ? error.message : "无法打开 GitHub Release" })
    }
  }

  const packages = release?.assets.filter((asset) => asset.name !== "SHA256SUMS.txt") ?? []
  return <Card>
    <CardHeader><CardTitle className="text-base">软件更新</CardTitle></CardHeader>
    <CardContent className="space-y-4">
      <p className="text-sm">当前后端版本：{version}</p>
      <Button variant="outline" disabled={busy} onClick={() => void check()}>{state.kind === "checking" ? "检查中…" : "检查更新"}</Button>
      {state.kind === "error" && <p role="alert" className="text-sm text-destructive">{state.message}</p>}
      {release && <div className="space-y-4">
        <p role="status" className="text-sm">{release.update_available ? `发现新版本：${release.latest_version}` : `当前已是最新版本：${release.current_version}`}</p>
        {release.published_at && <p className="break-all text-xs text-muted-foreground">发布时间：{release.published_at}</p>}
        <div className="space-y-2">
          <h3 className="text-sm font-medium">更新日志</h3>
          <pre className="max-h-64 overflow-y-auto whitespace-pre-wrap break-words rounded-md border p-3 font-sans text-sm">{release.notes || "此版本未提供更新日志。"}</pre>
        </div>
        {release.update_available && !externalDownload && packages.length > 0 && <div className="space-y-3">
          <Label htmlFor={assetId}>更新包</Label>
          <select id={assetId} value={assetName} disabled={busy} className="h-9 w-full min-w-0 rounded-md border bg-background px-2 text-sm" onChange={(event) => {
            setAssetName(event.target.value)
            setState({ kind: "ready", release })
          }}>
            <option value="">请选择更新包</option>
            {packages.map((asset) => <option key={asset.name} value={asset.name}>{asset.recommended ? "推荐 · " : ""}{asset.name}</option>)}
          </select>
          <p className="text-xs text-muted-foreground">更新包下载至后端所在设备，通过 SHA-256 校验后显示文件路径和安装步骤。</p>
          <Button disabled={busy || !assetName} onClick={() => void download()}>{state.kind === "downloading" ? "下载并校验中…" : "下载并校验更新包"}</Button>
        </div>}
        <Button variant="link" asChild><a href={release.release_url} target="_blank" rel="noopener noreferrer" onClick={(event) => {
          event.preventDefault()
          void openRelease(release.release_url)
        }}>前往 GitHub Release 下载</a></Button>
      </div>}
      {state.kind === "downloaded" && <div role="status" className="space-y-2 rounded-md border p-3 text-sm">
        <p>更新包已通过 SHA-256 校验。</p>
        <p className="break-all">文件路径：{state.download.path}</p>
        <p className="break-all text-xs text-muted-foreground">SHA-256：{state.download.sha256}</p>
        <p className="whitespace-pre-wrap">{state.download.instructions}</p>
      </div>}
    </CardContent>
  </Card>
}
