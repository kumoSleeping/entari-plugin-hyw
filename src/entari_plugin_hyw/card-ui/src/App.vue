<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { Icon } from '@iconify/vue'
import type { RenderData } from './types'
import StageCard from './components/StageCard.vue'
import MarkdownContent from './components/MarkdownContent.vue'

declare global {
  interface Window {
    RENDER_DATA: RenderData
  }
}

const data = ref<RenderData | null>(null)
const numSearchRefs = computed(() => data.value?.references?.length || 0)
const numPageRefs = computed(() => data.value?.page_references?.length || 0)

const mainTitle = computed(() => {
  const md = data.value?.markdown || ''
  const match = md.match(/^#\s+(.+)$/m)
  return match && match[1] ? match[1].trim() : ''
})

const summaryData = computed(() => {
  const md = data.value?.markdown || ''
  const summaryMatch = md.match(/<summary>([\s\S]*?)<\/summary>/i)
  if (!summaryMatch || !summaryMatch[1]) return null
  
  const content = summaryMatch[1].trim()
  // Hardcode title to 'Summary' as we no longer require a preceding H2
  const title = 'Summary'
  
  return { title, content, summaryMatch }
})

const parsedSections = computed(() => {
  const md = data.value?.markdown || ''
  if (!md) return []
  
  let content = md.replace(/^#\s+.+$/m, '')
  content = content.replace(/<summary>[\s\S]*?<\/summary>/i, '')
  content = content.replace(/(?:^|\n)\s*(?:#{1,3}|\*\*)\s*(?:References|Citations|Sources)[\s\S]*$/i, '')
  content = content.trim()
  
  const sections: Array<{ type: 'markdown' | 'card', content: string, title?: string, contentType?: 'table' | 'code' }> = []
  
  // Combine regex involves complexity, so we'll use a tokenizer approach
  // split tokens by Code Block or Table
  const combinedRegex = /(```[\s\S]*?```|((?:^|\n)\|[^\n]*\|(?:\n\|[^\n]*\|)*))/
  
  let remaining = content
  
  while (remaining) {
    const match = remaining.match(combinedRegex)
    if (!match) {
      if (remaining.trim()) {
        sections.push({ type: 'markdown', content: remaining.trim() })
      }
      break
    }
    
    const index = match.index!
    const matchedStr = match[0]
    const preText = remaining.substring(0, index)
    
    if (preText.trim()) {
      sections.push({ type: 'markdown', content: preText.trim() })
    }
    
    // Determine type
    const isCode = matchedStr.startsWith('```')
    // Tables might match with a leading newline, trim it for checking but render carefully
    const isTable = !isCode && matchedStr.trim().startsWith('|')
    
    if (isCode || isTable) {
        sections.push({
            type: 'card',
            title: isCode ? 'Code' : 'Data Grid',
            content: matchedStr.trim(),
            contentType: isCode ? 'code' : 'table'
        })
    } else {
        // Should not happen if regex is correct, but safe fallback
        sections.push({ type: 'markdown', content: matchedStr })
    }
    
    remaining = remaining.substring(index + matchedStr.length)
  }
  
  return sections
})

onMounted(() => {
  if (window.RENDER_DATA && Object.keys(window.RENDER_DATA).length > 0) {
    data.value = window.RENDER_DATA
  } else {
    data.value = {
      markdown: '# 测试标题\n\n(Introduction text that should appear at top)\n\n<summary>Summary content here.</summary>\n\n## Normal Section\nContent mixed with text.\n\n## 播放时间表\n| 季度 | 开始日期 | 结束日期 | 集数 |\n| :--- | :--- | :--- | :--- |\n| S1 | 2007 | 2008 | 25 |\n\n## Another Text Section\nMore text here.\n\n## 代码示例\n```python\nprint("Hello")\n```',
      total_time: 2.5,
      stages: [
        { name: 'Agent', model: 'gpt-4', provider: 'OpenAI', time: 1.23, cost: 0.001 },
      ],
      references: [],
      page_references: [],
      image_references: [],
      stats: { total_time: 2.5 }
    }
  }
})
</script>

<template>
  <div class="min-h-screen bg-base-200">
    <div id="main-container" class="w-full max-w-[450px] mx-auto py-8 space-y-4" data-theme="light">
      
      <!-- Title -->
      <h1 v-if="mainTitle" class="px-6 text-2xl font-black text-gray-900">{{ mainTitle }}</h1>

      <!-- Summary Note -->
      <div v-if="summaryData" class="mx-5 p-5 bg-amber-50 rounded-xl border border-amber-200/60">
        <div class="flex items-center gap-2 mb-2 text-amber-700">
          <Icon icon="mdi:note-text" class="text-lg" />
          <span class="font-bold">{{ summaryData.title || 'Summary' }}</span>
        </div>
        <MarkdownContent 
          :markdown="summaryData.content" 
          :num-search-refs="numSearchRefs"
          :num-page-refs="numPageRefs"
          class="text-gray-800 text-base leading-relaxed"
        />
      </div>

      <!-- Content Sections -->
      <template v-for="(section, idx) in parsedSections" :key="idx">
        
        <!-- Standard Markdown -->
        <div v-if="section.type === 'markdown'" class="px-6">
          <MarkdownContent 
            :markdown="section.content" 
            :num-search-refs="numSearchRefs"
            :num-page-refs="numPageRefs"
            class="prose prose-base max-w-none prose-headings:text-gray-900 prose-h2:text-xl prose-h2:mt-6 prose-h2:mb-3 prose-h2:pb-2 prose-h2:border-b prose-h2:border-gray-200 prose-p:text-gray-700 prose-p:leading-7"
          />
        </div>

        <!-- Special Card (Table/Code) -->
        <div v-else-if="section.type === 'card'" class="mx-5 bg-white rounded-xl border border-gray-200 overflow-hidden">
          <div class="px-4 py-2 bg-gray-50 border-b border-gray-100 flex items-center gap-2 text-gray-600">
             <Icon :icon="section.contentType === 'table' ? 'mdi:table' : 'mdi:code-braces'" class="text-base" />
             <span class="font-semibold text-sm">{{ section.title }}</span>
          </div>
          <div class="">
            <MarkdownContent 
              :markdown="section.content"
              :bare="true"
              :num-search-refs="numSearchRefs"
              :num-page-refs="numPageRefs"
            />
          </div>
        </div>

      </template>

      <!-- Workflow -->
      <div v-if="data?.stages?.length" class="mx-5 bg-white rounded-xl border border-gray-200 overflow-hidden">
        <div class="px-4 py-2 bg-gray-50 border-b border-gray-100 flex items-center gap-2 text-gray-600">
          <Icon icon="mdi:chart-timeline-variant" class="text-base" />
          <span class="font-semibold text-sm">Workflow</span>
        </div>
        <div class="p-4">
          <StageCard 
            v-for="(stage, index) in data.stages" 
            :key="index"
            :stage="stage"
            :is-first="index === 0"
            :is-last="index === data.stages.length - 1"
            :prev-stage-name="index > 0 ? data.stages[index - 1]?.name : undefined"
          />
        </div>
      </div>

    </div>
  </div>
</template>


