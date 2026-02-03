<script setup lang="ts">
import { Icon } from '@iconify/vue'

export interface HistoryItem {
  id: string
  query: string
  status: 'pending' | 'complete' | 'error'
  timestamp: Date
  preview?: string
}

defineProps<{
  items: HistoryItem[]
}>()

const emit = defineEmits<{
  (e: 'select', item: HistoryItem): void
}>()

const formatTime = (date: Date) => {
  const now = new Date()
  const diff = now.getTime() - date.getTime()

  if (diff < 60000) return 'Just now'
  if (diff < 3600000) return `${Math.floor(diff / 60000)}m ago`
  if (diff < 86400000) return `${Math.floor(diff / 3600000)}h ago`
  return date.toLocaleDateString()
}

const truncate = (text: string, maxLen: number = 50) => {
  if (text.length <= maxLen) return text
  return text.substring(0, maxLen - 3) + '...'
}
</script>

<template>
  <div v-if="items.length" class="w-full max-w-[560px] mx-auto">
    <div class="bg-white/80 backdrop-blur-sm rounded-xl shadow-lg border border-white/50 overflow-hidden">
      <!-- Header -->
      <div class="px-4 py-2 border-b border-gray-100 flex items-center justify-between">
        <span class="text-xs font-medium text-gray-500 uppercase tracking-wide">Recent</span>
        <span class="text-xs text-gray-400">{{ items.length }}</span>
      </div>

      <!-- Items -->
      <div class="divide-y divide-gray-50 max-h-[180px] overflow-y-auto">
        <div
          v-for="item in items.slice(0, 3)"
          :key="item.id"
          @click="emit('select', item)"
          class="px-4 py-3 hover:bg-gray-50 cursor-pointer transition-colors group"
        >
          <div class="flex items-start gap-3">
            <!-- Status indicator -->
            <div class="shrink-0 mt-1">
              <Icon
                v-if="item.status === 'pending'"
                icon="mdi:loading"
                class="w-4 h-4 text-blue-500 animate-spin"
              />
              <Icon
                v-else-if="item.status === 'complete'"
                icon="mdi:check-circle"
                class="w-4 h-4 text-green-500"
              />
              <Icon
                v-else
                icon="mdi:alert-circle"
                class="w-4 h-4 text-red-500"
              />
            </div>

            <!-- Content -->
            <div class="flex-1 min-w-0">
              <div class="text-sm font-medium text-gray-800 truncate group-hover:text-blue-600 transition-colors">
                {{ truncate(item.query, 60) }}
              </div>
              <div v-if="item.preview" class="text-xs text-gray-500 mt-0.5 truncate">
                {{ truncate(item.preview, 80) }}
              </div>
            </div>

            <!-- Time -->
            <div class="shrink-0 text-xs text-gray-400">
              {{ formatTime(item.timestamp) }}
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
