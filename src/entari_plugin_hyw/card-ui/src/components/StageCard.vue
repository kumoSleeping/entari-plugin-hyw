<script setup lang="ts">
import { Icon } from '@iconify/vue'
import type { Stage } from '../types'

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
    return ''
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
  'search': { color: 'text-blue-600', bg: 'bg-blue-50', line: 'bg-blue-200', iconBg: 'bg-blue-100/50', dotBg: 'bg-blue-600', icon: 'mdi:magnify' },
  'crawler': { color: 'text-orange-600', bg: 'bg-orange-50', line: 'bg-orange-200', iconBg: 'bg-orange-100/50', dotBg: 'bg-orange-600', icon: 'mdi:web' },
  'agent': { color: 'text-purple-600', bg: 'bg-purple-50', line: 'bg-purple-200', iconBg: 'bg-purple-100/50', dotBg: 'bg-purple-600', icon: 'mdi:robot' },
  'instruct': { color: 'text-yellow-600', bg: 'bg-yellow-50', line: 'bg-yellow-200', iconBg: 'bg-yellow-100/50', dotBg: 'bg-yellow-600', icon: 'mdi:lightning-bolt' },
  'vision': { color: 'text-green-600', bg: 'bg-green-50', line: 'bg-green-200', iconBg: 'bg-green-100/50', dotBg: 'bg-green-600', icon: 'mdi:eye' },
  'default': { color: 'text-gray-600', bg: 'bg-gray-50', line: 'bg-gray-200', iconBg: 'bg-gray-100/50', dotBg: 'bg-gray-600', icon: 'mdi:circle' }
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
</script>

<template>
  <div class="flex pl-1 pb-4 relative">
    <!-- Timeline -->
    <div class="relative w-4 shrink-0 flex flex-col">
      <div v-if="!isFirst" :class="['absolute top-0 w-[2px] left-1/2 -translate-x-1/2', getStageTheme(prevStageName).line]" style="height: 28px;"></div>
      <div :class="['absolute top-[24px] w-2 h-2 rounded-full z-10 left-1/2 -translate-x-1/2 border-2 border-white', getStageTheme(stage.name).dotBg]"></div>
      <div v-if="!isLast" :class="['absolute w-[2px] left-1/2 -translate-x-1/2', getStageTheme(stage.name).line]" style="top: 28px; bottom: -16px;"></div>
    </div>

    <!-- Content -->
    <div class="flex-1 min-w-0 pl-2">
      <div class="bg-white border border-gray-200 rounded-xl p-3">
        
        <!-- Header -->
        <div :class="['flex items-center justify-between gap-2', (stage.references?.length || stage.crawled_pages?.length) ? 'mb-2' : '']">
          <div class="flex items-center gap-2">
            <div :class="['w-7 h-7 rounded-lg flex items-center justify-center shrink-0', getStageTheme(stage.name).iconBg, getStageTheme(stage.name).color]">
              <Icon :icon="getIcon(stage.name)" class="text-base" />
            </div>
            <div class="flex flex-col">
              <span class="font-bold text-sm text-gray-900">{{ stage.name }}</span>
              <span class="text-[10px] text-gray-400 truncate max-w-[140px]">{{ getModelShort(stage.model) }}</span>
            </div>
          </div>
          <div v-if="stage.time > 0 || stage.cost > 0" class="text-[10px] text-gray-400 font-mono">
            <span v-if="stage.time > 0">{{ formatTime(stage.time) }}</span>
            <span v-if="stage.cost > 0" class="ml-2">{{ formatCost(stage.cost) }}</span>
          </div>
        </div>

        <!-- Search Results -->
        <div v-if="stage.references?.length" class="mt-2">
          <div class="text-[10px] font-semibold text-blue-600 uppercase mb-1">Search Results</div>
          <div class="space-y-1">
            <a v-for="(ref, idx) in stage.references" :key="idx" :href="ref.url" target="_blank"
               class="flex items-center gap-2 p-2 rounded-lg border border-gray-100 hover:border-blue-200 hover:bg-blue-50/50 transition-colors">
              <img :src="getFavicon(ref.url)" class="w-4 h-4 rounded">
              <div class="flex-1 min-w-0">
                <div class="text-xs font-medium text-gray-800 truncate">{{ ref.title }}</div>
                <div class="text-[10px] text-gray-400 truncate">{{ getDomain(ref.url) }}</div>
              </div>
              <span class="text-[10px] font-bold text-blue-600 bg-blue-100 px-1.5 py-0.5 rounded">{{ idx + 1 }}</span>
            </a>
          </div>
        </div>

        <!-- Crawled Pages -->
        <div v-if="stage.crawled_pages?.length" class="mt-2">
          <div class="text-[10px] font-semibold text-orange-600 uppercase mb-1">Fetched Pages</div>
          <div class="space-y-1">
            <a v-for="(page, idx) in stage.crawled_pages" :key="idx" :href="page.url" target="_blank"
               class="flex items-center gap-2 p-2 rounded-lg border border-gray-100 hover:border-orange-200 hover:bg-orange-50/50 transition-colors">
              <img :src="getFavicon(page.url)" class="w-4 h-4 rounded">
              <div class="flex-1 min-w-0">
                <div class="text-xs font-medium text-gray-800 truncate">{{ page.title }}</div>
                <div class="text-[10px] text-gray-400 truncate">{{ getDomain(page.url) }}</div>
              </div>
              <span class="text-[10px] font-bold text-orange-600 bg-orange-100 px-1.5 py-0.5 rounded">{{ idx + 1 }}</span>
            </a>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
