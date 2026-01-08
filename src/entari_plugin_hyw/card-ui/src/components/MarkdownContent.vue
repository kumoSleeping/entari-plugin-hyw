<script setup lang="ts">
import { computed } from 'vue'
import { marked, type Tokens } from 'marked'
import hljs from 'highlight.js/lib/core'
// Import only common languages to reduce bundle size
import python from 'highlight.js/lib/languages/python'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import json from 'highlight.js/lib/languages/json'
import bash from 'highlight.js/lib/languages/bash'
import css from 'highlight.js/lib/languages/css'
import xml from 'highlight.js/lib/languages/xml'
import java from 'highlight.js/lib/languages/java'
import cpp from 'highlight.js/lib/languages/cpp'
import go from 'highlight.js/lib/languages/go'
import rust from 'highlight.js/lib/languages/rust'
import sql from 'highlight.js/lib/languages/sql'
import markdown from 'highlight.js/lib/languages/markdown'

hljs.registerLanguage('python', python)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('js', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('ts', typescript)
hljs.registerLanguage('json', json)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('sh', bash)
hljs.registerLanguage('shell', bash)
hljs.registerLanguage('css', css)
hljs.registerLanguage('html', xml)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('java', java)
hljs.registerLanguage('cpp', cpp)
hljs.registerLanguage('c', cpp)
hljs.registerLanguage('go', go)
hljs.registerLanguage('rust', rust)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('markdown', markdown)
hljs.registerLanguage('md', markdown)

import 'highlight.js/styles/github.css'

const props = defineProps<{
  markdown: string
  numSearchRefs?: number
  numPageRefs?: number
  bare?: boolean  // When true, tables and code blocks render without window decoration
}>()

// Configure marked with syntax highlighting
marked.setOptions({
  breaks: true,
  gfm: true,
})

// Custom renderer for code blocks with Mac-style window header
const renderer = new marked.Renderer()
renderer.code = ({ text, lang }: Tokens.Code): string => {
  const language = lang || 'text'
  let highlighted = ''
  if (lang && hljs.getLanguage(lang)) {
    try {
      highlighted = hljs.highlight(text, { language: lang }).value
    } catch {
      highlighted = hljs.highlightAuto(text).value
    }
  } else {
    highlighted = hljs.highlightAuto(text).value
  }

  // Bare mode: just the code, no window decoration
  if (props.bare) {
    return `<pre class="!mt-0 !mb-0 !rounded-xl !bg-gray-50/80 backdrop-blur-md !p-4 overflow-x-auto"><code class="hljs language-${language} text-[13px] leading-relaxed font-mono">${highlighted}</code></pre>`
  }

  return `
    <div class="my-4 rounded-2xl overflow-hidden border border-gray-200/60 shadow-[0_8px_24px_-4px_rgba(0,0,0,0.12)] bg-white/60 backdrop-blur-xl group">
      <div class="flex items-center justify-between px-3 py-2 bg-gray-100/80 backdrop-blur-lg border-b border-gray-200/40">
        <div class="flex items-center gap-1.5">
          <!-- macOS Traffic Lights -->
          <div class="flex gap-1.5 mr-2">
            <div class="w-2.5 h-2.5 rounded-full bg-[#ff5f56] shadow-md"></div>
            <div class="w-2.5 h-2.5 rounded-full bg-[#ffbd2e] shadow-md"></div>
            <div class="w-2.5 h-2.5 rounded-full bg-[#27c93f] shadow-md"></div>
          </div>

        </div>
        <span class="text-[11px] font-mono text-gray-700/70 uppercase font-medium tracking-wider">${language}</span>
      </div>
      <pre class="!mt-0 !mb-0 !rounded-none !bg-white/60 backdrop-blur-md !p-4 overflow-x-auto"><code class="hljs language-${language} text-[13px] leading-relaxed font-mono">${highlighted}</code></pre>
    </div>
  `
}

marked.use({ renderer })

// Process markdown and convert citations to badges
const processedHtml = computed(() => {
  let md = props.markdown || ''
  
  // Remove References section at end
  md = md.replace(/(?:^|\n)\s*(?:#{1,3}|\*\*)\s*(?:References|Citations|Sources)[\s\S]*$/i, '')
  
  // Convert markdown to HTML
  let html = marked.parse(md) as string
  
  // Render <summary> tags as Mac-style Window blocks
  html = html.replace(/<summary>([\s\S]*?)<\/summary>/g, (_, content) => {
    return `
      <div class="my-6 rounded-2xl overflow-hidden border border-gray-200/60 shadow-[0_8px_24px_-4px_rgba(0,0,0,0.12)] bg-white/60 backdrop-blur-xl group">
        <div class="flex items-center justify-between px-3 py-2 bg-gray-100/80 backdrop-blur-lg border-b border-gray-200/40">
          <div class="flex items-center gap-1.5">
            <!-- macOS Traffic Lights -->
            <div class="flex gap-1.5 mr-2">
              <div class="w-2.5 h-2.5 rounded-full bg-[#ff5f56] shadow-md"></div>
              <div class="w-2.5 h-2.5 rounded-full bg-[#ffbd2e] shadow-md"></div>
              <div class="w-2.5 h-2.5 rounded-full bg-[#27c93f] shadow-md"></div>
            </div>

          </div>
          <span class="text-[11px] font-mono text-gray-700/70 uppercase font-bold tracking-wider">SUMMARY</span>
        </div>
        <div class="p-5 text-[15px] leading-relaxed text-base-content font-medium bg-white/60 backdrop-blur-md">
          ${content}
        </div>
      </div>
    `
  })
  
  // Wrap tables in Mac-style window container with modern grid layout
  html = html.replace(/<table[^>]*>([\s\S]*?)<\/table>/g, (_, content) => {
    // Parse table content to simple structure
    const rows = content.match(/<tr[^>]*>[\s\S]*?<\/tr>/g) || []
    
    // Extract headers
    const headerRow = rows[0] || ''
    const headers = (headerRow.match(/<th[^>]*>([\s\S]*?)<\/th>/g) || []).map((h: string) => {
      const alignMatch = h.match(/align="([^"]*)"/)
      const align = alignMatch ? alignMatch[1] : 'left'
      const text = h.replace(/<[^>]+>/g, '')
      return { text, align }
    })
    
    // Extract body rows
    const bodyRows = rows.slice(1).map((row: string) => {
      return (row.match(/<td[^>]*>([\s\S]*?)<\/td>/g) || []).map((c: string, i: number) => {
        const alignMatch = c.match(/align="([^"]*)"/)
        // Inherit alignment from header if not specified on cell
        const align = alignMatch ? alignMatch[1] : (headers[i]?.align || 'left') 
        // Keep inner HTML of the cell
        const innerHtml = c.replace(/^<td[^>]*>|<\/td>$/g, '')
        return { html: innerHtml, align }
      })
    })

    // Compact Table Style
    // If bare, remove border and radius to fit seamless into parent
    const containerClass = props.bare 
      ? "w-full bg-white text-[12px] select-text"
      : "w-full overflow-hidden rounded-lg border border-gray-200 bg-white text-[12px] select-text";
      
    let gridHtml = `<div class="${containerClass}">`
    
    // Combine headers and body rows for unified iteration
    const allRows: any[] = [headers.map((h: any) => ({ html: h.text, align: h.align })), ...bodyRows];

    allRows.forEach((row: any[], rowIndex: number) => {
      const isHeader = rowIndex === 0;
      const rowBg = isHeader 
        ? 'bg-gray-50 text-gray-800 font-semibold' 
        : (rowIndex % 2 === 0 ? 'bg-white' : 'bg-gray-50/50');
      const borderB = rowIndex < allRows.length - 1 ? 'border-b border-gray-100' : '';
        
      gridHtml += `<div class="flex w-full ${rowBg} ${borderB}">`;
      
      row.forEach((cell: any, colIndex: number) => {
        const justify = cell.align === 'center' ? 'justify-center text-center' : (cell.align === 'right' ? 'justify-end text-right' : 'justify-start');
        const borderClass = colIndex === row.length - 1 ? '' : 'border-r border-gray-100';
        
        gridHtml += `<div class="flex-1 py-1.5 px-2 min-w-0 break-words flex items-center leading-tight ${justify} ${borderClass}">
          <span>${cell.html}</span>
        </div>`;
      });
      gridHtml += `</div>`;
    });
    gridHtml += `</div>`;

    // Bare mode: just the grid, no window decoration
    if (props.bare) {
      return `<div class="overflow-x-auto">${gridHtml}</div>`
    }

    return `
      <div class="my-4 rounded-2xl overflow-hidden border border-gray-200/60 shadow-[0_8px_24px_-4px_rgba(0,0,0,0.12)] bg-white/60 backdrop-blur-xl group">
        <div class="flex items-center justify-between px-3 py-2 bg-gray-100/80 backdrop-blur-lg border-b border-gray-200/40">
          <div class="flex items-center gap-1.5">
            <!-- macOS Traffic Lights -->
            <div class="flex gap-1.5 mr-2">
              <div class="w-2.5 h-2.5 rounded-full bg-[#ff5f56] shadow-md"></div>
              <div class="w-2.5 h-2.5 rounded-full bg-[#ffbd2e] shadow-md"></div>
              <div class="w-2.5 h-2.5 rounded-full bg-[#27c93f] shadow-md"></div>
            </div>

          </div>
          <span class="text-[11px] font-mono text-gray-700/70 uppercase font-bold tracking-wider">DATA GRID</span>
        </div>
        <div class="overflow-x-auto bg-white/60 backdrop-blur-md p-0">
          ${gridHtml}
        </div>
      </div>
    `
  })
  
  // Convert [N] citations to colored badges
  const numSearch = props.numSearchRefs || 0
  const numPage = props.numPageRefs || 0
  
  html = html.replace(/\[(\d+)\]/g, (_, n) => {
    const num = parseInt(n)
    let colorClass = ''
    if (num >= 1 && num <= numSearch) {
      // Search: Flat Blue
      colorClass = 'bg-blue-500/10 text-blue-600'
    } else if (num > numSearch && num <= numSearch + numPage) {
      // Page: Flat Orange
      colorClass = 'bg-orange-500/10 text-orange-600'
    } else {
      // Default: Flat Gray
      colorClass = 'bg-base-content/10 text-base-content/60'
    }
    
    // Updated style: Superscript, flat color, no gradient, smaller
    return `<sup class="inline-flex items-center justify-center min-w-[14px] h-[14px] text-[9px] font-bold ${colorClass} rounded-[4px] mx-0.5 cursor-default select-none hover:bg-opacity-20 transition-all" style="vertical-align: super;">${num}</sup>`
  })
  
  return html
})
</script>

<template>
  <div ref="contentRef"
       class="prose prose-slate max-w-none 
              prose-headings:text-base-content prose-headings:font-bold prose-headings:mb-2 prose-headings:mt-4
              prose-p:text-base-content prose-p:leading-relaxed prose-p:my-2
              prose-a:text-blue-600 prose-a:no-underline hover:prose-a:underline
              prose-code:bg-base-200 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:text-[0.85em] prose-code:font-mono
              prose-pre:bg-base-200 prose-pre:border prose-pre:border-base-300 prose-pre:rounded-lg prose-pre:p-0
              prose-img:rounded-xl prose-img:shadow-[0_8px_24px_-4px_rgba(0,0,0,0.12)] prose-img:my-4 prose-img:max-h-[400px] prose-img:w-auto prose-img:object-contain prose-img:border prose-img:border-gray-200/60
              prose-ul:list-disc prose-ul:pl-4 prose-ul:list-outside
              prose-ol:list-decimal prose-ol:pl-4 prose-ol:list-outside
              prose-li:my-0.5 prose-li:pl-1
              [&>*:first-child]:!mt-0"
       v-html="processedHtml">
  </div>
</template>

<style>
/* Highlight.js theme - minimal */
.hljs {
  background: transparent !important;
  padding: 0 !important;
}
</style>
