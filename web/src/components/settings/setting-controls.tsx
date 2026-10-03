import { Children, isValidElement, useState, type ReactNode } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { FloppyDiskIcon, FolderOpenIcon, Tick02Icon } from "@hugeicons/core-free-icons"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { cn } from "@/lib/utils"
import { describeProxy, proxyMode, type ProxyMode } from "@/lib/proxy"

export type DeviceValue = "auto" | "cuda" | "cpu"

// Radix selects reserve the empty string; options that mean "none" use it all the same.
const EMPTY_OPTION = "__empty__"

function optionItems(children: ReactNode): Array<{ value: string; label: ReactNode }> {
  return Children.toArray(children).flatMap((child) => {
    if (!isValidElement<{ value?: string | number; children?: ReactNode }>(child) || child.type !== "option") return []
    return [{ value: String(child.props.value ?? ""), label: child.props.children }]
  })
}

/**
 * The shadcn select, written like a native one with <option> children, and as wide as its
 * choices rather than the whole row.
 */
export function OptionSelect({
  value,
  onValueChange,
  children,
  id,
  disabled,
  className,
  "aria-label": ariaLabel,
}: {
  value: string | number
  onValueChange: (value: string) => void
  children: ReactNode
  id?: string
  disabled?: boolean
  className?: string
  "aria-label"?: string
}) {
  const encode = (raw: string) => (raw === "" ? EMPTY_OPTION : raw)
  return (
    <Select
      value={encode(String(value))}
      onValueChange={(next) => onValueChange(next === EMPTY_OPTION ? "" : next)}
      disabled={disabled}
    >
      <SelectTrigger id={id} aria-label={ariaLabel} className={cn("min-w-28 max-w-full", className)}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {optionItems(children).map((item) => (
          <SelectItem key={item.value} value={encode(item.value)}>{item.label}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

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
  /** Shown after a short field, e.g. 秒 */
  unit?: string
  /** A short value (a number): the field is as wide as it needs, not the whole row */
  short?: boolean
}

interface ProxySettingProps extends SettingRowProps {
  /**
   * An empty value follows this other proxy setting instead of the system proxy.
   * `where` names the place it is set, `value` is its current value.
   */
  inheritFrom?: { where: string; value: string }
}

export function ProxySetting({
  ...props
}: ProxySettingProps) {
  return <ProxySettingEditor key={`${props.settingKey}:${props.value}`} {...props} />
}

function ProxySettingEditor({
  label,
  settingKey,
  value,
  onSave,
  saving,
  saved,
  inheritFrom,
}: ProxySettingProps) {
  const persistedMode = proxyMode(value)
  const [mode, setMode] = useState<ProxyMode>(persistedMode)
  const [customValue, setCustomValue] = useState(persistedMode === "custom" ? value : "")

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
        <OptionSelect aria-label={`${String(label)}模式`} value={mode} onValueChange={(next) => saveMode(next as ProxyMode)}>
          <option value="system">{inheritFrom ? `跟随全局（${describeProxy(inheritFrom.value)}）` : "系统代理"}</option>
          <option value="none">无代理</option>
          <option value="custom">自定义</option>
        </OptionSelect>
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
        {inheritFrom
          ? `全局代理在「${inheritFrom.where}」里设置；选「无代理」或「自定义」只改这里。`
          : "系统代理读取环境变量和操作系统设置；自定义地址支持 HTTP、HTTPS 和 SOCKS。"}
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
  ...props
}: SettingRowProps & { title?: string; pickerLabel?: string }) {
  return <PathPickerRowEditor key={`${props.settingKey}:${props.value}`} {...props} />
}

function PathPickerRowEditor({
  label,
  settingKey,
  value,
  onSave,
  saving,
  saved,
  placeholder,
  title,
  pickerLabel = "选择",
}: SettingRowProps & { title?: string; pickerLabel?: string }) {
  const [editValue, setEditValue] = useState(value)

  const pickDirectory = async () => {
    const manual = window.prompt(title ?? "输入文件夹路径", editValue)
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
        {pickerLabel}
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
  ...props
}: SettingRowProps) {
  return <SettingRowEditor key={`${props.settingKey}:${props.value}`} {...props} />
}

function SettingRowEditor({
  label,
  settingKey,
  value,
  onSave,
  saving,
  saved,
  masked,
  placeholder,
  unit,
  short,
}: SettingRowProps) {
  const [editValue, setEditValue] = useState(value)

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
        className={cn("h-8 text-sm", short || unit ? "w-28 flex-none" : "flex-1")}
        autoComplete="off"
        placeholder={placeholder}
      />
      {unit && <span className="text-sm text-muted-foreground">{unit}</span>}
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
