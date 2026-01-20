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
  ElRadioGroup,
  ElRadioButton,
} from "element-plus"
import {
  CircleCheck,
  WarningFilled,
  Loading,
  Cpu,
  Microphone,
  FolderOpened,
  Setting,
  Sunny,
  Moon,
  Monitor,
} from "@element-plus/icons-vue"
import { useSettings } from "@/composables/useSettings"
import { useTheme, type ThemeMode } from "@/composables/useTheme"
import { useLocale, type LocaleCode } from "@/composables/useLocale"
import { healthCheck } from "@/api"

const { settings, saving, saved, saveSettings, updateSetting } = useSettings()
const { themeMode, setTheme } = useTheme()
const { currentLocale, setLocale, t } = useLocale()

const backendStatus = ref<"checking" | "online" | "offline">("checking")
const activeTab = ref("appearance")

onMounted(async () => {
  try {
    await healthCheck()
    backendStatus.value = "online"
  } catch {
    backendStatus.value = "offline"
  }
})

const handleThemeChange = (mode: ThemeMode) => {
  setTheme(mode)
}

const handleLocaleChange = (locale: LocaleCode) => {
  setLocale(locale)
}

const whisperModels = [
  { value: "large-v3", label: "Large V3 (Best)" },
  { value: "large-v3-turbo", label: "Large V3 Turbo" },
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
        <h1 class="page-title">{{ t('settings.title') }}</h1>
        <p class="page-description">{{ t('settings.description') }}</p>
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
          {{ backendStatus === 'online' ? t('settings.backendOnline') : backendStatus === 'offline' ? t('settings.backendOffline') : t('settings.backendChecking') }}
        </el-tag>
        <el-button type="primary" :loading="saving" @click="saveSettings">
          <el-icon v-if="!saving && saved" class="mr-1"><CircleCheck /></el-icon>
          {{ saved ? t('settings.saved') : t('settings.save') }}
        </el-button>
      </div>
    </div>

    <!-- Settings Tabs -->
    <div class="settings-container">
      <el-tabs v-model="activeTab" class="settings-tabs">
        <!-- Appearance Tab -->
        <el-tab-pane name="appearance">
          <template #label>
            <span class="tab-label">
              <el-icon><Sunny /></el-icon>
              {{ t('settings.appearance') }}
            </span>
          </template>

          <div class="settings-section">
            <div class="section-header">
              <h3 class="section-title">{{ t('settings.appearance') }}</h3>
              <p class="section-description">{{ t('settings.appearanceDesc') }}</p>
            </div>

            <!-- Theme Selection -->
            <div class="appearance-option">
              <div class="option-info">
                <label class="form-label">{{ t('settings.theme') }}</label>
              </div>
              <div class="theme-selector">
                <button
                  class="theme-btn"
                  :class="{ active: themeMode === 'light' }"
                  @click="handleThemeChange('light')"
                >
                  <el-icon><Sunny /></el-icon>
                  <span>{{ t('settings.themeLight') }}</span>
                </button>
                <button
                  class="theme-btn"
                  :class="{ active: themeMode === 'dark' }"
                  @click="handleThemeChange('dark')"
                >
                  <el-icon><Moon /></el-icon>
                  <span>{{ t('settings.themeDark') }}</span>
                </button>
                <button
                  class="theme-btn"
                  :class="{ active: themeMode === 'system' }"
                  @click="handleThemeChange('system')"
                >
                  <el-icon><Monitor /></el-icon>
                  <span>{{ t('settings.themeSystem') }}</span>
                </button>
              </div>
            </div>

            <el-divider />

            <!-- Language Selection -->
            <div class="appearance-option">
              <div class="option-info">
                <label class="form-label">{{ t('settings.language') }}</label>
              </div>
              <div class="language-selector">
                <button
                  class="lang-btn"
                  :class="{ active: currentLocale === 'zh' }"
                  @click="handleLocaleChange('zh')"
                >
                  中文
                </button>
                <button
                  class="lang-btn"
                  :class="{ active: currentLocale === 'en' }"
                  @click="handleLocaleChange('en')"
                >
                  English
                </button>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <!-- API Tab - Left-Right Split Layout -->
        <el-tab-pane name="api">
          <template #label>
            <span class="tab-label">
              <el-icon><Cpu /></el-icon>
              AI & API
            </span>
          </template>

          <div class="settings-section">
            <div class="section-header">
              <h3 class="section-title">LLM Provider</h3>
              <p class="section-description">选择用于文本分析和摘要的 AI 服务</p>
            </div>

            <!-- Left-Right Split Layout -->
            <div class="llm-split-layout">
              <!-- Left: Provider List -->
              <div class="provider-list">
                <button
                  class="provider-list-btn"
                  :class="{ active: settings.llm_provider === 'anthropic' }"
                  @click="updateSetting('llm_provider', 'anthropic')"
                >
                  <div class="provider-list-icon">A</div>
                  <div class="provider-list-info">
                    <span class="provider-list-name">Anthropic</span>
                    <span class="provider-list-desc">Claude 系列模型</span>
                  </div>
                  <span v-if="settings.llm_provider === 'anthropic'" class="provider-active-badge">当前</span>
                </button>
                <button
                  class="provider-list-btn"
                  :class="{ active: settings.llm_provider === 'openai' }"
                  @click="updateSetting('llm_provider', 'openai')"
                >
                  <div class="provider-list-icon">O</div>
                  <div class="provider-list-info">
                    <span class="provider-list-name">OpenAI</span>
                    <span class="provider-list-desc">GPT 系列模型</span>
                  </div>
                  <span v-if="settings.llm_provider === 'openai'" class="provider-active-badge">当前</span>
                </button>
                <button
                  class="provider-list-btn"
                  :class="{ active: settings.llm_provider === 'custom' }"
                  @click="updateSetting('llm_provider', 'custom')"
                >
                  <div class="provider-list-icon">C</div>
                  <div class="provider-list-info">
                    <span class="provider-list-name">{{ settings.custom_name || 'Custom' }}</span>
                    <span class="provider-list-desc">OpenAI Compatible</span>
                  </div>
                  <span v-if="settings.llm_provider === 'custom'" class="provider-active-badge">当前</span>
                </button>
              </div>

              <!-- Right: Provider Config -->
              <div class="provider-config-panel">
                <!-- Anthropic Config -->
                <div v-if="settings.llm_provider === 'anthropic'" class="provider-config">
                  <h4 class="config-title">Anthropic 配置</h4>
                  <div class="config-form">
                    <div class="form-group">
                      <label class="form-label">Model</label>
                      <el-select
                        :model-value="settings.anthropic_model"
                        @update:model-value="updateSetting('anthropic_model', $event as string)"
                        class="form-input"
                      >
                        <el-option value="claude-sonnet-4-20250514" label="Claude Sonnet 4" />
                        <el-option value="claude-opus-4-20250514" label="Claude Opus 4" />
                        <el-option value="claude-3-5-haiku-20241022" label="Claude 3.5 Haiku" />
                      </el-select>
                    </div>
                    <div class="form-group">
                      <label class="form-label">API Base <span class="optional">(可选)</span></label>
                      <el-input
                        :model-value="settings.anthropic_api_base"
                        @update:model-value="updateSetting('anthropic_api_base', $event)"
                        placeholder="留空使用官方 API"
                        class="form-input"
                      />
                    </div>
                    <div class="form-group">
                      <label class="form-label">API Key</label>
                      <el-input
                        type="password"
                        :model-value="settings.anthropic_api_key"
                        @update:model-value="updateSetting('anthropic_api_key', $event)"
                        placeholder="sk-ant-api03-..."
                        show-password
                        class="form-input"
                      />
                    </div>
                  </div>
                </div>

                <!-- OpenAI Config -->
                <div v-else-if="settings.llm_provider === 'openai'" class="provider-config">
                  <h4 class="config-title">OpenAI 配置</h4>
                  <div class="config-form">
                    <div class="form-group">
                      <label class="form-label">Model</label>
                      <el-select
                        :model-value="settings.openai_model"
                        @update:model-value="updateSetting('openai_model', $event as string)"
                        class="form-input"
                      >
                        <el-option value="gpt-4o" label="GPT-4o" />
                        <el-option value="gpt-4o-mini" label="GPT-4o mini" />
                        <el-option value="gpt-4-turbo" label="GPT-4 Turbo" />
                        <el-option value="o1" label="o1" />
                        <el-option value="o1-mini" label="o1-mini" />
                      </el-select>
                    </div>
                    <div class="form-group">
                      <label class="form-label">API Base <span class="optional">(可选)</span></label>
                      <el-input
                        :model-value="settings.openai_api_base"
                        @update:model-value="updateSetting('openai_api_base', $event)"
                        placeholder="留空使用官方 API"
                        class="form-input"
                      />
                    </div>
                    <div class="form-group">
                      <label class="form-label">API Key</label>
                      <el-input
                        type="password"
                        :model-value="settings.openai_api_key"
                        @update:model-value="updateSetting('openai_api_key', $event)"
                        placeholder="sk-..."
                        show-password
                        class="form-input"
                      />
                    </div>
                  </div>
                </div>

                <!-- Custom OpenAI Compatible Config -->
                <div v-else-if="settings.llm_provider === 'custom'" class="provider-config">
                  <h4 class="config-title">自定义 OpenAI Compatible 配置</h4>
                  <div class="config-form">
                    <div class="form-group">
                      <label class="form-label">Provider Name</label>
                      <el-input
                        :model-value="settings.custom_name"
                        @update:model-value="updateSetting('custom_name', $event)"
                        placeholder="e.g. Ollama, DeepSeek, Groq"
                        class="form-input"
                      />
                    </div>
                    <div class="form-group">
                      <label class="form-label">Model Name</label>
                      <el-input
                        :model-value="settings.custom_model"
                        @update:model-value="updateSetting('custom_model', $event)"
                        placeholder="e.g. llama3, qwen2, deepseek-chat"
                        class="form-input"
                      />
                    </div>
                    <div class="form-group">
                      <label class="form-label">API Base URL</label>
                      <el-input
                        :model-value="settings.custom_api_base"
                        @update:model-value="updateSetting('custom_api_base', $event)"
                        placeholder="e.g. http://localhost:11434/v1"
                        class="form-input"
                      />
                      <p class="form-hint">OpenAI Compatible API endpoint</p>
                    </div>
                    <div class="form-group">
                      <label class="form-label">API Key <span class="optional">(如需要)</span></label>
                      <el-input
                        type="password"
                        :model-value="settings.custom_api_key"
                        @update:model-value="updateSetting('custom_api_key', $event)"
                        placeholder="留空表示无需认证 (如本地 Ollama)"
                        show-password
                        class="form-input"
                      />
                    </div>
                  </div>
                </div>
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

              <div class="form-group full-width">
                <label class="form-label">Whisper Model Path</label>
                <el-input
                  :model-value="settings.whisper_model_path"
                  @update:model-value="updateSetting('whisper_model_path', $event)"
                  placeholder="D:/models/whisper-large-v3"
                  class="form-input"
                />
                <p class="form-hint">Local path to Whisper model. Leave empty to auto-download.</p>
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
              <p class="form-hint">Required for speaker diarization (pyannote models), or use local paths below</p>
            </div>

            <el-divider />

            <div class="section-header">
              <h3 class="section-title">Diarization Model Paths</h3>
              <p class="section-description">Local paths for pyannote models. Leave empty to use HuggingFace.</p>
            </div>

            <div class="form-grid single-column">
              <div class="form-group">
                <label class="form-label">Pyannote Speaker Diarization</label>
                <el-input
                  :model-value="settings.pyannote_model_path"
                  @update:model-value="updateSetting('pyannote_model_path', $event)"
                  placeholder="D:/models/pyannote/speaker-diarization-3.1"
                  class="form-input"
                />
              </div>

              <div class="form-group">
                <label class="form-label">Pyannote Segmentation</label>
                <el-input
                  :model-value="settings.pyannote_segmentation_path"
                  @update:model-value="updateSetting('pyannote_segmentation_path', $event)"
                  placeholder="D:/models/pyannote/segmentation-3.0"
                  class="form-input"
                />
              </div>
            </div>

            <el-divider />

            <div class="section-header">
              <h3 class="section-title">Alignment Model Paths</h3>
              <p class="section-description">wav2vec2 models for word-level alignment</p>
            </div>

            <div class="form-grid single-column">
              <div class="form-group">
                <label class="form-label">Chinese Alignment Model</label>
                <el-input
                  :model-value="settings.alignment_model_zh"
                  @update:model-value="updateSetting('alignment_model_zh', $event)"
                  placeholder="D:/models/wav2vec2-large-xlsr-53-chinese-zh-cn"
                  class="form-input"
                />
                <p class="form-hint">jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn</p>
              </div>

              <div class="form-group">
                <label class="form-label">English Alignment Model</label>
                <el-input
                  :model-value="settings.alignment_model_en"
                  @update:model-value="updateSetting('alignment_model_en', $event)"
                  placeholder="Leave empty to use torchaudio built-in"
                  class="form-input"
                />
                <p class="form-hint">Leave empty to use torchaudio WAV2VEC2_ASR_BASE_960H</p>
              </div>
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
                <label class="form-label">数据根目录</label>
                <el-input
                  :model-value="settings.data_root"
                  @update:model-value="updateSetting('data_root', $event)"
                  placeholder="./data"
                  class="form-input"
                />
                <p class="form-hint">所有任务输出到 data/{task_id}/ 目录下，包含原始文件、转录、摘要等</p>
              </div>

              <el-divider />

              <div class="form-group">
                <label class="form-label">Obsidian Vault Path (Optional)</label>
                <el-input
                  :model-value="settings.obsidian_vault_path"
                  @update:model-value="updateSetting('obsidian_vault_path', $event)"
                  placeholder="C:\Users\...\ObsidianVault"
                  class="form-input"
                />
                <p class="form-hint">自动同步 markdown 文件到 Obsidian vault</p>
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

              <div class="form-group full-width">
                <label class="form-label">UVR Model Directory</label>
                <el-input
                  :model-value="settings.uvr_model_dir"
                  @update:model-value="updateSetting('uvr_model_dir', $event)"
                  placeholder="C:/Users/.../AppData/Local/Programs/Ultimate Vocal Remover/models"
                  class="form-input"
                />
                <p class="form-hint">Base directory for UVR models. Individual paths below take priority.</p>
              </div>
            </div>

            <el-divider />

            <div class="section-header">
              <h3 class="section-title">UVR Model Paths</h3>
              <p class="section-description">Specific paths for each UVR model (optional)</p>
            </div>

            <div class="form-grid single-column">
              <div class="form-group">
                <label class="form-label">UVR-MDX-NET-Inst_HQ_3</label>
                <el-input
                  :model-value="settings.uvr_mdx_inst_hq3_path"
                  @update:model-value="updateSetting('uvr_mdx_inst_hq3_path', $event)"
                  placeholder="MDX_Net_Models/UVR-MDX-NET-Inst_HQ_3.onnx"
                  class="form-input"
                />
                <p class="form-hint">MDX-Net instrumental separation model</p>
              </div>

              <div class="form-group">
                <label class="form-label">1_HP-UVR</label>
                <el-input
                  :model-value="settings.uvr_hp_uvr_path"
                  @update:model-value="updateSetting('uvr_hp_uvr_path', $event)"
                  placeholder="VR_Models/1_HP-UVR.pth"
                  class="form-input"
                />
                <p class="form-hint">VR architecture vocal separation</p>
              </div>

              <div class="form-group">
                <label class="form-label">UVR-DeNoise-Lite</label>
                <el-input
                  :model-value="settings.uvr_denoise_lite_path"
                  @update:model-value="updateSetting('uvr_denoise_lite_path', $event)"
                  placeholder="VR_Models/UVR-DeNoise-Lite.pth"
                  class="form-input"
                />
                <p class="form-hint">Lightweight denoising model</p>
              </div>

              <div class="form-group">
                <label class="form-label">Kim_Vocal_2</label>
                <el-input
                  :model-value="settings.uvr_kim_vocal_2_path"
                  @update:model-value="updateSetting('uvr_kim_vocal_2_path', $event)"
                  placeholder="Kim_Vocal_2.onnx"
                  class="form-input"
                />
                <p class="form-hint">Best vocal extraction model</p>
              </div>

              <div class="form-group">
                <label class="form-label">UVR-DeEcho-DeReverb</label>
                <el-input
                  :model-value="settings.uvr_deecho_dereverb_path"
                  @update:model-value="updateSetting('uvr_deecho_dereverb_path', $event)"
                  placeholder="VR_Models/UVR-DeEcho-DeReverb.pth"
                  class="form-input"
                />
                <p class="form-hint">Echo and reverb removal</p>
              </div>

              <div class="form-group">
                <label class="form-label">htdemucs</label>
                <el-input
                  :model-value="settings.uvr_htdemucs_path"
                  @update:model-value="updateSetting('uvr_htdemucs_path', $event)"
                  placeholder="Demucs_Models/v3_v4_repo/..."
                  class="form-input"
                />
                <p class="form-hint">Demucs v4 4-stem separation</p>
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

/* Appearance Options */
.appearance-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 0;
}

.option-info {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.theme-selector {
  display: flex;
  gap: 8px;
}

.theme-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  background: var(--bg-base);
  border: 2px solid var(--border-color);
  border-radius: 8px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s ease;
  font-size: 14px;
  font-weight: 500;
}

.theme-btn:hover {
  border-color: var(--text-muted);
  color: var(--text-primary);
}

.theme-btn.active {
  border-color: var(--primary-color);
  background: var(--primary-bg);
  color: var(--primary-color);
}

.language-selector {
  display: flex;
  gap: 8px;
}

.lang-btn {
  padding: 10px 20px;
  background: var(--bg-base);
  border: 2px solid var(--border-color);
  border-radius: 8px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.15s ease;
  font-size: 14px;
  font-weight: 500;
}

.lang-btn:hover {
  border-color: var(--text-muted);
  color: var(--text-primary);
}

.lang-btn.active {
  border-color: var(--primary-color);
  background: var(--primary-bg);
  color: var(--primary-color);
}

/* Provider Selector */
.provider-selector {
  display: flex;
  gap: 12px;
}

.provider-btn {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 20px 16px;
  background: var(--bg-base);
  border: 2px solid var(--border-color);
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.provider-btn:hover {
  border-color: var(--text-muted);
}

.provider-btn.active {
  border-color: var(--primary-color);
  background: var(--primary-bg);
}

.provider-btn .provider-name {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
}

.provider-btn .provider-desc {
  font-size: 13px;
  color: var(--text-muted);
}

.provider-btn.active .provider-name {
  color: var(--primary-color);
}

/* LLM Split Layout */
.llm-split-layout {
  display: grid;
  grid-template-columns: 280px 1fr;
  gap: 24px;
  min-height: 300px;
}

.provider-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.provider-list-btn {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  background: var(--bg-base);
  border: 2px solid var(--border-color);
  border-radius: 12px;
  cursor: pointer;
  transition: all 0.15s ease;
  text-align: left;
}

.provider-list-btn:hover {
  border-color: var(--text-muted);
}

.provider-list-btn.active {
  border-color: var(--primary-color);
  background: var(--primary-bg);
}

.provider-list-icon {
  width: 40px;
  height: 40px;
  background: var(--border-color);
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  font-weight: 700;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.provider-list-btn.active .provider-list-icon {
  background: var(--primary-color);
  color: #fff;
}

.provider-list-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.provider-list-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.provider-list-btn.active .provider-list-name {
  color: var(--primary-color);
}

.provider-list-desc {
  font-size: 12px;
  color: var(--text-muted);
}

.provider-active-badge {
  padding: 3px 8px;
  background: var(--primary-color);
  color: #fff;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 600;
}

.provider-config-panel {
  background: var(--bg-base);
  border-radius: 12px;
  padding: 24px;
  border: 1px solid var(--border-color);
}

.config-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  margin-bottom: 20px;
}

.config-form {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.provider-config {
  margin-top: 0;
}

.optional {
  font-weight: 400;
  color: var(--text-muted);
  font-size: 12px;
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

  .appearance-option {
    flex-direction: column;
    align-items: flex-start;
    gap: 12px;
  }

  .theme-selector,
  .language-selector {
    width: 100%;
  }

  .theme-btn,
  .lang-btn {
    flex: 1;
    justify-content: center;
  }

  .llm-split-layout {
    grid-template-columns: 1fr;
  }

  .provider-list {
    flex-direction: row;
    flex-wrap: wrap;
  }

  .provider-list-btn {
    flex: 1;
    min-width: 120px;
  }

  .provider-list-info {
    display: none;
  }

  .provider-list-icon {
    width: 32px;
    height: 32px;
    font-size: 14px;
  }

  .provider-active-badge {
    display: none;
  }
}
</style>
