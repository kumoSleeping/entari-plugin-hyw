<script setup lang="ts">
import { ref } from 'vue'
import { Icon } from '@iconify/vue'
import type { Stage } from '../types'

const failedImages = ref<Record<string, boolean>>({})

function handleImageError(url: string) {
  failedImages.value[url] = true
}

const props = defineProps<{
  stage: Stage
  isFirst?: boolean
  isLast?: boolean
  prevStageName?: string
}>()

function getDomain(url: string): string {
  try {
    return new URL(url).hostname.replace('www.', '')
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
  'search': { color: 'text-blue-600', bg: 'bg-blue-50', line: 'bg-red-300', iconBg: 'bg-blue-100/50', dotBg: 'bg-red-500', icon: 'mdi:magnify' },
  'crawler': { color: 'text-orange-600', bg: 'bg-orange-50', line: 'bg-red-300', iconBg: 'bg-orange-100/50', dotBg: 'bg-red-500', icon: 'mdi:web' },
  'agent': { color: 'text-purple-600', bg: 'bg-purple-50', line: 'bg-red-300', iconBg: 'bg-white/80', dotBg: 'bg-red-500', icon: 'mdi:robot' },
  'instruct': { color: 'text-red-600', bg: 'bg-red-50', line: 'bg-red-300', iconBg: 'bg-white/80', dotBg: 'bg-red-500', icon: 'mdi:lightning-bolt' },
  'vision': { color: 'text-green-600', bg: 'bg-green-50', line: 'bg-red-300', iconBg: 'bg-green-100/50', dotBg: 'bg-red-500', icon: 'mdi:eye' },
  'default': { color: 'text-gray-600', bg: 'bg-gray-50', line: 'bg-red-300', iconBg: 'bg-gray-100/50', dotBg: 'bg-red-500', icon: 'mdi:circle' }
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
          <div :class="['bg-white px-3 py-1.5 flex items-center justify-between gap-2']">
            <div class="flex items-center gap-2">
              <div :class="['w-6 h-6 flex items-center justify-center shrink-0 overflow-hidden border border-gray-100', getStageTheme(stage.name).iconBg, getStageTheme(stage.name).color]">
                <img v-if="getModelLogo(stage.model)" :src="getModelLogo(stage.model)" class="w-4 h-4 object-contain" />
                <Icon v-else :icon="getIcon(stage.name)" class="text-xs" />
              </div>
              <div class="flex flex-col">
                <span class="font-black text-xs text-gray-900 uppercase tracking-tight">{{ stage.name }}</span>
                <span class="text-[10px] font-mono text-gray-400 tabular-nums tracking-tighter">{{ getModelShort(stage.model) }}</span>
              </div>
            </div>
            <div v-if="stage.time > 0 || stage.cost > 0" class="text-[10px] text-gray-400 font-mono flex items-center justify-end gap-2 leading-tight min-w-[120px]">
              <span v-if="stage.cost > 0">{{ formatCost(stage.cost) }}</span>
              <span v-if="stage.time > 0 && stage.cost > 0" class="text-gray-300">·</span>
              <span v-if="stage.time > 0">{{ formatTime(stage.time) }}</span>
            </div>
          </div>


          <div v-if="stage.references?.length || stage.image_references?.length" class="bg-white pl-8 relative">
            <div v-if="stage.references?.length" class="divide-y divide-gray-50 relative z-10">
                <a v-for="(ref, idx) in stage.references" :key="idx" 
                   :href="ref.url" target="_blank" 
                   class="flex items-start gap-2 pr-3 py-2 hover:bg-gray-50 transition-colors group">
                  <!-- Favicon - Aligned with Title -->
                  <img :src="getFavicon(ref.url)" class="w-3 h-3 rounded-none shrink-0 object-contain mt-[3px]">
                  
                  <!-- Content: Title and Domain -->
                  <div class="flex-1 min-w-0 flex flex-col">
                    <div class="flex items-center gap-2">
                      <span class="flex-1 text-xs font-bold text-gray-800 truncate leading-tight tracking-tight">{{ ref.title }}</span>
                      <!-- Neutralized Citation Design -->
                      <span class="shrink-0 text-[10px] font-bold text-blue-600 flex items-center justify-center">{{ idx + 1 }}</span>
                    </div>
                    <div class="text-[10px] font-mono text-gray-400 truncate mt-0.5 tracking-tighter">{{ getDomain(ref.url) }}</div>
                  </div>
                </a>
            </div>

            <!-- Image Search Results -->
            <div v-if="stage.image_references?.length" class="pr-2 py-2 relative z-10">
              <div class="grid grid-cols-3 gap-1">
                <a v-for="(img, idx) in stage.image_references" :key="idx" 
                   v-show="!failedImages[img.url]"
                   :href="img.url" target="_blank" 
                   class="relative aspect-square overflow-hidden bg-white border border-gray-200 transition-colors">
                  <img :src="img.thumbnail || img.url" 
                       @error="handleImageError(img.url)"
                       class="w-full h-full object-cover">
                </a>
              </div>
            </div>
          </div>
        </div>
    </div>
  </div>
</template>
