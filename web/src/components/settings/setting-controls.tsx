import { useEffect, useState, type ReactNode } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { FloppyDiskIcon, FolderOpenIcon, Tick02Icon } from "@hugeicons/core-free-icons"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { selectDirectory } from "@/lib/tauri"

export type DeviceValue = "auto" | "cuda" | "cpu"

export interface SettingsSectionProps {
  title: ReactNode
  description?: ReactNode
  children: ReactNode
}

export function SettingsSection({ title, description, children }: SettingsSectionProps) {
  return (
    <section className="grid gap-4 border-b border-border/70 py-5 first:pt-0 last:border-b-0 last:pb-0 lg:grid-cols-[220px_minmax(0,1fr)]">
      <div className="space-y-1">
        <h3 className="text-base font-semibold text-foreground">{title}</h3>
        {description && (
          <p className="text-xs leading-5 text-muted-foreground">{description}</p>
        )}
      </div>
      <div className="min-w-0 space-y-3">{children}</div>
    </section>
  )
}

export interface SettingRowProps {
  label: ReactNode
  settingKey: string
  value: string
  onSave: (key: string, value: unknown) => Promise<void>
  saving: Record<string, boolean>
  saved: Record<string, boolean>
  masked?: boolean
  placeholder?: string
}

type ProxyMode = "system" | "none" | "custom"

const DISABLED_PROXY_VALUES = new Set(["direct", "none", "off", "false", "0"])

function proxyMode(value: string): ProxyMode {
  const normalized = value.trim().toLowerCase()
  if (!normalized) return "system"
  if (DISABLED_PROXY_VALUES.has(normalized)) return "none"
  return "custom"
}

export function ProxySetting({
  label,
  settingKey,
  value,
  onSave,
  saving,
  saved,
}: SettingRowProps) {
  const persistedMode = proxyMode(value)
  const [mode, setMode] = useState<ProxyMode>(persistedMode)
  const [customValue, setCustomValue] = useState(persistedMode === "custom" ? value : "")

  useEffect(() => {
    const nextMode = proxyMode(value)
    setMode(nextMode)
    if (nextMode === "custom") setCustomValue(value)
  }, [value])

  const saveMode = (nextMode: ProxyMode) => {
    setMode(nextMode)
    if (nextMode === "system") void onSave(settingKey, "")
    if (nextMode === "none") void onSave(settingKey, "direct")
  }

  const customDirty = mode === "custom" && customValue.trim() !== value.trim()
  const saveCustom = () => {
    const normalized = customValue.trim()
    if (normalized) void onSave(settingKey, normalized)
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3">
        <Label className="w-24 shrink-0 text-sm text-muted-foreground">{label}</Label>
        <select
          aria-label={`${String(label)}模式`}
          value={mode}
          onChange={(event) => saveMode(event.target.value as ProxyMode)}
          className="h-8 rounded-md border border-input bg-background px-2 text-sm"
        >
          <option value="system">系统代理</option>
          <option value="none">无代理</option>
          <option value="custom">自定义</option>
        </select>
        {saving[settingKey] && <span className="text-xs text-muted-foreground">保存中</span>}
        {!saving[settingKey] && saved[settingKey] && (
          <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5 text-emerald-500" />
        )}
      </div>
      {mode === "custom" && (
        <div className="flex items-center gap-3 pl-[6.75rem]">
          <Input
            aria-label={`${String(label)}地址`}
            value={customValue}
            onChange={(event) => setCustomValue(event.target.value)}
            onKeyDown={(event) => event.key === "Enter" && saveCustom()}
            className="h-8 flex-1 text-sm"
            autoComplete="off"
            placeholder="http://localhost:7897"
          />
          {customDirty && (
            <Button
              size="sm"
              variant="ghost"
              onClick={saveCustom}
              disabled={saving[settingKey] || !customValue.trim()}
              className="h-8 px-2"
              aria-label={`保存${String(label)}地址`}
            >
              <HugeiconsIcon icon={FloppyDiskIcon} className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
      )}
      <p className="pl-[6.75rem] text-xs text-muted-foreground">
        系统代理读取环境变量和操作系统设置；自定义地址支持 HTTP、HTTPS 和 SOCKS。
      </p>
    </div>
  )
}

export function DeviceChoice({
  value,
  onChange,
  labels = { auto: "自动", cuda: "CUDA", cpu: "内存" },
  options = ["cuda", "cpu"],
}: {
  value: string
  onChange: (value: DeviceValue) => void
  labels?: Record<DeviceValue, string>
  options?: DeviceValue[]
}) {
  const fallback = options[0] ?? "cuda"
  const current: DeviceValue = options.includes(value as DeviceValue) ? value as DeviceValue : fallback
  return (
    <div className="flex items-center gap-3">
      <Label className="w-24 shrink-0 text-sm text-muted-foreground">设备</Label>
      <div className="flex items-center gap-1">
        {options.map((device) => (
          <button
            key={device}
            type="button"
            onClick={() => onChange(device)}
            className={[
              "h-8 px-3 text-sm transition-colors",
              current === device
                ? "text-primary font-medium border-b-2 border-primary"
                : "text-muted-foreground hover:text-foreground",
            ].join(" ")}
          >
            {labels[device]}
          </button>
        ))}
      </div>
    </div>
  )
}

export function PathPickerRow({
  label,
  settingKey,
  value,
  onSave,
  saving,
  saved,
  placeholder,
  title,
}: SettingRowProps & { title?: string }) {
  const [editValue, setEditValue] = useState(value)

  useEffect(() => {
    setEditValue(value)
  }, [value])

  const pickDirectory = async () => {
    try {
      const selected = await selectDirectory({
        title: title ?? "选择模型文件夹",
        defaultPath: editValue || undefined,
      })
      if (selected) {
        setEditValue(selected)
        await onSave(settingKey, selected)
        return
      }
      if (selected === null) return
    } catch (error) {
      console.warn("Directory picker unavailable; falling back to manual path input", error)
    }

    const manual = window.prompt("输入文件夹路径", editValue)
    if (manual !== null) {
      setEditValue(manual)
      await onSave(settingKey, manual)
    }
  }

  const isDirty = editValue !== value
  const isSaving = saving[settingKey]
  const isSaved = saved[settingKey]

  return (
    <div className="flex items-center gap-3">
      <Label className="w-24 shrink-0 text-sm text-muted-foreground">{label}</Label>
      <Input
        value={editValue}
        onChange={(event) => setEditValue(event.target.value)}
        onKeyDown={(event) => event.key === "Enter" && onSave(settingKey, editValue)}
        className="h-8 flex-1 text-sm"
        autoComplete="off"
        placeholder={placeholder}
      />
      <Button size="sm" variant="ghost" onClick={pickDirectory} className="h-8 gap-1.5 px-2">
        <HugeiconsIcon icon={FolderOpenIcon} className="h-3.5 w-3.5" />
        选择
      </Button>
      {isDirty && (
        <Button
          size="sm"
          variant="ghost"
          onClick={() => onSave(settingKey, editValue)}
          disabled={isSaving}
          className="h-8 px-2"
        >
          {isSaved ? (
            <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5" />
          ) : (
            <HugeiconsIcon icon={FloppyDiskIcon} className="h-3.5 w-3.5" />
          )}
        </Button>
      )}
      {!isDirty && isSaved && (
        <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5 text-emerald-500" />
      )}
    </div>
  )
}

export function SettingRow({
  label,
  settingKey,
  value,
  onSave,
  saving,
  saved,
  masked,
  placeholder,
}: SettingRowProps) {
  const [editValue, setEditValue] = useState(value)

  useEffect(() => {
    setEditValue(value)
  }, [value])

  const isDirty = editValue !== value
  const isSaving = saving[settingKey]
  const isSaved = saved[settingKey]

  const handleSave = () => {
    if (!isDirty) return
    onSave(settingKey, editValue)
  }

  return (
    <div className="flex items-center gap-3">
      <Label className="w-24 shrink-0 text-sm text-muted-foreground">{label}</Label>
      <Input
        type={masked ? "password" : "text"}
        value={editValue}
        onChange={(event) => setEditValue(event.target.value)}
        onKeyDown={(event) => event.key === "Enter" && handleSave()}
        className="flex-1 h-8 text-sm"
        autoComplete="off"
        placeholder={placeholder}
      />
      {isDirty && (
        <Button
          size="sm"
          variant="ghost"
          onClick={handleSave}
          disabled={isSaving}
          className="h-8 px-2"
        >
          {isSaved ? (
            <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5" />
          ) : (
            <HugeiconsIcon icon={FloppyDiskIcon} className="h-3.5 w-3.5" />
          )}
        </Button>
      )}
      {!isDirty && isSaved && (
        <HugeiconsIcon icon={Tick02Icon} className="h-3.5 w-3.5 text-emerald-500" />
      )}
    </div>
  )
}
