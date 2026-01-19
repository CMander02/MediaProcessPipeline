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
  ElAlert,
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
  <div class="page-container">
    <!-- Status Alert -->
    <el-alert
      v-if="backendStatus === 'offline'"
      title="Backend Unavailable"
      type="warning"
      description="Cannot connect to the backend server. Some features may not work properly."
      show-icon
      :closable="false"
      class="status-alert"
    />

    <!-- Header -->
    <div class="page-header flex-between">
      <div>
        <h1 class="page-title">Settings</h1>
        <p class="page-description">Configure pipeline processing options</p>
      </div>
      <div class="header-actions">
        <el-tag
          :type="backendStatus === 'online' ? 'success' : backendStatus === 'offline' ? 'danger' : 'info'"
          class="status-tag"
          effect="plain"
        >
          <el-icon class="status-icon">
            <Loading v-if="backendStatus === 'checking'" class="animate-spin" />
            <CircleCheck v-else-if="backendStatus === 'online'" />
            <WarningFilled v-else />
          </el-icon>
          Backend {{ backendStatus }}
        </el-tag>
        <el-button type="primary" :loading="saving" @click="saveSettings">
          <el-icon v-if="!saving && saved" class="mr-1"><CircleCheck /></el-icon>
          {{ saved ? "Saved!" : "Save Changes" }}
        </el-button>
      </div>
    </div>

    <!-- Settings Tabs -->
    <div class="settings-container">
      <el-tabs v-model="activeTab" class="settings-tabs">
        <!-- API Tab -->
        <el-tab-pane name="api">
          <template #label>
            <span class="tab-label">
              <el-icon><Cpu /></el-icon>
              AI & API
            </span>
          </template>

          <div class="settings-section">
            <div class="section-header">
              <h3 class="section-title">AI & API Configuration</h3>
              <p class="section-description">Configure LLM providers for text analysis and summarization</p>
            </div>

            <div class="form-grid">
              <div class="form-group">
                <label class="form-label">LLM Provider</label>
                <el-select
                  :model-value="settings.llm_provider"
                  @update:model-value="updateSetting('llm_provider', $event as 'anthropic' | 'openai')"
                  class="form-input"
                >
                  <el-option value="anthropic" label="Anthropic (Claude)" />
                  <el-option value="openai" label="OpenAI (GPT)" />
                </el-select>
              </div>

              <div class="form-group">
                <label class="form-label">Model</label>
                <el-select
                  :model-value="settings.llm_model"
                  @update:model-value="updateSetting('llm_model', $event as string)"
                  class="form-input"
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

            <el-divider />

            <div class="form-grid">
              <div class="form-group full-width">
                <label class="form-label">Anthropic API Key</label>
                <el-input
                  type="password"
                  :model-value="settings.anthropic_api_key"
                  @update:model-value="updateSetting('anthropic_api_key', $event)"
                  placeholder="sk-ant-api03-..."
                  show-password
                  class="form-input"
                />
                <p class="form-hint">Required for Claude models</p>
              </div>

              <div class="form-group full-width">
                <label class="form-label">OpenAI API Key</label>
                <el-input
                  type="password"
                  :model-value="settings.openai_api_key"
                  @update:model-value="updateSetting('openai_api_key', $event)"
                  placeholder="sk-..."
                  show-password
                  class="form-input"
                />
                <p class="form-hint">Required for GPT models</p>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <!-- Transcription Tab -->
        <el-tab-pane name="transcription">
          <template #label>
            <span class="tab-label">
              <el-icon><Microphone /></el-icon>
              Transcription
            </span>
          </template>

          <div class="settings-section">
            <div class="section-header">
              <h3 class="section-title">Transcription Settings</h3>
              <p class="section-description">Configure WhisperX for speech recognition and speaker diarization</p>
            </div>

            <div class="form-grid">
              <div class="form-group">
                <label class="form-label">Whisper Model</label>
                <el-select
                  :model-value="settings.whisper_model"
                  @update:model-value="updateSetting('whisper_model', $event as string)"
                  class="form-input"
                >
                  <el-option
                    v-for="m in whisperModels"
                    :key="m.value"
                    :value="m.value"
                    :label="m.label"
                  />
                </el-select>
              </div>

              <div class="form-group">
                <label class="form-label">Device</label>
                <el-select
                  :model-value="settings.whisper_device"
                  @update:model-value="updateSetting('whisper_device', $event as 'cpu' | 'cuda')"
                  class="form-input"
                >
                  <el-option value="cuda" label="CUDA (GPU)" />
                  <el-option value="cpu" label="CPU" />
                </el-select>
              </div>

              <div class="form-group">
                <label class="form-label">Compute Type</label>
                <el-select
                  :model-value="settings.whisper_compute_type"
                  @update:model-value="updateSetting('whisper_compute_type', $event as string)"
                  class="form-input"
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

            <div class="toggle-setting">
              <div class="toggle-info">
                <label class="form-label">Speaker Diarization</label>
                <p class="form-hint">Identify and label different speakers in the audio</p>
              </div>
              <el-switch
                :model-value="settings.enable_diarization"
                @update:model-value="updateSetting('enable_diarization', $event as boolean)"
              />
            </div>

            <div class="form-group full-width" style="margin-top: 20px">
              <label class="form-label">HuggingFace Token</label>
              <el-input
                type="password"
                :model-value="settings.hf_token"
                @update:model-value="updateSetting('hf_token', $event)"
                placeholder="hf_..."
                show-password
                class="form-input"
              />
              <p class="form-hint">Required for speaker diarization (pyannote models)</p>
            </div>
          </div>
        </el-tab-pane>

        <!-- Paths Tab -->
        <el-tab-pane name="paths">
          <template #label>
            <span class="tab-label">
              <el-icon><FolderOpened /></el-icon>
              Paths
            </span>
          </template>

          <div class="settings-section">
            <div class="section-header">
              <h3 class="section-title">Data Paths</h3>
              <p class="section-description">Configure storage directories for media processing</p>
            </div>

            <div class="form-grid single-column">
              <div class="form-group">
                <label class="form-label">Inbox Path</label>
                <el-input
                  :model-value="settings.inbox_path"
                  @update:model-value="updateSetting('inbox_path', $event)"
                  placeholder="./data/inbox"
                  class="form-input"
                />
                <p class="form-hint">Directory for incoming media files</p>
              </div>

              <div class="form-group">
                <label class="form-label">Processing Path</label>
                <el-input
                  :model-value="settings.processing_path"
                  @update:model-value="updateSetting('processing_path', $event)"
                  placeholder="./data/processing"
                  class="form-input"
                />
                <p class="form-hint">Temporary storage during processing</p>
              </div>

              <div class="form-group">
                <label class="form-label">Outputs Path</label>
                <el-input
                  :model-value="settings.outputs_path"
                  @update:model-value="updateSetting('outputs_path', $event)"
                  placeholder="./data/outputs"
                  class="form-input"
                />
                <p class="form-hint">Final processed outputs</p>
              </div>

              <el-divider />

              <div class="form-group">
                <label class="form-label">Archive Path</label>
                <el-input
                  :model-value="settings.archive_path"
                  @update:model-value="updateSetting('archive_path', $event)"
                  placeholder="./data/archive"
                  class="form-input"
                />
              </div>

              <div class="form-group">
                <label class="form-label">Obsidian Vault Path (Optional)</label>
                <el-input
                  :model-value="settings.obsidian_vault_path"
                  @update:model-value="updateSetting('obsidian_vault_path', $event)"
                  placeholder="C:\Users\...\ObsidianVault"
                  class="form-input"
                />
                <p class="form-hint">Auto-sync archives to your Obsidian vault</p>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <!-- Processing Tab -->
        <el-tab-pane name="processing">
          <template #label>
            <span class="tab-label">
              <el-icon><Setting /></el-icon>
              Processing
            </span>
          </template>

          <div class="settings-section">
            <div class="section-header">
              <h3 class="section-title">Audio Processing</h3>
              <p class="section-description">Configure UVR5 for vocal separation</p>
            </div>

            <div class="form-grid">
              <div class="form-group">
                <label class="form-label">UVR Model</label>
                <el-select
                  :model-value="settings.uvr_model"
                  @update:model-value="updateSetting('uvr_model', $event as string)"
                  class="form-input"
                >
                  <el-option
                    v-for="m in uvrModels"
                    :key="m.value"
                    :value="m.value"
                    :label="m.label"
                  />
                </el-select>
              </div>

              <div class="form-group">
                <label class="form-label">Device</label>
                <el-select
                  :model-value="settings.uvr_device"
                  @update:model-value="updateSetting('uvr_device', $event as 'cpu' | 'cuda')"
                  class="form-input"
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
  </div>
</template>

<style scoped>
.status-alert {
  margin-bottom: 24px;
  border-radius: var(--border-radius);
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 16px;
}

.status-tag {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  font-weight: 500;
}

.status-icon {
  font-size: 14px;
}

.settings-container {
  background: var(--bg-elevated);
  border-radius: var(--border-radius);
  box-shadow: var(--shadow-sm);
  overflow: hidden;
}

.settings-tabs :deep(.el-tabs__header) {
  margin: 0;
  padding: 0 24px;
  background: var(--bg-base);
  border-bottom: 1px solid var(--border-color);
}

.settings-tabs :deep(.el-tabs__nav-wrap::after) {
  display: none;
}

.settings-tabs :deep(.el-tabs__item) {
  height: 56px;
  line-height: 56px;
  padding: 0 20px;
}

.tab-label {
  display: flex;
  align-items: center;
  gap: 8px;
}

.settings-tabs :deep(.el-tabs__content) {
  padding: 0;
}

.settings-section {
  padding: 32px;
}

.section-header {
  margin-bottom: 28px;
}

.section-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 6px;
}

.section-description {
  font-size: 14px;
  color: var(--text-muted);
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 24px;
}

.form-grid.single-column {
  grid-template-columns: 1fr;
  max-width: 600px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.form-group.full-width {
  grid-column: 1 / -1;
}

.form-label {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-secondary);
}

.form-input {
  width: 100%;
}

.form-hint {
  font-size: 13px;
  color: var(--text-muted);
  margin-top: 4px;
}

.toggle-setting {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  background: var(--bg-base);
  border-radius: var(--border-radius-sm);
}

.toggle-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.toggle-info .form-label {
  margin-bottom: 0;
}

.toggle-info .form-hint {
  margin-top: 0;
}

@media (max-width: 768px) {
  .form-grid {
    grid-template-columns: 1fr;
  }

  .settings-section {
    padding: 20px;
  }
}
</style>
