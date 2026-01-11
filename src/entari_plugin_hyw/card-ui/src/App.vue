<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { Icon } from '@iconify/vue'

import type { RenderData } from './types'
import StageCard from './components/StageCard.vue'
import MarkdownContent from './components/MarkdownContent.vue'

// Get icon for card type
const getCardIcon = (contentType?: string): string => {
  switch (contentType) {
    case 'summary': return 'mdi:file-document-outline'
    case 'code': return 'mdi:code-braces'
    case 'table': return 'mdi:table'
    default: return 'mdi:card-outline'
  }
}

// Get display label for card
const getCardLabel = (contentType?: string, language?: string): string => {
  switch (contentType) {
    case 'summary': return 'Summary'
    case 'code': return language ? language.charAt(0).toUpperCase() + language.slice(1) : 'Code'
    case 'table': return 'Table'
    default: return ''
  }
}

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

// Calculate the reference offset for each stage (for unified badge numbering)
const getRefOffset = (stageIndex: number): number => {
  if (!data.value?.stages) return 0
  let offset = 0
  for (let i = 0; i < stageIndex; i++) {
    const stage = data.value.stages[i]
    if (stage) {
      offset += (stage.references?.length || 0) + (stage.crawled_pages?.length || 0)
    }
  }
  return offset
}

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

// Process title to support <u> underline tags
const processedTitle = computed(() => {
  return mainTitle.value.replace(/<u>([^<]*)<\/u>/g, (_, content) => {
    return `<span class="underline decoration-[5px] underline-offset-8" style="text-decoration-color: var(--theme-color)">${content}</span>`
  })
})




const dedent = (text: string) => {
  const lines = text.split('\n')
  // Find minimum indentation of non-empty lines
  let minIndent = Infinity
  for (const line of lines) {
    if (line.trim().length === 0) continue
    const leadingSpace = line.match(/^\s*/)?.[0].length || 0
    if (leadingSpace < minIndent) minIndent = leadingSpace
  }
  
  if (minIndent === Infinity || minIndent === 0) return text
  
  return lines.map(line => {
    if (line.trim().length === 0) return ''
    return line.substring(minIndent)
  }).join('\n')
}


const themeColor = computed(() => data.value?.theme_color || '#ef4444')

// Calculate relative luminance to determine if color is light or dark
const getLuminance = (hex: string): number => {
  const match = hex.replace('#', '').match(/.{2}/g)
  if (!match) return 0
  const [r, g, b] = match.map(x => {
    const c = parseInt(x, 16) / 255
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  })
  return 0.2126 * (r ?? 0) + 0.7152 * (g ?? 0) + 0.0722 * (b ?? 0)
}

// Auto text color: dark text on light bg, white text on dark bg
const headerTextColor = computed(() => {
  const luminance = getLuminance(themeColor.value)
  return luminance > 0.4 ? '#1f2937' : '#ffffff'  // gray-800 or white
})

const themeStyle = computed(() => ({ 
  '--theme-color': themeColor.value,
  '--header-text-color': headerTextColor.value,
  '--text-primary': '#2c2c2e',       // Warm dark gray for headings (Apple HIG inspired)
  '--text-body': '#3a3a3c',          // Softer reading color for body text
  '--text-muted': '#636366',         // Muted secondary text
  '--border-color': '#e5e7eb',       // gray-200, for borders
  '--bg-subtle': '#f9fafb'           // gray-50, for subtle backgrounds
}))


const parsedSections = computed(() => {
  const rawMd = data.value?.markdown || ''
  if (!rawMd) return []
  
  // Robustness: Strip any content (AI thoughts, system role prefixes) before the first H1 heading
  // User request: "Match the first big header, ignore what comes before it" ("匹配第一个大标题 无视前面的")
  const md = stripPrefixBeforeH1(rawMd)
  
  let content = md.replace(/^#\s+.+$/m, '')
  content = content.replace(/(?:^|\n)\s*(?:#{1,3}|\*\*)\s*(?:References|Citations|Sources)[\s\S]*$/i, '')
  content = content.trim()
  
  const sections: Array<{ type: 'markdown' | 'card', content: string, title?: string, contentType?: 'table' | 'code' | 'summary', language?: string }> = []
  
  // Combine regex involves complexity, so we'll use a tokenizer approach
  // split tokens by Code Block or Table
  // split tokens by Code Block or Table or Summary
  const combinedRegex = /(```[\s\S]*?```|((?:^|\n)\|[^\n]*\|(?:\n\|[^\n]*\|)*)|<summary>[\s\S]*?<\/summary>)/
  
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
    const isSummary = matchedStr.startsWith('<summary>')
    // Tables might match with a leading newline, trim it for checking but render carefully
    const isTable = !isCode && !isSummary && matchedStr.trim().startsWith('|')
    
    if (isCode || isTable || isSummary) {
        let language = ''
        let content = matchedStr.trim()
        
        if (isCode) {
            const match = matchedStr.match(/^```(\w+)/)
            if (match && match[1]) language = match[1]
        } else if (isSummary) {
            // Strip tags
            content = content.replace(/^<summary>/, '').replace(/<\/summary>$/, '')
            content = dedent(content)
        }

        sections.push({
            type: 'card',
            title: isCode ? 'Code' : (isSummary ? 'Summary' : 'Table'),
            content: content,
            contentType: isCode ? 'code' : (isSummary ? 'summary' : 'table'),
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
    // Demo data for development preview
    data.value = {
      markdown: `# 终极硬核整合包格雷科技新视野

<summary>
《格雷科技：新视野》（GregTech: New Horizons，简称 GTNH）是一款基于 Minecraft 1.7.10 版本的深度硬核科技向整合包。它以 GregTech 5 Unofficial 为核心，通过超过 8 年的持续开发，将 300 多个模组深度集成，构建了极其严苛且逻辑严密的科技树，是公认的生存挑战巅峰之作。
</summary>

## 核心机制与游戏体验
GTNH 的核心在于"格雷化"改造，几乎所有模组的合成表都经过重新设计，以匹配其严苛的阶级制度 [4][8]。玩家需要从原始的石器时代开始，历经蒸汽时代、电力时代，最终向星际航行迈进。其游戏过程极其漫长，旨在让玩家在每一毫秒的进度中感受工业发展的成就感 [3][7]。

![GTNH 游戏场景](https://i.ytimg.com/vi/5T-oSWAgaMM/maxresdefault.jpg)

## 科技阶层与任务系统
整合包拥有 15 个清晰的科技等级（Tiers），最终目标是建造"星门"（Stargate）[2]。为了引导玩家不迷失在复杂的工业流程中，GTNH 内置了超过 3900 条任务的巨型任务书，涵盖了从基础生存到高阶多方块结构的详细指导 [4][7]。

- 15 个科技等级
    - 任务数量：3900+
    - 最终目标：建造"星门"

> 机动战士高达系列是日本动画史上最具影响力的动画作品之一，深受全球观众的喜爱。

| 特性 | 详细描述 |
| :--- | :--- |
| **基础版本** | Minecraft 1.7.10 (高度优化) |
| **任务数量** | 3900+ 任务引导 [7] |
| **科技阶层** | 15 个技术等级 [2] |
| **核心模组** | GregTech 5 Unofficial, Thaumcraft 等 [8] |

## 安装与运行建议
由于其高度集成的特性，官方强烈建议使用 **Prism Launcher** 进行安装和管理 [5]。在运行环境方面，虽然基于旧版 MC，但通过社区努力，目前推荐使用 **Java 17-25** 版本以获得最佳的内存管理和性能优化，确保大型自动化工厂运行流畅 [5]。

\`\`\`bash
curl -s https://raw.githubusercontent.com/GTNewHorizons/GT-New-Horizons-Modpack/master/README.md
java -version
java -Xmx1024M -Xms1024M -jar prism-launcher.jar
\`\`\``,
      total_time: 8.5,
      stages: [
        {
          name: 'instruct',
          model: 'qwen/qwen3-235b-a22b-2507',
          provider: 'Qwen',
          time: 1.83,
          cost: 0.0002,
        },
        {
          name: 'search',
          model: '',
          provider: '',
          time: 0.5,
          cost: 0.0,
          references: [
            { title: 'GTNH 2025 Server Information', url: 'https://stonelegion.com/mc-gtnh-2026/' },
            { title: 'GT New Horizons Wiki', url: 'https://gtnh.miraheze.org/wiki/Main_Page' },
            { title: 'GT New Horizons - GitHub', url: 'https://github.com/GTNewHorizons/GT-New-Horizons-Modpack' },
            { title: 'GT New Horizons - CurseForge', url: 'https://www.curseforge.com/minecraft/modpacks/gt-new-horizons' },
            { title: 'Installing and Migrating - GTNH', url: 'https://gtnh.miraheze.org/wiki/Installing_and_Migrating' },
            { title: 'Modlist - GT New Horizons', url: 'https://wiki.gtnewhorizons.com/wiki/Modlist' },
            { title: 'GregTech: New Horizons - Home', url: 'https://www.gtnewhorizons.com/' },
            { title: 'GT New Horizons - FTB Wiki', url: 'https://ftb.fandom.com/wiki/GT_New_Horizons' }
          ],
          image_references: [
            { title: 'GTNH Live Lets Play', url: 'https://i.ytimg.com/vi/5T-oSWAgaMM/maxresdefault.jpg', thumbnail: 'https://tse4.mm.bing.net/th/id/OIP.b_56VnY4nyrzeqp1JetmFQHaEK?pid=Api' },
            { title: 'GTNH Modpack Cover', url: 'https://i.mcmod.cn/modpack/cover/20240113/1705139595_29797_dSkE.jpg', thumbnail: 'https://tse1.mm.bing.net/th/id/OIP.KNKaZX1d_4Ueq6vpl1qJNAHaEo?pid=Api' },
            { title: 'GTNH Steam Age', url: 'https://i.ytimg.com/vi/8IPwXxqB71w/maxresdefault.jpg', thumbnail: 'https://tse4.mm.bing.net/th/id/OIP.P-KrnI4GBH21yPgwpNPSzAHaEK?pid=Api' },
            { title: 'GTNH MCMod Cover', url: 'https://i.mcmod.cn/post/cover/20230201/1675241030_2_VqDc.jpg', thumbnail: 'https://tse2.mm.bing.net/th/id/OIP.GvYz7YWrg-fnpAHjOiW3OAHaEo?pid=Api' },
            { title: 'GTNH Tectech Tutorial', url: 'http://i0.hdslb.com/bfs/archive/1ed1e53341fd44018138f2823b2fe6c499fb9c9c.jpg', thumbnail: 'https://tse4.mm.bing.net/th/id/OIP.0Wg7xFHTjhxIV9hKuUo4xwHaEo?pid=Api' }
          ]
        },
        {
          name: 'crawler',
          model: '',
          provider: '',
          time: 2.5,
          cost: 0.0,
          crawled_pages: [
            { title: 'GregTech: New Horizons Official Wiki', url: 'https://gtnh.miraheze.org/wiki/Main_Page' },
            { title: 'GT New Horizons Modpack Download', url: 'https://www.curseforge.com/minecraft/modpacks/gt-new-horizons' },
            { title: 'Installing and Migrating Guide', url: 'https://gtnh.miraheze.org/wiki/Installing_and_Migrating' }
          ]
        },
        {
          name: 'agent',
          model: 'google/gemini-3-flash-preview',
          provider: 'Google',
          time: 13.0,
          cost: 0.0018,
        }
      ],
      references: [
        { title: 'GTNH 2025 Server Information', url: 'https://stonelegion.com/mc-gtnh-2026/' },
        { title: 'GT New Horizons Wiki', url: 'https://gtnh.miraheze.org/wiki/Main_Page' },
        { title: 'GT New Horizons - GitHub', url: 'https://github.com/GTNewHorizons/GT-New-Horizons-Modpack' },
        { title: 'GT New Horizons - CurseForge', url: 'https://www.curseforge.com/minecraft/modpacks/gt-new-horizons' },
        { title: 'Installing and Migrating - GTNH', url: 'https://gtnh.miraheze.org/wiki/Installing_and_Migrating' },
        { title: 'Modlist - GT New Horizons', url: 'https://wiki.gtnewhorizons.com/wiki/Modlist' },
        { title: 'GregTech: New Horizons - Home', url: 'https://www.gtnewhorizons.com/' },
        { title: 'GT New Horizons - FTB Wiki', url: 'https://ftb.fandom.com/wiki/GT_New_Horizons' }
      ],
      page_references: [
        { title: 'GregTech: New Horizons Official Wiki', url: 'https://gtnh.miraheze.org/wiki/Main_Page' },
        { title: 'GT New Horizons Modpack Download', url: 'https://www.curseforge.com/minecraft/modpacks/gt-new-horizons' }
      ],
      image_references: [
        { title: 'GTNH Live Lets Play', url: 'https://i.ytimg.com/vi/5T-oSWAgaMM/maxresdefault.jpg', thumbnail: 'https://tse4.mm.bing.net/th/id/OIP.b_56VnY4nyrzeqp1JetmFQHaEK?pid=Api' },
        { title: 'GTNH Modpack Cover', url: 'https://i.mcmod.cn/modpack/cover/20240113/1705139595_29797_dSkE.jpg', thumbnail: 'https://tse1.mm.bing.net/th/id/OIP.KNKaZX1d_4Ueq6vpl1qJNAHaEo?pid=Api' },
        { title: 'GTNH Steam Age', url: 'https://i.ytimg.com/vi/8IPwXxqB71w/maxresdefault.jpg', thumbnail: 'https://tse4.mm.bing.net/th/id/OIP.P-KrnI4GBH21yPgwpNPSzAHaEK?pid=Api' }
      ],
      stats: { total_time: 8.5 },
      theme_color: '#ef4444'
    }
  }
})
</script>

<template>
  <div class="bg-[#f2f2f2] flex justify-center" :style="themeStyle">
    <!-- Main container with explicit background for screenshot capture -->
    <div id="main-container" class="w-[540px] pt-16 pb-12 space-y-8 !bg-[#f2f2f2]" data-theme="light">
      
      <!-- Title -->
      <header v-if="mainTitle" class="px-6 mb-8">
        <!-- Removed Time/Icon Badge as requested -->
        <h1 class="text-4xl font-black leading-tight tracking-tighter uppercase tabular-nums" style="color: var(--text-primary)" v-html="processedTitle"></h1>
      </header>

      <!-- Content Sections -->
      <template v-for="(section, idx) in parsedSections" :key="idx">
        
        <!-- Standard Markdown -->
        <div v-if="section.type === 'markdown'" class="px-7">
          <MarkdownContent 
            :markdown="section.content" 
            :num-search-refs="numSearchRefs"
            :num-page-refs="numPageRefs"
            class="prose-h2:text-[26px] prose-h2:font-black prose-h2:uppercase prose-h2:tracking-tight prose-h2:mb-4 prose-h2:text-gray-800"
          />
        </div>

        <!-- Special Card (Table/Code/Summary) -->
        <div v-else-if="section.type === 'card'" class="mx-6 relative">
          <!-- Corner Rectangle Badge with Icon and Label -->
          <div 
            class="absolute -top-2 -left-2 h-7 px-2.5 z-10 flex items-center gap-1.5"
            :style="{ backgroundColor: themeColor, color: headerTextColor, boxShadow: '0 2px 4px 0 rgba(0,0,0,0.15)' }"
          >
            <Icon :icon="getCardIcon(section.contentType)" class="text-base" />
            <span class="text-xs font-bold uppercase tracking-wide">{{ getCardLabel(section.contentType, section.language) }}</span>
          </div>
          <div 
            class="shadow-sm shadow-black/10 bg-white" 
            :class="[
              section.contentType === 'summary' ? 'pt-8 px-5 pb-3 text-base leading-relaxed' : '',
              section.contentType === 'code' ? 'pt-7 pb-2' : '',
              section.contentType === 'table' ? 'pt-5' : ''
            ]"
          >
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
      <div v-if="data?.stages?.length" class="mx-6 relative">
        <!-- Corner Rectangle Badge with Icon and Label -->
        <div 
          class="absolute -top-2 -left-2 h-7 px-2.5 z-10 flex items-center gap-1.5"
          :style="{ backgroundColor: themeColor, color: headerTextColor, boxShadow: '0 2px 4px 0 rgba(0,0,0,0.15)' }"
        >
          <Icon icon="mdi:link-variant" class="text-base" />
          <span class="text-xs font-bold uppercase tracking-wide">Flow</span>
        </div>
        <div class="p-2 pt-5 bg-white shadow-sm shadow-black/10">
          <StageCard 
            v-for="(stage, index) in data.stages" 
            :key="index"
            :stage="stage"
            :is-first="index === 0"
            :is-last="index === data.stages.length - 1"
            :prev-stage-name="index > 0 ? data.stages[index - 1]?.name : undefined"
            :ref-offset="getRefOffset(index)"
          />
        </div>
      </div>

    </div>
  </div>
</template>


