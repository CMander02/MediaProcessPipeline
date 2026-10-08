import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react"
import { Popover } from "radix-ui"
import { HugeiconsIcon } from "@hugeicons/react"
import { ArrowDown01Icon } from "@hugeicons/core-free-icons"
import { cn } from "@/lib/utils"

interface SpeakerNameEditorProps {
  initialValue: string
  label: string
  options: string[]
  color: string
  onSave: (name: string) => void
  onCancel: () => void
}

export function SpeakerNameEditor({ initialValue, label, options, color, onSave, onCancel }: SpeakerNameEditorProps) {
  const [value, setValue] = useState(initialValue)
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(-1)
  const inputRef = useRef<HTMLInputElement>(null)
  const anchorRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const composing = useRef(false)
  const finished = useRef(false)
  const listId = useId()
  const names = [...new Set(options)]

  useEffect(() => {
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [])

  const save = (name = value) => {
    if (finished.current) return
    finished.current = true
    onSave(name.trim())
  }
  const cancel = () => {
    if (finished.current) return
    finished.current = true
    onCancel()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    event.stopPropagation()
    if (composing.current || event.nativeEvent.isComposing || event.keyCode === 229) return
    if (event.key === "Enter") {
      event.preventDefault()
      save(open && activeIndex >= 0 ? names[activeIndex] : value)
    } else if (event.key === "Escape") {
      event.preventDefault()
      cancel()
    } else if ((event.key === "ArrowDown" || event.key === "ArrowUp") && names.length > 0) {
      event.preventDefault()
      setOpen(true)
      setActiveIndex((current) => event.key === "ArrowDown"
        ? (current + 1) % names.length
        : (current <= 0 ? names.length : current) - 1)
    }
  }

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Anchor asChild>
        <div ref={anchorRef} className="flex w-36 items-center rounded border bg-background focus-within:ring-1 focus-within:ring-primary" style={{ color }}>
          <input
            ref={inputRef}
            role="combobox"
            aria-label={label}
            aria-expanded={open}
            aria-controls={open ? listId : undefined}
            aria-activedescendant={open && activeIndex >= 0 ? `${listId}-${activeIndex}` : undefined}
            aria-autocomplete="list"
            value={value}
            placeholder="输入或选择名字"
            onChange={(event) => { setValue(event.target.value); setActiveIndex(-1) }}
            onKeyDown={handleKeyDown}
            onCompositionStart={() => { composing.current = true }}
            onCompositionEnd={() => { composing.current = false }}
            onBlur={(event) => {
              if (!anchorRef.current?.contains(event.relatedTarget) && !listRef.current?.contains(event.relatedTarget)) save()
            }}
            className="min-w-0 flex-1 bg-transparent px-1 py-0.5 text-xs font-medium outline-none"
          />
          <button
            type="button"
            tabIndex={-1}
            aria-label="展开所有说话人名字"
            aria-expanded={open}
            aria-controls={open ? listId : undefined}
            onPointerDown={(event) => event.preventDefault()}
            onClick={() => { setOpen(!open); setActiveIndex(-1); inputRef.current?.focus() }}
            className="shrink-0 rounded p-1 hover:bg-muted"
          >
            <HugeiconsIcon icon={ArrowDown01Icon} className="size-3.5" />
          </button>
        </div>
      </Popover.Anchor>
      <Popover.Portal>
        <Popover.Content
          ref={listRef}
          id={listId}
          role="listbox"
          aria-label="说话人名字"
          align="start"
          sideOffset={4}
          onOpenAutoFocus={(event) => event.preventDefault()}
          onCloseAutoFocus={(event) => event.preventDefault()}
          onEscapeKeyDown={(event) => {
            event.preventDefault()
            if (!composing.current && !event.isComposing && event.keyCode !== 229) cancel()
          }}
          onInteractOutside={(event) => {
            if (anchorRef.current?.contains(event.target as Node)) event.preventDefault()
            else save()
          }}
          className="z-50 max-h-48 min-w-36 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-md border bg-popover p-1 text-popover-foreground shadow-md"
        >
          {names.map((name, index) => (
            <button
              key={name}
              id={`${listId}-${index}`}
              type="button"
              role="option"
              tabIndex={-1}
              aria-selected={activeIndex === index}
              onPointerDown={(event) => event.preventDefault()}
              onClick={() => save(name)}
              className={cn("block w-full rounded px-2 py-1 text-left text-xs hover:bg-accent", activeIndex === index && "bg-accent")}
            >
              {name}
            </button>
          ))}
          {names.length === 0 && <span className="block px-2 py-1 text-xs text-muted-foreground">暂无其他名字</span>}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}
