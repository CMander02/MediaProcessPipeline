<script setup lang="ts">
import { ref, computed, onMounted } from "vue"
import { ElButton, ElInput, ElSkeleton, ElIcon } from "element-plus"
import { Refresh, Search, WarningFilled, FolderOpened } from "@element-plus/icons-vue"
import ArchiveCard from "@/components/ArchiveCard.vue"
import ArchiveDetailDialog from "@/components/ArchiveDetailDialog.vue"
import { pipelineApi } from "@/api"
import type { ArchiveItem } from "@/types"

const archives = ref<ArchiveItem[]>([])
const loading = ref(true)
const error = ref<string | null>(null)
const search = ref("")
const selected = ref<ArchiveItem | null>(null)
const dialogVisible = ref(false)

const fetchArchives = async () => {
  loading.value = true
  try {
    const res = await pipelineApi.archives()
    archives.value = res.archives || []
    error.value = null
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Failed to fetch archives"
  } finally {
    loading.value = false
  }
}

const filtered = computed(() =>
  archives.value.filter((a) =>
    a.title.toLowerCase().includes(search.value.toLowerCase())
  )
)

const handleArchiveClick = (archive: ArchiveItem) => {
  selected.value = archive
  dialogVisible.value = true
}

onMounted(() => {
  fetchArchives()
})
</script>

<template>
  <div class="page-container space-y-8">
    <!-- Header -->
    <div class="flex-between">
      <div>
        <h1 class="page-title">Archives</h1>
        <p class="page-description">Browse processed media results</p>
      </div>
      <el-button @click="fetchArchives">
        <el-icon class="mr-1"><Refresh /></el-icon>
        Refresh
      </el-button>
    </div>

    <!-- Search -->
    <el-input v-model="search" placeholder="Search archives..." :prefix-icon="Search" />

    <!-- Archives Grid -->
    <div class="custom-card">
      <div class="custom-card-header">
        <h2 class="custom-card-title">
          {{ search ? `Search Results (${filtered.length})` : `All Archives (${archives.length})` }}
        </h2>
        <p class="custom-card-description">Transcripts, summaries, and mindmaps</p>
      </div>

      <!-- Loading -->
      <div v-if="loading" class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <el-skeleton v-for="i in 4" :key="i" :rows="3" animated />
      </div>

      <!-- Error -->
      <div v-else-if="error" class="flex items-center gap-2 text-[#f56c6c] py-4">
        <el-icon><WarningFilled /></el-icon>
        <span>{{ error }}</span>
      </div>

      <!-- Empty -->
      <div v-else-if="filtered.length === 0" class="empty-state">
        <el-icon class="empty-state-icon"><FolderOpened /></el-icon>
        <p class="empty-state-text">
          {{ search ? "No matching archives found" : "No archives yet" }}
        </p>
        <p class="empty-state-hint">Process some media to see results here</p>
      </div>

      <!-- Archive Grid -->
      <div v-else class="grid grid-cols-1 md:grid-cols-2 gap-4 max-h-[500px] overflow-auto">
        <ArchiveCard
          v-for="archive in filtered"
          :key="archive.path"
          :archive="archive"
          @click="handleArchiveClick(archive)"
        />
      </div>
    </div>

    <!-- Archive Detail Dialog -->
    <ArchiveDetailDialog
      :archive="selected"
      v-model:visible="dialogVisible"
    />
  </div>
</template>
