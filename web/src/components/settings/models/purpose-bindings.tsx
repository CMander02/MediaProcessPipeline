import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { type PurposeModelBindingsProps, type PurposeBindingDef } from "./types"
import {
  buildProviderModelOptions,
  getRuntimeModelBindings,
  bindingValue,
  asrBindingValue,
  splitModelValue,
  capabilityForBinding,
} from "./registry-utils"
import { CardLikeSection } from "./controls"

export function PurposeModelBindings({ settings, updateSetting }: PurposeModelBindingsProps) {
  const textOptions = buildProviderModelOptions(settings, "llm")
  const asrOptions = buildProviderModelOptions(settings, "asr")
  const visionOptions = buildProviderModelOptions(settings, "vision")
  const embeddingOptions = buildProviderModelOptions(settings, "embedding")
  const runtimeBindings = getRuntimeModelBindings(settings)

  const bindings: PurposeBindingDef[] = [
    {
      key: "polish",
      label: "字幕简单润色",
      description: "字幕初修、错字修正和轻量断句。",
      options: textOptions,
      fallback: bindingValue(runtimeBindings.polish, "deepseek", settings.deepseek_polish_model, "deepseek-v4-flash"),
    },
    {
      key: "subtitle_refine",
      label: "字幕二次润色",
      description: "上下文一致性、专名统一和语气调整。",
      options: textOptions,
      fallback: bindingValue(runtimeBindings.subtitle_refine, "deepseek", settings.deepseek_polish_model, "deepseek-v4-flash"),
    },
    {
      key: "analyze",
      label: "字幕分析",
      description: "语言、主题、专名和结构线索抽取。",
      options: textOptions,
      fallback: bindingValue(runtimeBindings.analyze, "deepseek", settings.deepseek_analyze_model, "deepseek-v4-flash"),
    },
    {
      key: "summary",
      label: "全文总结",
      description: "README、章节总结和观点归纳。",
      options: textOptions,
      fallback: bindingValue(runtimeBindings.summary, "deepseek", settings.deepseek_summary_model, "deepseek-v4-pro"),
    },
    {
      key: "mindmap",
      label: "思维导图",
      description: "导图 map/reduce 和层级结构生成。",
      options: textOptions,
      fallback: bindingValue(runtimeBindings.mindmap, "deepseek", settings.deepseek_mindmap_model, "deepseek-v4-flash"),
    },
    {
      key: "asr",
      label: "ASR",
      description: "语音识别，可选择本地模型或 ASR API。",
      options: asrOptions,
      fallback: asrBindingValue(settings, runtimeBindings.asr),
    },
    {
      key: "vision",
      label: "图文理解",
      description: "小红书图文笔记的 OCR、图片理解和场景描述。",
      options: visionOptions,
      fallback: bindingValue(runtimeBindings.vision, "custom-vision-default", settings.vlm_model, "Qwen/Qwen3.5-4B"),
    },
    {
      key: "embedding",
      label: "知识库向量",
      description: "任务完成后的字幕、摘要和片段索引。",
      options: embeddingOptions,
      fallback: bindingValue(runtimeBindings.embedding, "custom-embedding-default", settings.kb_embedding_model, "qwen3-embedding-0.6b"),
    },
  ]

  const updateBinding = async (binding: PurposeBindingDef, value: string) => {
    const [providerId, modelId] = splitModelValue(value)
    await updateSetting("runtime_model_bindings", {
      ...runtimeBindings,
      [binding.key]: {
        provider_id: providerId,
        model_id: modelId,
        capability: capabilityForBinding(binding.key),
      },
    })
  }

  return (
    <CardLikeSection title="模型用途">
      <div className="grid gap-3 lg:grid-cols-2">
        {bindings.map((binding) => (
          <PurposeBindingRow
            key={binding.key}
            binding={binding}
            value={binding.fallback}
            onChange={(value) => updateBinding(binding, value)}
          />
        ))}
      </div>
    </CardLikeSection>
  )
}

function PurposeBindingRow({
  binding,
  value,
  onChange,
}: {
  binding: PurposeBindingDef
  value: string
  onChange: (value: string) => Promise<void>
}) {
  const hasSelectedValue = binding.options.some((option) => option.value === value)
  const selectedValue = hasSelectedValue ? value : ""

  return (
    <div className="flex flex-col gap-2 rounded-lg border border-border/70 p-3">
      <div className="flex flex-col gap-1">
        <Label className="text-sm font-medium">{binding.label}</Label>
        <p className="text-xs leading-5 text-muted-foreground">{binding.description}</p>
      </div>
      <Select
        value={selectedValue}
        onValueChange={(nextValue) => void onChange(nextValue)}
        disabled={binding.options.length === 0}
      >
        <SelectTrigger className="w-full" aria-label={binding.label}>
          <SelectValue placeholder={binding.options.length === 0 ? "无可用模型" : value ? "已选模型不可用，请重新选择" : "请选择模型"} />
        </SelectTrigger>
        <SelectContent position="popper" align="start">
          <SelectGroup>
            {binding.options.map((option) => (
              <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
    </div>
  )
}
