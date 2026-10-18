import { useId } from "react"
import { Checkbox } from "@/components/ui/checkbox"
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel, FieldLegend, FieldSet } from "@/components/ui/field"
import { WORKFLOW_GROUPS, type WorkflowConfig } from "@/lib/workflow"

export function WorkflowControls({ value, onChange, disabled = false }: {
  value: WorkflowConfig
  onChange: (value: WorkflowConfig) => void
  disabled?: boolean
}) {
  const id = useId()
  return <div className="flex flex-col gap-4">
    {WORKFLOW_GROUPS.map((group, index) => <FieldSet key={group.label}>
      <FieldLegend variant="label">{group.label}</FieldLegend>
      <FieldGroup className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {group.steps.map((step) => <Field key={step.key} orientation="horizontal" data-disabled={disabled}>
          <Checkbox id={`${id}-${step.key}`} checked={value[step.key]} disabled={disabled}
            onCheckedChange={(checked) => onChange({ ...value, [step.key]: checked === true })} />
          <FieldContent>
            <FieldLabel htmlFor={`${id}-${step.key}`}>{step.label}</FieldLabel>
            <FieldDescription>{step.description}</FieldDescription>
          </FieldContent>
        </Field>)}
      </FieldGroup>
      {index === 0 && <Field orientation="horizontal" className="rounded-md border p-3" data-disabled>
        <Checkbox id={`${id}-asr`} checked disabled />
        <FieldContent>
          <FieldLabel htmlFor={`${id}-asr`}>ASR 语音识别 · 必选</FieldLabel>
          <FieldDescription>音视频始终转写，并保存原始字幕。文字和图文按各自流程处理。</FieldDescription>
        </FieldContent>
      </Field>}
    </FieldSet>)}
    <p className="text-xs text-muted-foreground">未勾选的步骤会跳过。输出文件始终归档保存。</p>
  </div>
}
