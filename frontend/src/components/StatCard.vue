<script setup lang="ts">
import { ElIcon } from "element-plus"

defineProps<{
  label: string
  value: number
  iconColor?: string
}>()
</script>

<template>
  <div class="stat-card">
    <el-icon class="stat-card-icon" :style="{ color: iconColor }">
      <slot name="icon" />
    </el-icon>
    <div>
      <div class="stat-card-value">{{ value }}</div>
      <div class="stat-card-label">{{ label }}</div>
    </div>
  </div>
</template>
