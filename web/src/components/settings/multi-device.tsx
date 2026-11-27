import { useCallback, useEffect, useState } from "react"
import { api, type ExecutionNodesResponse } from "@/lib/api"
import type { RuntimeSettings } from "@/lib/settings-schema"
import { libraryChanged } from "@/hooks/use-library-directory"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { SettingRow } from "./setting-controls"

interface MultiDeviceSettingsProps {
  settings: RuntimeSettings
  updateSetting: (key: string, value: unknown) => Promise<void>
  saving: Record<string, boolean>
  saved: Record<string, boolean>
  mobile: boolean
}

const synchronizationSwitches = [
  { key: "remote_sync_enabled", label: "启用归档同步", defaultValue: false },
  { key: "remote_sync_upload_results", label: "上传处理结果", defaultValue: true },
  { key: "remote_sync_download_results", label: "下载文本归档", defaultValue: true },
] satisfies Array<{ key: keyof RuntimeSettings; label: string; defaultValue: boolean }>

export function MultiDeviceSettings({ settings, updateSetting, saving, saved, mobile }: MultiDeviceSettingsProps) {
  const [registry, setRegistry] = useState<ExecutionNodesResponse | null>(null)
  const [nodeError, setNodeError] = useState("")
  const [pulling, setPulling] = useState(false)
  const [pushing, setPushing] = useState(false)
  const [pullMessage, setPullMessage] = useState("")
  const loadNodes = useCallback(async () => {
    try {
      setRegistry(await api.execution.nodes())
      setNodeError("")
    } catch (error) {
      setNodeError(error instanceof Error ? error.message : "无法读取算力节点")
    }
  }, [])

  useEffect(() => {
    void loadNodes()
    const timer = window.setInterval(() => void loadNodes(), 15000)
    return () => window.clearInterval(timer)
  }, [loadNodes])

  const pull = async () => {
    setPulling(true)
    setPullMessage("")
    try {
      const result = await api.sync.pull()
      setPullMessage(result.enabled
        ? `同步完成：更新 ${result.upserted} 项，删除 ${result.deleted} 项，跳过 ${result.skipped} 项。`
        : result.reason === "self_workspace"
          ? "远程地址指向当前资料库，已跳过同步。"
          : "请先启用归档同步、下载文本归档，并填写远程服务器地址。")
      libraryChanged()
    } catch (error) {
      setPullMessage(error instanceof Error ? error.message : "拉取同步失败")
    } finally {
      setPulling(false)
    }
  }
  const push = async () => {
    setPushing(true)
    setPullMessage("")
    try {
      const result = await api.sync.push()
      if (!result.enabled) {
        setPullMessage("请先启用归档同步并填写远程服务器地址。")
      } else if (result.uploaded === 0 && !result.library_pushed) {
        setPullMessage("推送完成：当前没有待推送的归档或文件夹变更。")
      } else {
        setPullMessage(
          `推送完成：上传 ${result.uploaded} 项归档${result.library_pushed ? "，文件夹设置已同步" : ""}。`,
        )
      }
      libraryChanged()
    } catch (error) {
      setPullMessage(error instanceof Error ? error.message : "推送同步失败")
    } finally {
      setPushing(false)
    }
  }
  const fieldProps = { onSave: updateSetting, saving, saved }
  const toggle = (key: string, label: string, checked: boolean) => (
    <div className="flex items-center justify-between gap-3" key={key}>
      <Label htmlFor={`multi-device-${key}`} className="text-sm">{label}</Label>
      <Switch id={`multi-device-${key}`} checked={checked} disabled={saving[key]} onCheckedChange={(value) => void updateSetting(key, value)} />
    </div>
  )
  const preferred = settings.remote_preferred_worker_id || settings.remote_worker_id || ""
  const nodes = registry?.nodes ?? []

  return <div className="space-y-4">
    {!mobile && <Card>
      <CardHeader><CardTitle className="text-base">远程服务器与本机节点</CardTitle></CardHeader>
      <CardContent className="space-y-4">
        <SettingRow {...fieldProps} label="服务器地址" settingKey="remote_server_url" value={settings.remote_server_url ?? ""} placeholder="https://mpp.example.com" />
        <SettingRow {...fieldProps} label="服务器 Token" settingKey="remote_api_token" value={settings.remote_api_token ?? ""} masked />
        <SettingRow {...fieldProps} label="本机节点 ID" settingKey="remote_worker_id" value={settings.remote_worker_id ?? ""} />
        <SettingRow {...fieldProps} label="本机节点名称" settingKey="remote_worker_name" value={settings.remote_worker_name ?? ""} />
        <p className="text-xs text-muted-foreground">电脑用节点 ID 标识本机。协调服务器填写节点 ID 后，只允许同一 ID 的电脑注册。接入多台电脑时，协调服务器必须将节点 ID 留空，并在下方选择默认优先节点。</p>
        {synchronizationSwitches.map(({ key, label, defaultValue }) => toggle(key, label, Boolean(settings[key] ?? defaultValue)))}
        {toggle("remote_execution_worker_enabled", "本机领取远程任务", Boolean(settings.remote_execution_worker_enabled))}
        <p className="text-xs text-muted-foreground">默认同步文本、封面和图文配图。归档拉取不下载原始音视频。</p>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" disabled={pulling || pushing} onClick={() => void pull()}>{pulling ? "拉取中…" : "立即拉取同步"}</Button>
          <Button variant="outline" disabled={pulling || pushing} onClick={() => void push()}>{pushing ? "推送中…" : "立即推送同步"}</Button>
        </div>
        {pullMessage && <p role="status" className="text-sm text-muted-foreground">{pullMessage}</p>}
      </CardContent>
    </Card>}
    <Card>
      <CardHeader><CardTitle className="text-base">服务器任务路由</CardTitle></CardHeader>
      <CardContent className="space-y-4">
        {toggle("remote_execution_enabled", "允许远程节点执行任务", Boolean(settings.remote_execution_enabled))}
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <Label htmlFor="multi-device-preferred-worker">默认优先节点</Label>
          <select id="multi-device-preferred-worker" className="h-9 max-w-full rounded-md border bg-background px-2" value={preferred} disabled={saving.remote_preferred_worker_id} onChange={(event) => void updateSetting("remote_preferred_worker_id", event.target.value)}>
            <option value="">任意空闲节点</option>
            <option value="local">服务器本机</option>
            {preferred && preferred !== "local" && !nodes.some((node) => node.node_id === preferred) && <option value={preferred}>{preferred}（未注册）</option>}
            {nodes.filter((node) => node.node_id !== "local").map((node) => <option key={node.node_id} value={node.node_id}>{node.display_name || node.node_id}{node.online ? "" : "（离线）"}</option>)}
          </select>
        </div>
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <Label htmlFor="multi-device-offline-policy">目标节点离线时</Label>
          <select id="multi-device-offline-policy" className="h-9 max-w-full rounded-md border bg-background px-2" value={settings.remote_execution_offline_policy ?? "fallback_server"} disabled={saving.remote_execution_offline_policy} onChange={(event) => void updateSetting("remote_execution_offline_policy", event.target.value)}>
            <option value="fallback_server">超时后由服务器执行</option>
            <option value="wait">持续等待目标节点</option>
            <option value="fallback_any_online">超时后由其他在线节点执行</option>
          </select>
        </div>
      </CardContent>
    </Card>
    <Card>
      <CardHeader className="flex-row items-center justify-between gap-2"><CardTitle className="text-base">算力节点</CardTitle><Button variant="ghost" size="sm" onClick={() => void loadNodes()}>刷新节点</Button></CardHeader>
      <CardContent>
        {nodeError && <p role="alert" className="text-sm text-destructive">{nodeError}</p>}
        {!registry && !nodeError && <p className="text-sm text-muted-foreground">读取节点中…</p>}
        <ul className="divide-y" aria-label="已注册算力节点">{nodes.map((node) => <li key={node.node_id} className="space-y-1 py-3 first:pt-0 last:pb-0">
          <div className="flex flex-wrap items-center justify-between gap-2 text-sm"><span className="font-medium">{node.display_name || node.node_id}{node.node_id === "local" ? "（服务器本机）" : ""}</span><span>{node.online ? node.busy ? "在线 · 忙碌" : "在线 · 空闲" : "离线"}</span></div>
          <p className="break-all text-xs text-muted-foreground">{node.node_id} · {node.platform} · {node.compute_devices.map((device) => device.toUpperCase()).join(" / ")}</p>
        </li>)}</ul>
      </CardContent>
    </Card>
  </div>
}
