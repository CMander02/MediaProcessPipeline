<script setup lang="ts">
import { ref, watch } from "vue"
import { ElDialog, ElTabs, ElTabPane, ElSkeleton, ElIcon } from "element-plus"
import { Document, Cpu, Share, FolderOpened, Calendar } from "@element-plus/icons-vue"
import type { ArchiveItem } from "@/types"

const props = defineProps<{
  archive: ArchiveItem | null
  visible: boolean
}>()

const emit = defineEmits<{
  (e: "update:visible", value: boolean): void
}>()

const content = ref<{
  transcript?: string
  summary?: string
  mindmap?: string
}>({})
const loading = ref(false)
const activeTab = ref("transcript")

watch(
  () => [props.archive, props.visible],
  ([archive, visible]) => {
    if (!archive || !visible) return

    loading.value = true
    // In production, fetch actual content from backend
    setTimeout(() => {
      content.value = {
        transcript: (archive as ArchiveItem).has_transcript
          ? `[Transcript content for "${(archive as ArchiveItem).title}" would be loaded here from ${(archive as ArchiveItem).path}]`
          : undefined,
        summary: (archive as ArchiveItem).has_summary
          ? `[Summary content for "${(archive as ArchiveItem).title}" would be loaded here]`
          : undefined,
        mindmap: (archive as ArchiveItem).has_mindmap
          ? `[Mindmap content for "${(archive as ArchiveItem).title}" would be loaded here]`
          : undefined,
      }
      loading.value = false
    }, 300)
  },
  { immediate: true }
)

const handleClose = () => {
  emit("update:visible", false)
}
</script>

<template>
  <el-dialog
    :model-value="visible"
    @update:model-value="handleClose"
    :title="archive?.title || 'Archive'"
    width="800px"
    :close-on-click-modal="true"
  >
    <div v-if="archive">
      <el-tabs v-model="activeTab">
        <el-tab-pane v-if="archive.has_transcript" label="Transcript" name="transcript">
          <template #label>
            <span class="flex items-center gap-1">
              <el-icon><Document /></el-icon>
              Transcript
            </span>
          </template>
        </el-tab-pane>
        <el-tab-pane v-if="archive.has_summary" label="Summary" name="summary">
          <template #label>
            <span class="flex items-center gap-1">
              <el-icon><Cpu /></el-icon>
              Summary
            </span>
          </template>
        </el-tab-pane>
        <el-tab-pane v-if="archive.has_mindmap" label="Mindmap" name="mindmap">
          <template #label>
            <span class="flex items-center gap-1">
              <el-icon><Share /></el-icon>
              Mindmap
            </span>
          </template>
        </el-tab-pane>
      </el-tabs>

      <div v-if="loading" class="mt-4">
        <el-skeleton :rows="5" animated />
      </div>

      <div v-else class="mt-4">
        <div
          v-if="activeTab === 'transcript' && archive.has_transcript"
          class="border border-[var(--border-color)] rounded-lg p-4 max-h-[400px] overflow-auto"
        >
          <pre class="whitespace-pre-wrap text-sm">{{ content.transcript }}</pre>
        </div>
        <div
          v-if="activeTab === 'summary' && archive.has_summary"
          class="border border-[var(--border-color)] rounded-lg p-4 max-h-[400px] overflow-auto"
        >
          <div class="text-sm">{{ content.summary }}</div>
        </div>
        <div
          v-if="activeTab === 'mindmap' && archive.has_mindmap"
          class="border border-[var(--border-color)] rounded-lg p-4 max-h-[400px] overflow-auto"
        >
          <pre class="whitespace-pre-wrap text-sm font-mono">{{ content.mindmap }}</pre>
        </div>
      </div>

      <div class="flex-between mt-4 text-xs text-[var(--text-muted)]">
        <span class="flex items-center gap-1">
          <el-icon><FolderOpened /></el-icon>
          {{ archive.path }}
        </span>
        <span class="flex items-center gap-1">
          <el-icon><Calendar /></el-icon>
          {{ archive.date }}
        </span>
      </div>
    </div>
  </el-dialog>
</template>
