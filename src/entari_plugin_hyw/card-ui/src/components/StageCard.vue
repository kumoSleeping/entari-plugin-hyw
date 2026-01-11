<script setup lang="ts">
import { ref, computed } from 'vue'
import { Icon } from '@iconify/vue'
import type { Stage } from '../types'

const props = defineProps<{
  stage: Stage
  isFirst?: boolean
  isLast?: boolean
  prevStageName?: string
  refOffset?: number
}>()

const failedImages = ref<Record<string, boolean>>({})
const imageHeights = ref<Record<string, number>>({})

function handleImageError(url: string) {
  failedImages.value[url] = true
}

function handleImageLoad(url: string, event: Event) {
  const img = event.target as HTMLImageElement
  if (img.naturalWidth && img.naturalHeight) {
    // Store aspect ratio as height per unit width
    imageHeights.value[url] = img.naturalHeight / img.naturalWidth
  }
}

// Compute two columns for masonry layout
const imageColumns = computed(() => {
  const images = props.stage.image_references || []
  const leftColumn: typeof images = []
  const rightColumn: typeof images = []
  let leftHeight = 0
  let rightHeight = 0

  for (const img of images) {
    if (failedImages.value[img.url]) continue
    
    // Get aspect ratio (default to 1 if not loaded yet)
    const aspectRatio = imageHeights.value[img.url] || 1
    
    // Add to shorter column
    if (leftHeight <= rightHeight) {
      leftColumn.push(img)
      leftHeight += aspectRatio
    } else {
      rightColumn.push(img)
      rightHeight += aspectRatio
    }
  }

  return { leftColumn, rightColumn }
})





function getDomain(url: string): string {
  try {
    const urlObj = new URL(url)
    const hostname = urlObj.hostname.replace('www.', '')
    const pathname = urlObj.pathname === '/' ? '' : urlObj.pathname
    return hostname + pathname
  } catch {
    return url
  }
}

function getFavicon(url: string): string {
  const domain = getDomain(url)
  return `https://www.google.com/s2/favicons?domain=${domain}&sz=32`
}

function formatTime(seconds: number): string {
  return `${seconds.toFixed(2)}s`
}

function formatCost(dollars: number): string {
  return dollars > 0 ? `$${dollars.toFixed(6)}` : '$0'
}

function getModelShort(model: string): string {
  const short = model.includes('/') ? model.split('/').pop() || model : model
  return short.length > 25 ? short.slice(0, 23) + '…' : short
}

function getStageTheme(name?: string) {
  if (!name) return themes['default']
  const key = name.toLowerCase()
  
  if (key.includes('search')) return themes['search']
  if (key.includes('crawl') || key.includes('page')) return themes['crawler']
  if (key.includes('agent')) return themes['agent']
  if (key.includes('instruct')) return themes['instruct']
  if (key.includes('vision')) return themes['vision']
  
  return themes['default']
}

const themes: Record<string, any> = {
  'search': { color: 'text-blue-600', bg: 'bg-blue-50', iconBg: 'bg-blue-100/50', icon: 'mdi:magnify' },
  'crawler': { color: 'text-orange-600', bg: 'bg-orange-50', iconBg: 'bg-orange-100/50', icon: 'mdi:web' },
  'agent': { color: 'text-purple-600', bg: 'bg-purple-50', iconBg: 'bg-white/80', icon: 'mdi:robot' },
  'instruct': { color: 'text-red-600', bg: 'bg-red-50', iconBg: 'bg-white/80', icon: 'mdi:lightning-bolt' },
  'vision': { color: 'text-purple-600', bg: 'bg-purple-50', iconBg: 'bg-white/80', icon: 'mdi:eye' },
  'default': { color: 'text-gray-600', bg: 'bg-gray-50', iconBg: 'bg-gray-100/50', icon: 'mdi:circle' }
}

function getIcon(name: string): string {
  const key = name.toLowerCase()
  if (key.includes('search')) return 'mdi:magnify'
  if (key.includes('crawl') || key.includes('page')) return 'mdi:web'
  if (key.includes('agent')) return 'mdi:robot'
  if (key.includes('instruct')) return 'mdi:lightning-bolt'
  if (key.includes('vision')) return 'mdi:eye'
  return 'mdi:circle'
}

function getModelLogo(model: string): string | undefined {
  if (!model) return undefined
  const m = model.toLowerCase()
  if (m.includes('openai') || m.includes('gpt')) return 'logos/openai.svg'
  if (m.includes('claude') || m.includes('anthropic')) return 'logos/anthropic.svg'
  if (m.includes('gemini') || m.includes('google')) return 'logos/google.svg'
  if (m.includes('deepseek')) return 'logos/deepseek.png'
  if (m.includes('huggingface')) return 'logos/huggingface.png'
  if (m.includes('mistral')) return 'logos/mistral.png'
  if (m.includes('perplexity')) return 'logos/perplexity.svg'
  if (m.includes('cerebras')) return 'logos/cerebras.svg'
  if (m.includes('grok')) return 'logos/grok.png'
  if (m.includes('qwen')) return 'logos/qwen.png'
  if (m.includes('minimax')) return 'logos/minimax.png'
  if (m.includes('nvidia') || m.includes('nvida')) return 'logos/nvida.png'
  if (m.includes('azure') || m.includes('microsoft')) return 'logos/microsoft.svg'
  if (m.includes('xai')) return 'logos/xai.png'
  if (m.includes('xiaomi')) return 'logos/xiaomi.png'
  if (m.includes('zai')) return 'logos/zai.png'
  return undefined
}
</script>

<template>
  <div class="relative">
    <!-- Content -->
    <div class="flex-1 min-w-0 pl-2">
        <div class="rounded-none overflow-hidden bg-white">
        
          <!-- Header -->
          <div :class="['bg-white px-4 py-2.5 flex items-center justify-between gap-3']">
            <div class="flex items-center gap-3">
              <div :class="['w-8 h-8 flex items-center justify-center shrink-0 overflow-hidden border border-gray-100', getStageTheme(stage.name).iconBg, getStageTheme(stage.name).color]">
                <img v-if="getModelLogo(stage.model)" :src="getModelLogo(stage.model)" class="w-5 h-5 object-contain" />
                <Icon v-else :icon="getIcon(stage.name)" class="text-lg" />
              </div>
              <div class="flex flex-col">
                <span class="font-black text-[18px] text-gray-800 uppercase tracking-tight">{{ stage.name }}</span>
                <span class="text-[15.5px] font-mono tabular-nums tracking-tighter" style="color: var(--text-muted)">{{ getModelShort(stage.model) }}</span>
              </div>
            </div>
            <div v-if="stage.time > 0 || stage.cost > 0" class="text-[15.5px] font-mono flex items-center justify-end gap-2 leading-tight min-w-[120px]" style="color: var(--text-muted)">
              <span v-if="stage.cost > 0">{{ formatCost(stage.cost) }}</span>
              <span v-if="stage.time > 0 && stage.cost > 0" class="text-gray-300">·</span>
              <span v-if="stage.time > 0">{{ formatTime(stage.time) }}</span>
            </div>
          </div>


          <div v-if="stage.references?.length || stage.image_references?.length || stage.crawled_pages?.length" class="bg-white pl-11 relative">
            <div v-if="stage.references?.length" class="divide-y divide-gray-50 relative z-10">
                <a v-for="(ref, idx) in stage.references" :key="idx" 
                   :href="ref.url" target="_blank" 
                   class="flex items-start gap-3 pr-3 py-3 hover:bg-gray-50 transition-colors group">
                  <!-- Favicon - Aligned with Title -->
                  <img :src="getFavicon(ref.url)" class="w-4 h-4 rounded-none shrink-0 object-contain mt-[4px]">
                  
                  <!-- Content: Title and Domain -->
                  <div class="flex-1 min-w-0 flex flex-col">
                    <div class="flex items-center gap-2">
                      <span class="flex-1 text-[18px] font-bold text-gray-700 truncate leading-tight tracking-tight">{{ ref.title }}</span>
                      <!-- Square Badge with Shadow -->
                      <span class="shrink-0 w-[18px] h-[18px] text-[11px] font-bold flex items-center justify-center" style="background-color: var(--theme-color); color: var(--header-text-color); box-shadow: 0 1px 3px 0 rgba(0,0,0,0.15)">{{ (refOffset || 0) + idx + 1 }}</span>
                    </div>
                    <div class="text-[15.5px] font-mono truncate mt-0.5 tracking-tighter" style="color: var(--text-muted)">{{ getDomain(ref.url) }}</div>
                  </div>
                </a>
            </div>

            <!-- Image Search Results - True Masonry Layout -->
            <div v-if="stage.image_references?.length" class="pr-3 py-3 relative z-10">
              <div class="flex gap-2">
                <!-- Left Column -->
                <div class="flex-1 flex flex-col gap-2">
                  <a v-for="(img, idx) in imageColumns.leftColumn" :key="`left-${img.url}-${idx}`" 
                     :href="img.url" target="_blank" 
                     class="relative overflow-hidden transition-all hover:opacity-90 group block">
                    <img :src="img.thumbnail || img.url" 
                         @load="handleImageLoad(img.url, $event)"
                         @error="handleImageError(img.url)"
                         class="w-full h-auto block group-hover:scale-[1.02] transition-transform">
                  </a>
                </div>
                <!-- Right Column -->
                <div class="flex-1 flex flex-col gap-2">
                  <a v-for="(img, idx) in imageColumns.rightColumn" :key="`right-${img.url}-${idx}`" 
                     :href="img.url" target="_blank" 
                     class="relative overflow-hidden transition-all hover:opacity-90 group block">
                    <img :src="img.thumbnail || img.url" 
                         @load="handleImageLoad(img.url, $event)"
                         @error="handleImageError(img.url)"
                         class="w-full h-auto block group-hover:scale-[1.02] transition-transform">
                  </a>
                </div>
              </div>
            </div>

            <div v-if="stage.crawled_pages?.length" class="divide-y divide-gray-50 relative z-10">
                <a v-for="(page, idx) in stage.crawled_pages" :key="idx" 
                   :href="page.url" target="_blank" 
                   class="flex items-start gap-3 pr-3 py-3 hover:bg-gray-50 transition-colors group">
                  <img :src="getFavicon(page.url)" class="w-4 h-4 rounded-none shrink-0 object-contain mt-[4px]">
                  <div class="flex-1 min-w-0 flex flex-col">
                    <div class="flex items-center gap-2">
                      <span class="flex-1 text-[18px] font-bold text-gray-700 truncate leading-tight tracking-tight">{{ page.title }}</span>
                      <!-- Square Badge with Shadow -->
                      <span class="shrink-0 w-[18px] h-[18px] text-[11px] font-bold flex items-center justify-center" style="background-color: var(--theme-color); color: var(--header-text-color); box-shadow: 0 1px 3px 0 rgba(0,0,0,0.15)">{{ (refOffset || 0) + (stage.references?.length || 0) + idx + 1 }}</span>
                    </div>
                    <div class="text-[15.5px] font-mono truncate mt-0.5 tracking-tighter" style="color: var(--text-muted)">{{ getDomain(page.url) }}</div>
                  </div>
                </a>
            </div>
          </div>
        </div>
    </div>
  </div>
</template>
