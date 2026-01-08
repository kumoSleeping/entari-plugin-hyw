<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { Icon } from '@iconify/vue'
import type { RenderData } from './types'
import StageCard from './components/StageCard.vue'
import MarkdownContent from './components/MarkdownContent.vue'

declare global {
  interface Window {
    RENDER_DATA: RenderData
    updateRenderData: (data: RenderData) => void
  }
}

const data = ref<RenderData | null>(null)

// Expose update method for Python to call
window.updateRenderData = (newData: RenderData) => {
  data.value = newData
}

const numSearchRefs = computed(() => data.value?.references?.length || 0)
const numPageRefs = computed(() => data.value?.page_references?.length || 0)

// Helper: Strips content before the first H1 heading (e.g., AI "thought" prefixes)
const stripPrefixBeforeH1 = (text: string): string => {
  // Find the first line starting with "# " (H1)
  const h1Match = text.match(/^#\s+/m)
  if (h1Match && h1Match.index !== undefined) {
    // If found, return everything starting from that H1
    // This effectively discards any "thought" blocks or "### ASSISTANT" prefixes appearing before it.
    return text.substring(h1Match.index)
  }
  // If no H1 found, return text as-is (fallback)
  return text
}

const mainTitle = computed(() => {
  const md = stripPrefixBeforeH1(data.value?.markdown || '')
  const match = md.match(/^#\s+(.+)$/m)
  return match && match[1] ? match[1].trim() : ''
})
const agentModel = computed(() => {
  const agentStage = data.value?.stages?.find(s => s.name.toLowerCase().includes('agent'))
  return agentStage?.model || 'AI AGENT'
})


const parsedSections = computed(() => {
  const rawMd = data.value?.markdown || ''
  if (!rawMd) return []
  
  // Robustness: Strip any content (AI thoughts, system role prefixes) before the first H1 heading
  // User request: "Match the first big header, ignore what comes before it" ("匹配第一个大标题 无视前面的")
  const md = stripPrefixBeforeH1(rawMd)
  
  let content = md.replace(/^#\s+.+$/m, '')
  content = content.replace(/(?:^|\n)\s*(?:#{1,3}|\*\*)\s*(?:References|Citations|Sources)[\s\S]*$/i, '')
  content = content.trim()
  
  const sections: Array<{ type: 'markdown' | 'card', content: string, title?: string, contentType?: 'table' | 'code', language?: string }> = []
  
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
        let language = ''
        if (isCode) {
            const match = matchedStr.match(/^```(\w+)/)
            if (match && match[1]) language = match[1]
        }
        sections.push({
            type: 'card',
            title: isCode ? 'Code' : 'Table',
            content: matchedStr.trim(),
            contentType: isCode ? 'code' : 'table',
            language: language
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
  <div class="min-h-screen bg-[#f2f2f2] flex justify-center selection:bg-red-100 selection:text-red-900">
    <!-- Main container with explicit background for screenshot capture -->
    <div id="main-container" class="w-full max-w-[450px] py-10 space-y-6 !bg-[#f2f2f2]" data-theme="light">
      
      <!-- Title -->
      <header v-if="mainTitle" class="px-7 mb-8">
        <div class="h-1 w-12 bg-red-500 mb-4"></div>
        <h1 class="text-3xl font-black text-gray-900 leading-tight tracking-tighter uppercase tabular-nums">
          {{ mainTitle }}
        </h1>
        <div class="mt-2 text-[10px] font-mono text-gray-900 tracking-widest flex items-center justify-end gap-2">
          Report by {{ agentModel }}
          <span class="w-2 h-2 bg-red-500"></span>
        </div>
      </header>

      <!-- Content Sections -->
      <template v-for="(section, idx) in parsedSections" :key="idx">
        
        <!-- Standard Markdown -->
        <div v-if="section.type === 'markdown'" class="px-7">
          <MarkdownContent 
            :markdown="section.content" 
            :num-search-refs="numSearchRefs"
            :num-page-refs="numPageRefs"
            class="prose-h2:text-xl prose-h2:font-black prose-h2:uppercase prose-h2:tracking-tight prose-h2:mb-4"
          />
        </div>

        <!-- Special Card (Table/Code) -->
        <div v-else-if="section.type === 'card'" class="mx-6">
          <!-- Header for both Table and Code -->
          <!-- Header for Code only -->
          <div v-if="section.contentType !== 'table'" class="px-4 py-1.5 bg-gray-50 flex items-center justify-between ">
             <div class="flex items-center gap-2">
                <Icon icon="mdi:code-braces" class="text-red-500 text-sm" />
                <span class="font-black text-[10px] text-gray-700 uppercase tracking-widest">{{ section.title }}</span>
             </div>
             <div v-if="section.language" class="text-gray-600 text-[9px] font-mono tabular-nums tracking-tighter">
                {{ section.language }}
             </div>
          </div>
          <div class="bg-white ">
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
      <div v-if="data?.stages?.length" class="mx-6">
        <div class="px-4 py-2 bg-gray-50 flex items-center justify-between ">
          <div class="flex items-center gap-2">
            <Icon icon="mdi:chart-timeline-variant" class="text-red-500 text-sm" />
            <span class="font-black text-[10px] text-gray-700 uppercase tracking-widest">FLOW</span>
          </div>
          <div class="text-[9px] font-mono text-gray-600 tracking-tighter tabular-nums">
            {{ data.stages.length }} Stages
          </div>
        </div>
        <div class="p-4 bg-white ">
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


