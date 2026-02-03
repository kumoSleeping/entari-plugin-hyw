<script setup lang="ts">
import { ref, watch, computed, nextTick, onMounted } from 'vue'
import { Icon } from '@iconify/vue'

const props = defineProps<{
  disabled?: boolean
  loading?: boolean
  currentQuery?: string
}>()

const query = ref('')
const textareaRef = ref<HTMLTextAreaElement | null>(null)
const emit = defineEmits<{
  (e: 'search', query: string): void
  (e: 'stop'): void
  (e: 'resize'): void
}>()

const canSubmit = computed(() => !props.disabled && !props.loading && query.value.trim().length > 0)

// Auto-resize textarea based on content
const LINE_HEIGHT = 24 // px per line
const MAX_LINES = 8
const MIN_HEIGHT = LINE_HEIGHT
const MAX_HEIGHT = LINE_HEIGHT * MAX_LINES

const adjustHeight = async () => {
  await nextTick()
  const textarea = textareaRef.value
  if (!textarea) return

  // Reset height to measure scrollHeight correctly
  textarea.style.height = `${MIN_HEIGHT}px`

  // Calculate new height (capped at max)
  const scrollHeight = textarea.scrollHeight
  const newHeight = Math.min(Math.max(scrollHeight, MIN_HEIGHT), MAX_HEIGHT)

  textarea.style.height = `${newHeight}px`

  // Enable scroll if content exceeds max
  textarea.style.overflowY = scrollHeight > MAX_HEIGHT ? 'auto' : 'hidden'

  // Notify parent to resize window
  emit('resize')
}

const onKeydown = (e: KeyboardEvent) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    if (!canSubmit.value) return
    emit('search', query.value)
  }
  // Shift+Enter: allow newline, will trigger input event
}

const handleSubmit = () => {
  if (!canSubmit.value) return
  emit('search', query.value)
}

const handleStop = () => {
  emit('stop')
}

// Watch for input changes to auto-resize
watch(query, () => {
  adjustHeight()
})

// Clear input when loading starts (query submitted)
watch(() => props.loading, (isLoading) => {
  if (isLoading) {
    query.value = ''
    // Reset textarea height
    if (textareaRef.value) {
      textareaRef.value.style.height = `${MIN_HEIGHT}px`
      textareaRef.value.style.overflowY = 'hidden'
    }
    emit('resize')
  }
})

onMounted(() => {
  adjustHeight()
})
</script>

<template>
  <div class="w-full relative">
    <!-- Input container with marque border when loading -->
    <div
      class="relative rounded-2xl overflow-hidden"
      :class="loading ? 'marque-border' : ''"
    >
      <!-- Background for marque effect -->
      <div v-if="loading" class="absolute inset-0 marque-bg rounded-2xl"></div>

      <!-- Inner container -->
      <div class="relative bg-white/95 backdrop-blur-xl m-[2px] rounded-[14px]">
        <div class="flex items-center gap-2 px-3 py-2">
          <!-- Input -->
          <textarea
            ref="textareaRef"
            v-model="query"
            @keydown="onKeydown"
            :placeholder="loading ? '' : ''"
            :disabled="disabled || loading"
            class="flex-1 text-base bg-transparent outline-none resize-none"
            :class="{
              'text-gray-800 placeholder-gray-400': !disabled && !loading,
              'text-gray-400 placeholder-gray-300 cursor-not-allowed': disabled || loading
            }"
            :style="{ height: `${MIN_HEIGHT}px`, overflowY: 'hidden', lineHeight: '24px' }"
            autofocus
          />

          <!-- Action Button -->
          <button
            v-if="!loading"
            @click="handleSubmit"
            :disabled="!canSubmit"
            class="shrink-0 w-8 h-8 rounded-xl flex items-center justify-center transition-all duration-200 self-end"
            :class="canSubmit
              ? 'bg-gray-900 text-white hover:bg-gray-700 hover:scale-105 active:scale-95'
              : 'bg-gray-100 text-gray-300 cursor-not-allowed'"
          >
            <Icon icon="mdi:arrow-up" class="text-lg" />
          </button>

          <!-- Stop Button when loading -->
          <button
            v-else
            @click="handleStop"
            class="shrink-0 w-8 h-8 rounded-xl bg-red-500 text-white flex items-center justify-center hover:bg-red-600 hover:scale-105 active:scale-95 transition-all duration-200 self-end"
          >
            <Icon icon="mdi:stop" class="text-lg" />
          </button>
        </div>
      </div>
    </div>

    <!-- Loading indicator text -->
    <div
      v-if="loading"
      class="mt-2 flex items-center gap-2 text-sm text-gray-500 animate-pulse"
    >
      <div class="flex gap-1">
        <span class="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style="animation-delay: 0ms"></span>
        <span class="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style="animation-delay: 150ms"></span>
        <span class="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce" style="animation-delay: 300ms"></span>
      </div>
      <span class="font-medium">生成中</span>
    </div>
  </div>
</template>

<style scoped>
/* Marque border animation */
.marque-border {
  position: relative;
}

.marque-bg {
  background: linear-gradient(
    90deg,
    #ef4444,
    #f97316,
    #eab308,
    #22c55e,
    #3b82f6,
    #8b5cf6,
    #ef4444
  );
  background-size: 200% 100%;
  animation: marque 2s linear infinite;
}

@keyframes marque {
  0% {
    background-position: 0% 50%;
  }
  100% {
    background-position: 200% 50%;
  }
}

/* Bounce animation for dots */
@keyframes bounce {
  0%, 100% {
    transform: translateY(0);
  }
  50% {
    transform: translateY(-4px);
  }
}

.animate-bounce {
  animation: bounce 0.6s ease-in-out infinite;
}

/* Custom scrollbar for textarea */
textarea::-webkit-scrollbar {
  width: 4px;
}
textarea::-webkit-scrollbar-track {
  background: transparent;
}
textarea::-webkit-scrollbar-thumb {
  background: #d1d5db;
  border-radius: 2px;
}
</style>
