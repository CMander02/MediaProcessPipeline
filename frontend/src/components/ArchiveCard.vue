<script setup lang="ts">
import { ElTag, ElIcon } from "element-plus"
import { Calendar, Document, Cpu, Share } from "@element-plus/icons-vue"
import type { ArchiveItem } from "@/types"

defineProps<{
  archive: ArchiveItem
}>()

defineEmits<{
  (e: "click"): void
}>()
</script>

<template>
  <div class="archive-card" @click="$emit('click')">
    <h3 class="archive-card-title">{{ archive.title }}</h3>
    <div class="archive-card-date">
      <el-icon><Calendar /></el-icon>
      {{ archive.date }}
    </div>
    <div class="archive-card-badges">
      <el-tag v-if="archive.has_transcript" size="small" type="info">
        <el-icon class="mr-1"><Document /></el-icon>
        Transcript
      </el-tag>
      <el-tag v-if="archive.has_summary" size="small" type="info">
        <el-icon class="mr-1"><Cpu /></el-icon>
        Summary
      </el-tag>
      <el-tag v-if="archive.has_mindmap" size="small" type="info">
        <el-icon class="mr-1"><Share /></el-icon>
        Mindmap
      </el-tag>
    </div>
  </div>
</template>
