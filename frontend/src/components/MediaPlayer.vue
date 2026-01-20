<script setup lang="ts">
import { ref, computed, watch, onMounted, onUnmounted } from "vue"
import { ElIcon } from "element-plus"
import { VideoPlay, VideoPause, Headset } from "@element-plus/icons-vue"
import type { Subtitle } from "@/types"

const props = defineProps<{
  mediaUrl?: string
  mediaType: "video" | "audio"
  subtitles: Subtitle[]
  backgroundUrl?: string
}>()

const emit = defineEmits<{
  (e: "timeUpdate", time: number): void
  (e: "subtitleClick", subtitle: Subtitle): void
}>()

const videoRef = ref<HTMLVideoElement | null>(null)
const audioRef = ref<HTMLAudioElement | null>(null)
const currentTime = ref(0)
const duration = ref(0)
const isPlaying = ref(false)

// Find current subtitle based on playback time
const currentSubtitle = computed(() => {
  const timeMs = currentTime.value * 1000
  return props.subtitles.find(
    (sub) => timeMs >= sub.startTime && timeMs <= sub.endTime
  )
})

// Find surrounding subtitles for display
const visibleSubtitles = computed(() => {
  const currentIdx = props.subtitles.findIndex((sub) => sub === currentSubtitle.value)
  if (currentIdx === -1) {
    // Show subtitles around current time
    const timeMs = currentTime.value * 1000
    const closestIdx = props.subtitles.findIndex((sub) => sub.startTime > timeMs)
    const idx = closestIdx > 0 ? closestIdx - 1 : 0
    return props.subtitles.slice(Math.max(0, idx - 2), idx + 5)
  }
  return props.subtitles.slice(Math.max(0, currentIdx - 2), currentIdx + 5)
})

const mediaElement = computed(() => {
  return props.mediaType === "video" ? videoRef.value : audioRef.value
})

const handleTimeUpdate = () => {
  if (mediaElement.value) {
    currentTime.value = mediaElement.value.currentTime
    emit("timeUpdate", currentTime.value)
  }
}

const handleLoadedMetadata = () => {
  if (mediaElement.value) {
    duration.value = mediaElement.value.duration
  }
}

const handlePlay = () => {
  isPlaying.value = true
}

const handlePause = () => {
  isPlaying.value = false
}

const togglePlay = () => {
  if (mediaElement.value) {
    if (isPlaying.value) {
      mediaElement.value.pause()
    } else {
      mediaElement.value.play()
    }
  }
}

const seekTo = (time: number) => {
  if (mediaElement.value) {
    mediaElement.value.currentTime = time
  }
}

const handleSubtitleClick = (subtitle: Subtitle) => {
  seekTo(subtitle.startTime / 1000)
  emit("subtitleClick", subtitle)
}

const formatTime = (seconds: number): string => {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`
  }
  return `${m}:${s.toString().padStart(2, "0")}`
}

// Expose methods for parent components
defineExpose({
  seekTo,
  togglePlay,
  get currentTime() {
    return currentTime.value
  },
})
</script>

<template>
  <div class="media-player" :class="{ 'is-audio': mediaType === 'audio' }">
    <!-- Video player -->
    <div v-if="mediaType === 'video'" class="video-container">
      <video
        ref="videoRef"
        :src="mediaUrl"
        class="video-element"
        @timeupdate="handleTimeUpdate"
        @loadedmetadata="handleLoadedMetadata"
        @play="handlePlay"
        @pause="handlePause"
      />
      <div class="video-overlay" @click="togglePlay">
        <transition name="fade">
          <div v-if="!isPlaying" class="play-button-large">
            <el-icon><VideoPlay /></el-icon>
          </div>
        </transition>
      </div>
    </div>

    <!-- Audio player with background -->
    <div v-else class="audio-container">
      <div
        class="audio-background"
        :style="backgroundUrl ? { backgroundImage: `url(${backgroundUrl})` } : {}"
      >
        <div class="audio-background-overlay" />
        <div class="audio-icon">
          <el-icon><Headset /></el-icon>
        </div>
      </div>
      <audio
        ref="audioRef"
        :src="mediaUrl"
        @timeupdate="handleTimeUpdate"
        @loadedmetadata="handleLoadedMetadata"
        @play="handlePlay"
        @pause="handlePause"
      />
    </div>

    <!-- Controls -->
    <div class="player-controls">
      <button class="control-btn play-btn" @click="togglePlay">
        <el-icon v-if="isPlaying"><VideoPause /></el-icon>
        <el-icon v-else><VideoPlay /></el-icon>
      </button>
      <div class="time-display">
        <span>{{ formatTime(currentTime) }}</span>
        <span class="time-separator">/</span>
        <span>{{ formatTime(duration) }}</span>
      </div>
      <div class="progress-bar" @click="(e) => {
        const rect = (e.target as HTMLElement).getBoundingClientRect()
        const percent = (e.clientX - rect.left) / rect.width
        seekTo(percent * duration)
      }">
        <div class="progress-fill" :style="{ width: `${(currentTime / duration) * 100}%` }" />
      </div>
    </div>

    <!-- Subtitles panel -->
    <div class="subtitles-panel">
      <div class="subtitles-header">字幕</div>
      <div class="subtitles-list">
        <div
          v-for="sub in visibleSubtitles"
          :key="sub.index"
          class="subtitle-item"
          :class="{ active: sub === currentSubtitle }"
          @click="handleSubtitleClick(sub)"
        >
          <span class="subtitle-time">{{ formatTime(sub.startTime / 1000) }}</span>
          <span v-if="sub.speaker" class="subtitle-speaker">[{{ sub.speaker }}]</span>
          <span class="subtitle-text">{{ sub.text }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.media-player {
  display: flex;
  flex-direction: column;
  background: var(--bg-elevated);
  border-radius: var(--border-radius);
  overflow: hidden;
}

/* Video container */
.video-container {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  background: #000;
}

.video-element {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.video-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}

.play-button-large {
  width: 80px;
  height: 80px;
  background: rgba(0, 0, 0, 0.6);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 40px;
}

/* Audio container */
.audio-container {
  position: relative;
  width: 100%;
  aspect-ratio: 16 / 9;
  background: linear-gradient(135deg, var(--primary-color) 0%, var(--primary-dark) 100%);
}

.audio-background {
  width: 100%;
  height: 100%;
  background-size: cover;
  background-position: center;
  display: flex;
  align-items: center;
  justify-content: center;
}

.audio-background-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
}

.audio-icon {
  position: relative;
  z-index: 1;
  font-size: 80px;
  color: rgba(255, 255, 255, 0.8);
}

/* Controls */
.player-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: var(--bg-base);
  border-top: 1px solid var(--border-color);
}

.control-btn {
  width: 40px;
  height: 40px;
  border: none;
  background: var(--primary-color);
  color: #fff;
  border-radius: 50%;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  transition: background 0.15s;
}

.control-btn:hover {
  background: var(--primary-dark);
}

.time-display {
  font-size: 13px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.time-separator {
  margin: 0 4px;
  color: var(--text-muted);
}

.progress-bar {
  flex: 1;
  height: 6px;
  background: var(--border-color);
  border-radius: 3px;
  cursor: pointer;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: var(--primary-color);
  border-radius: 3px;
  transition: width 0.1s linear;
}

/* Subtitles panel */
.subtitles-panel {
  border-top: 1px solid var(--border-color);
  max-height: 200px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.subtitles-header {
  padding: 10px 16px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-secondary);
  background: var(--bg-base);
  border-bottom: 1px solid var(--border-color);
}

.subtitles-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px 0;
}

.subtitle-item {
  padding: 8px 16px;
  cursor: pointer;
  display: flex;
  align-items: flex-start;
  gap: 8px;
  transition: background 0.15s;
}

.subtitle-item:hover {
  background: var(--bg-base);
}

.subtitle-item.active {
  background: var(--primary-bg);
}

.subtitle-time {
  font-size: 12px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
  width: 50px;
}

.subtitle-speaker {
  font-size: 12px;
  color: var(--primary-color);
  font-weight: 500;
  flex-shrink: 0;
}

.subtitle-text {
  font-size: 14px;
  color: var(--text-primary);
  line-height: 1.4;
}

.subtitle-item.active .subtitle-text {
  color: var(--primary-color);
  font-weight: 500;
}

/* Animations */
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s;
}

.fade-enter-from,
.fade-leave-to {
  opacity: 0;
}
</style>
</script>
