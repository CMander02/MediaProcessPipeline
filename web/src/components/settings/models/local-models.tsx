import { useEffect, useState } from "react"
import { api, type LocalAsrModelsStatus } from "@/lib/api"
import { type LocalModelSettingsProps } from "./types"
import { type LocalSettingsId, LOCAL_SETTINGS_ENTRIES } from "./local-model-options"
import { LocalSettingsLayout, DetailHeader } from "./controls"
import {
  AudioFlowControls,
  VocalSeparationControls,
  AsrSettingsControls,
  LocalLlmSettingsControls,
} from "./audio-controls"

export function LocalModelSettings({
  settings,
  updateSetting,
  saving,
  saved,
  detectLocalUvr,
  uvrDetecting,
  uvrDetection,
}: LocalModelSettingsProps) {
  const [selectedId, setSelectedId] = useState<LocalSettingsId>("audio-flow")
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
  const activeItem = LOCAL_SETTINGS_ENTRIES.find((entry) => entry.id === selectedId)
    ?? LOCAL_SETTINGS_ENTRIES[0]

  return (
    <LocalSettingsLayout
      selectedId={selectedId}
      onSelect={setSelectedId}
    >
      <DetailHeader
        title={activeItem.title}
        description={activeItem.description}
      />

      {selectedId === "audio-flow" ? (
        <AudioFlowControls
          settings={settings}
          updateSetting={updateSetting}
          saving={saving}
          saved={saved}
        />
      ) : null}

      {selectedId === "uvr" ? (
        <VocalSeparationControls
          settings={settings}
          updateSetting={updateSetting}
          saving={saving}
          saved={saved}
          detectLocalUvr={detectLocalUvr}
          uvrDetecting={uvrDetecting}
          uvrDetection={uvrDetection}
        />
      ) : null}

      {selectedId === "sherpa-asr" ? (
        <AsrSettingsControls
          settings={settings}
          updateSetting={updateSetting}
          saving={saving}
          saved={saved}
          sherpaStatus={sherpaStatus}
        />
      ) : null}

      {selectedId === "local-llm" ? (
        <LocalLlmSettingsControls
          settings={settings}
          updateSetting={updateSetting}
          saving={saving}
          saved={saved}
        />
      ) : null}
    </LocalSettingsLayout>
  )
}
