import { type ReactNode } from "react"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { SettingRow } from "../setting-controls"
import { type LocalSettingsId, LOCAL_SETTINGS_ENTRIES } from "./local-model-options"
import { type ModelListItem, type SharedSettingsProps } from "./types"

export function CardLikeSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <h3 className="mb-3 text-base font-semibold">{title}</h3>
      {children}
    </div>
  )
}

export function ProviderFormRow({
  label,
  className,
  children,
}: {
  label: string
  className?: string
  children: ReactNode
}) {
  return (
    <div className={["space-y-1", className].filter(Boolean).join(" ")}>
      <Label className="text-xs text-muted-foreground">{label}</Label>
      {children}
    </div>
  )
}

export function ProviderTextInput({
  fieldKey,
  value,
  onCommit,
  type = "text",
  placeholder,
  className,
  ariaLabel,
}: {
  fieldKey: string
  value: string
  onCommit: (value: string) => void
  type?: "text" | "password" | "number"
  placeholder?: string
  className?: string
  ariaLabel?: string
}) {
  return (
    <input
      key={`${fieldKey}:${value}`}
      type={type}
      min={type === "number" ? 1 : undefined}
      step={type === "number" ? 1 : undefined}
      aria-label={ariaLabel}
      defaultValue={value}
      onBlur={(event) => onCommit(event.target.value)}
      placeholder={placeholder}
      className={[
        "h-8 w-full rounded-md border border-input bg-background px-2 text-sm",
        className,
      ].filter(Boolean).join(" ")}
    />
  )
}

export function ProviderEmptyState({ hasProviders }: { hasProviders: boolean }) {
  return (
    <div className="flex h-full min-h-64 items-center justify-center rounded-md border border-dashed border-border text-sm text-muted-foreground">
      {hasProviders ? "当前筛选条件下没有 Provider。" : "还没有 Provider。"}
    </div>
  )
}

export function LocalSettingsLayout({
  selectedId,
  onSelect,
  children,
}: {
  selectedId: LocalSettingsId
  onSelect: (id: LocalSettingsId) => void
  children: ReactNode
}) {
  return (
    <div className="grid h-full min-h-0 grid-cols-1 gap-4 overflow-y-auto xl:grid-cols-[190px_minmax(0,1fr)] xl:overflow-hidden">
      <aside className="rounded-lg border bg-card/30 p-2 xl:min-h-0">
        <nav aria-label="本地模型设置" className="flex gap-1 overflow-x-auto xl:block xl:space-y-1">
          {LOCAL_SETTINGS_ENTRIES.map((entry) => {
            const active = entry.id === selectedId
            return (
              <button
                key={entry.id}
                type="button"
                onClick={() => onSelect(entry.id)}
                className={[
                  "shrink-0 rounded-md px-3 py-2 text-left text-sm transition-colors xl:w-full",
                  active
                    ? "bg-primary/10 font-medium text-primary"
                    : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                ].join(" ")}
              >
                {entry.title}
              </button>
            )
          })}
        </nav>
      </aside>

      <section className="min-h-[360px] min-w-0 overflow-hidden rounded-lg border bg-background xl:min-h-0">
        <div className="h-full space-y-1 overflow-y-auto p-5 pr-4">{children}</div>
      </section>
    </div>
  )
}

export function ModelListLayout({
  searchPlaceholder,
  query,
  onQueryChange,
  items,
  selectedId,
  onSelect,
  footer,
  children,
}: {
  searchPlaceholder: string
  query: string
  onQueryChange: (value: string) => void
  items: ModelListItem[]
  selectedId: string
  onSelect: (id: string) => void
  footer?: ReactNode
  children: ReactNode
}) {
  const normalizedQuery = query.trim().toLowerCase()
  const visibleItems = normalizedQuery
    ? items.filter((item) =>
      `${item.title} ${item.description} ${item.searchText ?? ""}`.toLowerCase().includes(normalizedQuery),
    )
    : items

  return (
    <div className="grid h-full min-h-0 grid-cols-1 gap-4 overflow-y-auto xl:grid-cols-[minmax(280px,320px)_minmax(0,1fr)] xl:overflow-hidden">
      <aside className="flex flex-col rounded-lg border bg-card/40 p-3 xl:min-h-0 xl:overflow-hidden">
        <input
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder={searchPlaceholder}
          className="mb-3 h-9 rounded-full border border-input bg-background px-4 text-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-primary"
        />
        <div className="max-h-56 space-y-1 overflow-y-auto pr-1 xl:min-h-0 xl:max-h-none xl:flex-1">
          {visibleItems.map((item) => {
            const active = item.id === selectedId
            return (
              <button
                key={item.id}
                type="button"
                onClick={() => onSelect(item.id)}
                className={[
                  "flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left transition-colors",
                  active ? "bg-primary/10 text-foreground" : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
                ].join(" ")}
              >
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-background text-xs font-semibold text-primary ring-1 ring-border [&_svg]:h-5 [&_svg]:w-5">
                  {item.icon ?? item.badge}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{item.title}</span>
                  <span className="block truncate text-xs text-muted-foreground">{item.description}</span>
                </span>
                {item.status && (
                  <span className="rounded-full border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700">
                    {item.status}
                  </span>
                )}
              </button>
            )
          })}
        </div>
        {footer}
      </aside>

      <section className="min-h-[360px] min-w-0 overflow-hidden rounded-lg border bg-background xl:min-h-0">
        <div className="h-full space-y-5 overflow-y-auto p-5 pr-4">{children}</div>
      </section>
    </div>
  )
}

export function DetailHeader({
  title,
  description,
  active,
  activeLabel = "默认 LLM",
  onActivate,
}: {
  title: string
  description: string
  active?: boolean
  activeLabel?: string
  onActivate?: () => void | Promise<void>
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-border pb-4">
      <div className="space-y-1">
        <h3 className="text-lg font-semibold text-foreground">{title}</h3>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      {typeof active === "boolean" && (
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">{activeLabel}</span>
          <Switch
            checked={active}
            onCheckedChange={(checked) => {
              if (checked) void onActivate?.()
            }}
          />
        </div>
      )}
    </div>
  )
}

export function SelectSettingRow({
  label,
  value,
  onChange,
  children,
}: {
  label: string
  value: string
  onChange: (value: string) => void | Promise<void>
  children: ReactNode
}) {
  return (
    <div className="flex items-center gap-3">
      <Label className="w-24 shrink-0 text-sm text-muted-foreground" htmlFor={`select-${label}`}>{label}</Label>
      <select
        id={`select-${label}`}
        value={value}
        onChange={(event) => void onChange(event.target.value)}
        className="h-8 min-w-0 flex-1 rounded-md border border-input bg-background px-3 text-sm sm:max-w-md"
      >
        {children}
      </select>
    </div>
  )
}

export function SwitchSettingRow({
  label,
  checked,
  onChange,
}: {
  label: string
  checked: boolean
  onChange: (checked: boolean) => void | Promise<void>
}) {
  return (
    <div className="flex items-center justify-between gap-3 sm:max-w-md">
      <Label className="text-sm text-muted-foreground" htmlFor={`switch-${label}`}>{label}</Label>
      <Switch id={`switch-${label}`} checked={checked} onCheckedChange={(value) => void onChange(value)} />
    </div>
  )
}

export function AdvancedSettings({ children, label = "高级设置" }: { children: ReactNode; label?: string }) {
  return (
    <details className="rounded-md border border-border/70 px-3 py-2">
      <summary className="cursor-pointer select-none text-sm text-muted-foreground">{label}</summary>
      <div className="mt-3 space-y-3">{children}</div>
    </details>
  )
}

export function NumberSettingRow({
  label,
  settingKey,
  fallback,
  settings,
  updateSetting,
  saving,
  saved,
}: SharedSettingsProps & { label: string; settingKey: string; fallback: number }) {
  return (
    <SettingRow
      label={label}
      settingKey={settingKey}
      value={String(settings[settingKey] ?? fallback)}
      onSave={(key, value) => updateSetting(key, Number(value))}
      saving={saving}
      saved={saved}
    />
  )
}
