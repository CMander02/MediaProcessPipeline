import { useEffect, useState, type ReactNode } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { FloppyDiskIcon, FolderOpenIcon, Tick02Icon } from "@hugeicons/core-free-icons"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { getDialogBridge } from "@/lib/electron"

export type DeviceValue = "cuda" | "cpu"

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

export function DeviceChoice({
  value,
  onChange,
  labels = { cuda: "CUDA", cpu: "内存" },
}: {
  value: string
  onChange: (value: DeviceValue) => void
  labels?: Record<DeviceValue, string>
}) {
  const current: DeviceValue = value === "cpu" ? "cpu" : "cuda"
  return (
    <div className="flex items-center gap-3">
      <Label className="w-24 shrink-0 text-sm text-muted-foreground">设备</Label>
      <div className="flex items-center gap-1">
        {(["cuda", "cpu"] as const).map((device) => (
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
    const selected = await getDialogBridge()?.selectDirectory({
      title: title ?? "选择模型文件夹",
      defaultPath: editValue || undefined,
    })
    if (selected) {
      setEditValue(selected)
      await onSave(settingKey, selected)
      return
    }
    if (!getDialogBridge()) {
      const manual = window.prompt("输入文件夹路径", editValue)
      if (manual !== null) {
        setEditValue(manual)
        await onSave(settingKey, manual)
      }
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
