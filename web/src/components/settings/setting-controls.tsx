import { Children, isValidElement, useContext, useEffect, useRef, useState, type ReactNode } from "react"
import { HugeiconsIcon } from "@hugeicons/react"
import { FolderOpenIcon, Tick02Icon } from "@hugeicons/core-free-icons"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { cn } from "@/lib/utils"
import { describeProxy, proxyMode, type ProxyMode } from "@/lib/proxy"
import { SettingsSaveErrors } from "./save-errors"

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

export function ProxySetting(props: ProxySettingProps) {
  const error = useContext(SettingsSaveErrors)[props.settingKey]
  return (
    <div className="space-y-1">
      <ProxySettingEditor key={`${props.settingKey}:${props.value}`} {...props} />
      <FieldError error={error} />
    </div>
  )
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

  const saveCustom = () => {
    const normalized = customValue.trim()
    if (normalized && normalized !== value.trim()) void onSave(settingKey, normalized)
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
            onBlur={saveCustom}
            onKeyDown={(event) => event.key === "Enter" && saveCustom()}
            className="h-8 flex-1 text-sm"
            autoComplete="off"
            placeholder="http://localhost:7897"
          />
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

interface SavedChange {
  from: string
  to: string
}

/**
 * The one save rule for text settings: a change saves on blur or Enter, the field then says
 * 已保存 with 撤销 for a few seconds, and a failed save shows its reason under the field.
 */
function useFieldSave({ settingKey, value, onSave }: Pick<SettingRowProps, "settingKey" | "value" | "onSave">) {
  const error = useContext(SettingsSaveErrors)[settingKey]
  const [change, setChange] = useState<SavedChange | null>(null)
  const timer = useRef<number | undefined>(undefined)

  useEffect(() => () => window.clearTimeout(timer.current), [])

  const save = async (next: string) => {
    const from = value
    window.clearTimeout(timer.current)
    setChange(null)
    await onSave(settingKey, next)
    setChange({ from, to: next })
    timer.current = window.setTimeout(() => setChange(null), 6000)
  }

  const undo = () => {
    if (!change) return
    window.clearTimeout(timer.current)
    setChange(null)
    void onSave(settingKey, change.from)
  }

  // A failed save leaves its reason in the context, so it is never reported as saved.
  return { save, undo, change: error ? null : change, error }
}

function SaveStatus({
  saving,
  change,
  canUndo,
  onUndo,
}: {
  saving: boolean
  change: SavedChange | null
  canUndo: boolean
  onUndo: () => void
}) {
  if (saving) return <span className="shrink-0 text-xs text-muted-foreground">保存中…</span>
  if (!change) return null
  return (
    <span role="status" className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
      <HugeiconsIcon icon={Tick02Icon} className="size-3.5 text-emerald-500" />
      已保存
      {canUndo && (
        <button type="button" onClick={onUndo} className="font-medium text-foreground underline-offset-2 hover:underline">
          撤销
        </button>
      )}
    </span>
  )
}

function FieldError({ error }: { error?: string }) {
  if (!error) return null
  return <p className="pl-[6.75rem] text-xs text-destructive">没保存上：{error}</p>
}

/** Commits a text field on blur or Enter when it changed; Esc puts the saved value back. */
function useCommittedText(value: string, onCommit: (next: string) => void) {
  const [text, setText] = useState(value)
  const committed = useRef(value)
  const commit = () => {
    if (text === value || text === committed.current) return
    committed.current = text
    onCommit(text)
  }
  return {
    text,
    setText,
    inputProps: {
      value: text,
      onChange: (event: React.ChangeEvent<HTMLInputElement>) => setText(event.target.value),
      onBlur: commit,
      onKeyDown: (event: React.KeyboardEvent<HTMLInputElement>) => {
        if (event.key === "Enter") commit()
        if (event.key === "Escape") setText(value)
      },
    },
  }
}

type PathPickerRowProps = SettingRowProps & { title?: string; pickerLabel?: string }

export function PathPickerRow(props: PathPickerRowProps) {
  const field = useFieldSave(props)
  return (
    <div className="space-y-1">
      <PathPickerRowEditor
        key={`${props.settingKey}:${props.value}`}
        {...props}
        onCommit={(next) => void field.save(next)}
        status={<SaveStatus saving={Boolean(props.saving[props.settingKey])} change={field.change} canUndo onUndo={field.undo} />}
      />
      <FieldError error={field.error} />
    </div>
  )
}

function PathPickerRowEditor({
  label,
  value,
  placeholder,
  title,
  pickerLabel = "选择",
  onCommit,
  status,
}: PathPickerRowProps & { onCommit: (next: string) => void; status: ReactNode }) {
  const { text, setText, inputProps } = useCommittedText(value, onCommit)

  const pickDirectory = () => {
    const manual = window.prompt(title ?? "输入文件夹路径", text)
    if (manual !== null && manual !== value) {
      setText(manual)
      onCommit(manual)
    }
  }

  return (
    <div className="flex items-center gap-3">
      <Label className="w-24 shrink-0 text-sm text-muted-foreground">{label}</Label>
      <Input
        {...inputProps}
        aria-label={typeof label === "string" ? label : undefined}
        className="h-8 flex-1 text-sm"
        autoComplete="off"
        placeholder={placeholder}
      />
      <Button size="sm" variant="ghost" onClick={pickDirectory} className="h-8 gap-1.5 px-2">
        <HugeiconsIcon icon={FolderOpenIcon} className="h-3.5 w-3.5" />
        {pickerLabel}
      </Button>
      {status}
    </div>
  )
}

export function SettingRow(props: SettingRowProps) {
  const field = useFieldSave(props)
  return (
    <div className="space-y-1">
      <SettingRowEditor
        key={`${props.settingKey}:${props.value}`}
        {...props}
        onCommit={(next) => void field.save(next)}
        status={(
          <SaveStatus
            saving={Boolean(props.saving[props.settingKey])}
            change={field.change}
            // A masked secret comes back as its mask, which can't be saved back.
            canUndo={!props.masked}
            onUndo={field.undo}
          />
        )}
      />
      <FieldError error={field.error} />
    </div>
  )
}

function SettingRowEditor({
  label,
  value,
  masked,
  placeholder,
  unit,
  short,
  onCommit,
  status,
}: SettingRowProps & { onCommit: (next: string) => void; status: ReactNode }) {
  const { inputProps } = useCommittedText(value, onCommit)

  return (
    <div className="flex items-center gap-3">
      <Label className="w-24 shrink-0 text-sm text-muted-foreground">{label}</Label>
      <Input
        {...inputProps}
        type={masked ? "password" : "text"}
        aria-label={typeof label === "string" ? label : undefined}
        className={cn("h-8 text-sm", short || unit ? "w-28 flex-none" : "flex-1")}
        autoComplete="off"
        placeholder={placeholder}
      />
      {unit && <span className="text-sm text-muted-foreground">{unit}</span>}
      {status}
    </div>
  )
}
