<script setup lang="ts">
import MarkdownContent from './MarkdownContent.vue'

const props = defineProps<{
  title: string
  content: string
  level: number
  numSearchRefs?: number
  numPageRefs?: number
  index: number
}>()
</script>

<template>
  <div class="mb-5">
    <!-- Normal Text Section: macOS Window Style -->
    <div class="rounded-none overflow-hidden border border-gray-300 bg-white/70 backdrop-blur-xl">
      <!-- Section Window Header with traffic lights + title on right -->
      <div class="flex items-center justify-between px-3 py-2 bg-gray-100/80 backdrop-blur-lg border-b border-gray-200/40">
        <div class="flex items-center gap-1.5">
          <!-- macOS Traffic Lights -->
          <div class="flex gap-1.5 mr-2">
            <div class="w-2.5 h-2.5 rounded-full bg-[#ff5f56] shadow-md"></div>
            <div class="w-2.5 h-2.5 rounded-full bg-[#ffbd2e] shadow-md"></div>
            <div class="w-2.5 h-2.5 rounded-full bg-[#27c93f] shadow-md"></div>
          </div>
        </div>
        <span class="text-[11px] font-mono text-gray-700/70 uppercase font-bold tracking-wider">{{ title }}</span>
      </div>
      
      <!-- Section Content -->
      <div class="p-5 bg-white/40 backdrop-blur-sm">
        <MarkdownContent 
          :markdown="content"
          :num-search-refs="numSearchRefs"
          :num-page-refs="numPageRefs"
        />
      </div>
    </div>
  </div>
</template>
