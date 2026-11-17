import { useAppAccess } from "@/hooks/use-app-access-context"
import { useMediaQuery } from "@/hooks/use-media-query"
import { usePlatform } from "@/platform/use-platform"
import type { ComputeCapabilities } from "@/lib/api"
import { SelectSettingRow } from "./controls"

type ComputeComponent = "uvr" | "sherpa_onnx" | "llama_cpp" | "moss_cpp" | "torch" | "transformers"

function gpuLabel(component: ComputeComponent, capabilities: ComputeCapabilities | undefined): string {
  if (!capabilities) return "GPU（能力待检测）"
  const configured = capabilities.component_gpu_backends?.[component]
  if (configured === null) return "GPU（当前组件不可用）"
  if (configured) return `GPU（${configured === "cuda" ? "CUDA" : configured === "mps" ? "MPS" : "Metal"}）`
  if (capabilities.cuda_available) return "GPU（CUDA，需运行时支持）"
  if ((component === "llama_cpp" || component === "moss_cpp") && capabilities.metal_available) return "GPU（Metal，需引擎支持）"
  if ((component === "torch" || component === "transformers") && capabilities.mps_available) return "GPU（MPS）"
  return "GPU（当前组件不可用）"
}

export function DeviceSettingRow({
  component,
  value,
  onChange,
  label = "运行设备",
}: {
  component: ComputeComponent
  value: string
  onChange: (value: string) => void | Promise<void>
  label?: string
}) {
  const { capabilities } = useAppAccess()
  const platform = usePlatform()
  const phone = useMediaQuery("(max-width: 767px)")
  if (platform.isNative || phone) return null

  const gpu = gpuLabel(component, capabilities?.compute_capabilities)
  const current = value === "cpu" || value === "auto" ? value : value ? "gpu" : "auto"
  return (
    <div className="flex flex-col gap-1">
      <SelectSettingRow label={label} value={current} onChange={onChange}>
        <option value="auto">自动</option>
        <option value="gpu">{gpu}</option>
        <option value="cpu">CPU</option>
      </SelectSettingRow>
      <p className="pl-[6.75rem] text-xs text-muted-foreground">
        {gpu.includes("不可用") ? "自动模式使用 CPU；显式选择 GPU 时会检查运行时支持。" : "自动模式优先使用可用 GPU，无可用 GPU 时使用 CPU。GPU 需要模型运行时支持。"}
      </p>
    </div>
  )
}
