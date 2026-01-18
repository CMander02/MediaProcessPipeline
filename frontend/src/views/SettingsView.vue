<script setup lang="ts">
import { ref, onMounted } from "vue"
import {
  ElButton,
  ElInput,
  ElSelect,
  ElOption,
  ElSwitch,
  ElTabs,
  ElTabPane,
  ElTag,
  ElDivider,
  ElIcon,
} from "element-plus"
import {
  CircleCheck,
  WarningFilled,
  Loading,
  Cpu,
  Microphone,
  FolderOpened,
  Setting,
} from "@element-plus/icons-vue"
import { useSettings } from "@/composables/useSettings"
import { healthCheck } from "@/api"

const { settings, saving, saved, saveSettings, updateSetting } = useSettings()
const backendStatus = ref<"checking" | "online" | "offline">("checking")
const activeTab = ref("api")

onMounted(async () => {
  try {
    await healthCheck()
    backendStatus.value = "online"
  } catch {
    backendStatus.value = "offline"
  }
})

const whisperModels = [
  { value: "large-v3", label: "Large V3 (Best)" },
  { value: "large-v2", label: "Large V2" },
  { value: "medium", label: "Medium" },
  { value: "small", label: "Small" },
  { value: "base", label: "Base" },
  { value: "tiny", label: "Tiny (Fastest)" },
]

const computeTypes = [
  { value: "float16", label: "float16 (GPU)" },
  { value: "int8", label: "int8" },
  { value: "float32", label: "float32 (CPU)" },
]

const uvrModels = [
  { value: "Kim_Vocal_2", label: "Kim Vocal 2" },
  { value: "Kim_Vocal_1", label: "Kim Vocal 1" },
  { value: "UVR-MDX-NET-Inst_HQ_3", label: "MDX-NET Inst HQ 3" },
  { value: "UVR_MDXNET_KARA_2", label: "MDX-NET KARA 2" },
]
</script>

<template>
  <div class="page-container space-y-8">
    <!-- Header -->
    <div class="flex-between">
      <div>
        <h1 class="page-title">Settings</h1>
        <p class="page-description">Configure pipeline processing options</p>
      </div>
      <div class="flex items-center gap-3">
        <el-tag :type="backendStatus === 'online' ? 'success' : 'danger'">
          <el-icon class="mr-1">
            <Loading v-if="backendStatus === 'checking'" class="animate-spin" />
            <CircleCheck v-else-if="backendStatus === 'online'" />
            <WarningFilled v-else />
          </el-icon>
          Backend {{ backendStatus }}
        </el-tag>
        <el-button type="primary" :loading="saving" @click="saveSettings">
          <el-icon v-if="!saving && !saved" class="mr-1"><Setting /></el-icon>
          <el-icon v-if="saved" class="mr-1"><CircleCheck /></el-icon>
          {{ saved ? "Saved" : "Save" }}
        </el-button>
      </div>
    </div>

    <!-- Settings Tabs -->
    <el-tabs v-model="activeTab">
      <!-- API Tab -->
      <el-tab-pane label="AI & API" name="api">
        <template #label>
          <span class="flex items-center gap-1">
            <el-icon><Cpu /></el-icon>
            AI & API
          </span>
        </template>

        <div class="custom-card mt-4">
          <div class="custom-card-header">
            <h3 class="custom-card-title">AI & API Configuration</h3>
            <p class="custom-card-description">Configure LLM providers for text analysis</p>
          </div>

          <div class="space-y-6">
            <div class="space-y-2">
              <label class="text-sm font-medium">LLM Provider</label>
              <el-select
                :model-value="settings.llm_provider"
                @update:model-value="updateSetting('llm_provider', $event)"
                style="width: 100%"
              >
                <el-option value="anthropic" label="Anthropic (Claude)" />
                <el-option value="openai" label="OpenAI (GPT)" />
              </el-select>
            </div>

            <el-divider />

            <div class="space-y-2">
              <label class="text-sm font-medium">Anthropic API Key</label>
              <el-input
                type="password"
                :model-value="settings.anthropic_api_key"
                @update:model-value="updateSetting('anthropic_api_key', $event)"
                placeholder="sk-ant-..."
                show-password
              />
            </div>

            <div class="space-y-2">
              <label class="text-sm font-medium">OpenAI API Key</label>
              <el-input
                type="password"
                :model-value="settings.openai_api_key"
                @update:model-value="updateSetting('openai_api_key', $event)"
                placeholder="sk-..."
                show-password
              />
            </div>

            <div class="space-y-2">
              <label class="text-sm font-medium">Model</label>
              <el-select
                :model-value="settings.llm_model"
                @update:model-value="updateSetting('llm_model', $event)"
                style="width: 100%"
              >
                <template v-if="settings.llm_provider === 'anthropic'">
                  <el-option value="claude-sonnet-4-20250514" label="Claude Sonnet 4" />
                  <el-option value="claude-opus-4-20250514" label="Claude Opus 4" />
                  <el-option value="claude-3-5-haiku-20241022" label="Claude 3.5 Haiku" />
                </template>
                <template v-else>
                  <el-option value="gpt-4o" label="GPT-4o" />
                  <el-option value="gpt-4o-mini" label="GPT-4o mini" />
                  <el-option value="gpt-4-turbo" label="GPT-4 Turbo" />
                </template>
              </el-select>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- Transcription Tab -->
      <el-tab-pane label="Transcription" name="transcription">
        <template #label>
          <span class="flex items-center gap-1">
            <el-icon><Microphone /></el-icon>
            Transcription
          </span>
        </template>

        <div class="custom-card mt-4">
          <div class="custom-card-header">
            <h3 class="custom-card-title">Transcription Settings</h3>
            <p class="custom-card-description">Configure WhisperX for speech recognition</p>
          </div>

          <div class="space-y-6">
            <div class="grid grid-cols-2 gap-4">
              <div class="space-y-2">
                <label class="text-sm font-medium">Whisper Model</label>
                <el-select
                  :model-value="settings.whisper_model"
                  @update:model-value="updateSetting('whisper_model', $event)"
                  style="width: 100%"
                >
                  <el-option
                    v-for="m in whisperModels"
                    :key="m.value"
                    :value="m.value"
                    :label="m.label"
                  />
                </el-select>
              </div>

              <div class="space-y-2">
                <label class="text-sm font-medium">Device</label>
                <el-select
                  :model-value="settings.whisper_device"
                  @update:model-value="updateSetting('whisper_device', $event)"
                  style="width: 100%"
                >
                  <el-option value="cuda" label="CUDA (GPU)" />
                  <el-option value="cpu" label="CPU" />
                </el-select>
              </div>

              <div class="space-y-2">
                <label class="text-sm font-medium">Compute Type</label>
                <el-select
                  :model-value="settings.whisper_compute_type"
                  @update:model-value="updateSetting('whisper_compute_type', $event)"
                  style="width: 100%"
                >
                  <el-option
                    v-for="c in computeTypes"
                    :key="c.value"
                    :value="c.value"
                    :label="c.label"
                  />
                </el-select>
              </div>
            </div>

            <el-divider />

            <div class="flex-between">
              <div>
                <label class="text-sm font-medium">Speaker Diarization</label>
                <p class="text-xs text-[var(--text-muted)]">Identify and label different speakers</p>
              </div>
              <el-switch
                :model-value="settings.enable_diarization"
                @update:model-value="updateSetting('enable_diarization', $event as boolean)"
              />
            </div>

            <div class="space-y-2">
              <label class="text-sm font-medium">HuggingFace Token</label>
              <el-input
                type="password"
                :model-value="settings.hf_token"
                @update:model-value="updateSetting('hf_token', $event)"
                placeholder="hf_..."
                show-password
              />
              <p class="text-xs text-[var(--text-muted)]">Required for speaker diarization (pyannote models)</p>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- Paths Tab -->
      <el-tab-pane label="Paths" name="paths">
        <template #label>
          <span class="flex items-center gap-1">
            <el-icon><FolderOpened /></el-icon>
            Paths
          </span>
        </template>

        <div class="custom-card mt-4">
          <div class="custom-card-header">
            <h3 class="custom-card-title">Data Paths</h3>
            <p class="custom-card-description">Configure storage directories</p>
          </div>

          <div class="space-y-4">
            <div class="space-y-2">
              <label class="text-sm font-medium">Inbox Path</label>
              <el-input
                :model-value="settings.inbox_path"
                @update:model-value="updateSetting('inbox_path', $event)"
                placeholder="./data/inbox"
              />
              <p class="text-xs text-[var(--text-muted)]">Directory for incoming media files</p>
            </div>

            <div class="space-y-2">
              <label class="text-sm font-medium">Processing Path</label>
              <el-input
                :model-value="settings.processing_path"
                @update:model-value="updateSetting('processing_path', $event)"
                placeholder="./data/processing"
              />
              <p class="text-xs text-[var(--text-muted)]">Temporary storage during processing</p>
            </div>

            <div class="space-y-2">
              <label class="text-sm font-medium">Outputs Path</label>
              <el-input
                :model-value="settings.outputs_path"
                @update:model-value="updateSetting('outputs_path', $event)"
                placeholder="./data/outputs"
              />
              <p class="text-xs text-[var(--text-muted)]">Final processed outputs</p>
            </div>

            <el-divider />

            <div class="space-y-2">
              <label class="text-sm font-medium">Archive Path</label>
              <el-input
                :model-value="settings.archive_path"
                @update:model-value="updateSetting('archive_path', $event)"
                placeholder="./data/archive"
              />
            </div>

            <div class="space-y-2">
              <label class="text-sm font-medium">Obsidian Vault Path (Optional)</label>
              <el-input
                :model-value="settings.obsidian_vault_path"
                @update:model-value="updateSetting('obsidian_vault_path', $event)"
                placeholder="C:\Users\...\ObsidianVault"
              />
              <p class="text-xs text-[var(--text-muted)]">Auto-sync archives to Obsidian vault</p>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <!-- Processing Tab -->
      <el-tab-pane label="Processing" name="processing">
        <template #label>
          <span class="flex items-center gap-1">
            <el-icon><Setting /></el-icon>
            Processing
          </span>
        </template>

        <div class="custom-card mt-4">
          <div class="custom-card-header">
            <h3 class="custom-card-title">Audio Processing</h3>
            <p class="custom-card-description">Configure UVR5 vocal separation</p>
          </div>

          <div class="grid grid-cols-2 gap-4">
            <div class="space-y-2">
              <label class="text-sm font-medium">UVR Model</label>
              <el-select
                :model-value="settings.uvr_model"
                @update:model-value="updateSetting('uvr_model', $event)"
                style="width: 100%"
              >
                <el-option
                  v-for="m in uvrModels"
                  :key="m.value"
                  :value="m.value"
                  :label="m.label"
                />
              </el-select>
            </div>

            <div class="space-y-2">
              <label class="text-sm font-medium">Device</label>
              <el-select
                :model-value="settings.uvr_device"
                @update:model-value="updateSetting('uvr_device', $event)"
                style="width: 100%"
              >
                <el-option value="cuda" label="CUDA (GPU)" />
                <el-option value="cpu" label="CPU" />
              </el-select>
            </div>
          </div>
        </div>
      </el-tab-pane>
    </el-tabs>
  </div>
</template>
