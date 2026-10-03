import { useEffect, useState } from "react"
import { api, type LocalAsrModelsStatus } from "@/lib/api"
import { type LocalModelSettingsProps, type SharedSettingsProps } from "./types"
import { type LocalSettingsId, LOCAL_SETTINGS_ENTRIES } from "./local-model-options"
import { DetailHeader } from "./controls"
import {
  AudioFlowControls,
  VocalSeparationControls,
  AsrSettingsControls,
  LocalLlmSettingsControls,
} from "./audio-controls"

function AsrSection(props: SharedSettingsProps) {
  const { settings } = props
  const [sherpaStatus, setSherpaStatus] = useState<LocalAsrModelsStatus | null>(null)

  useEffect(() => {
    let active = true
    api.settings.localAsrModels()
      .then((status) => {
        if (active) setSherpaStatus(status)
      })
      .catch(() => {
        if (active) setSherpaStatus(null)
      })
    return () => {
      active = false
    }
  }, [settings.sherpa_model_id, settings.sherpa_model_root])

  return <AsrSettingsControls {...props} sherpaStatus={sherpaStatus} />
}

/** One stage of the local audio and model pipeline, as a section of 处理流程. */
export function LocalModelSection({
  id,
  settings,
  updateSetting,
  saving,
  saved,
  detectLocalUvr,
  uvrDetecting,
  uvrDetection,
}: LocalModelSettingsProps & { id: LocalSettingsId }) {
  const entry = LOCAL_SETTINGS_ENTRIES.find((item) => item.id === id) ?? LOCAL_SETTINGS_ENTRIES[0]
  const shared = { settings, updateSetting, saving, saved }

  return (
    <div className="space-y-4">
      <DetailHeader title={entry.title} description={entry.description} />
      {id === "audio-flow" && <AudioFlowControls {...shared} />}
      {id === "uvr" && (
        <VocalSeparationControls
          {...shared}
          detectLocalUvr={detectLocalUvr}
          uvrDetecting={uvrDetecting}
          uvrDetection={uvrDetection}
        />
      )}
      {id === "sherpa-asr" && <AsrSection {...shared} />}
      {id === "local-llm" && <LocalLlmSettingsControls {...shared} />}
    </div>
  )
}
