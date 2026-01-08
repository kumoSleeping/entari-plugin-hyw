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
import shell from 'highlight.js/lib/languages/shell'
import yaml from 'highlight.js/lib/languages/yaml'
import properties from 'highlight.js/lib/languages/properties'

hljs.registerLanguage('python', python)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('js', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('ts', typescript)
hljs.registerLanguage('json', json)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('sh', bash)
hljs.registerLanguage('shell', shell)
hljs.registerLanguage('zsh', bash)
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
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('yml', yaml)
hljs.registerLanguage('properties', properties)
hljs.registerLanguage('ini', properties)
hljs.registerLanguage('conf', properties)

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

// Custom renderer for code blocks with technical layout
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
    return `<pre class="!mt-0 !mb-0 !rounded-none !bg-gray-50 !p-4 overflow-x-auto border-b border-gray-100"><code class="hljs language-${language} text-[13px] leading-relaxed font-mono">${highlighted}</code></pre>`
  }

  // Dynamic Icon mapping
  const getLangIcon = (l: string) => {
    const map: Record<string, { icon: string, color: string }> = {
      'python': { icon: 'mdi:language-python', color: 'text-blue-500' },
      'javascript': { icon: 'mdi:language-javascript', color: 'text-yellow-500' },
      'js': { icon: 'mdi:language-javascript', color: 'text-yellow-500' },
      'typescript': { icon: 'mdi:language-typescript', color: 'text-blue-600' },
      'ts': { icon: 'mdi:language-typescript', color: 'text-blue-600' },
      'bash': { icon: 'mdi:terminal', color: 'text-green-500' },
      'sh': { icon: 'mdi:terminal', color: 'text-green-500' },
      'shell': { icon: 'mdi:terminal', color: 'text-green-500' },
      'json': { icon: 'mdi:code-json', color: 'text-yellow-600' },
      'html': { icon: 'mdi:language-html5', color: 'text-orange-500' },
      'css': { icon: 'mdi:language-css3', color: 'text-blue-500' },
      'yaml': { icon: 'mdi:file-cog', color: 'text-purple-500' },
      'sql': { icon: 'mdi:database', color: 'text-red-500' }
    }
    return map[l] || { icon: 'mdi:code-braces', color: 'text-red-500' }
  }
  const langInfo = getLangIcon(language)

  return `
    <div class="my-6 space-y-1 group">
      <div class="flex items-center justify-between px-3 py-1.5 bg-gray-50 ">
        <div class="flex items-center gap-2">
          <Icon icon="${langInfo.icon}" class="${langInfo.color} text-sm" />
          <span class="text-[10px] font-black text-gray-700 uppercase tracking-widest">${language}</span>
        </div>
        <div class="text-gray-500 text-[9px] font-mono tracking-tighter tabular-nums">
          Source Code
        </div>
      </div>
      <div class="">
        <pre class="!mt-0 !mb-0 !rounded-none !bg-white !p-4 overflow-x-auto"><code class="hljs language-${language} text-[13px] leading-relaxed font-mono">${highlighted}</code></pre>
      </div>
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
  
  // Render <summary> tags as technical highlight blocks
  html = html.replace(/<summary>([\s\S]*?)<\/summary>/g, (_, content) => {
    return `
      <div class="my-8 group">
        <div class="flex items-center justify-between px-3 py-1.5 bg-gray-50 ">
          <div class="flex items-center gap-2">
            <Icon icon="mdi:lightning-bolt" class="text-red-500 text-sm" />
            <span class="text-[10px] font-black text-gray-700 uppercase tracking-widest">Summary</span>
          </div>
          <div class="text-gray-500 text-[9px] font-mono tracking-tighter tabular-nums">
            Insight
          </div>
        </div>
        <div class="p-5 text-[15px] leading-relaxed text-gray-800 font-medium bg-white ">
          ${content}
        </div>
      </div>
    `
  })
  
  // Wrap tables in crisp technical borders
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
        const align = alignMatch ? alignMatch[1] : (headers[i]?.align || 'left') 
        const innerHtml = c.replace(/^<td[^>]*>|<\/td>$/g, '')
        return { html: innerHtml, align }
      })
    })

    const containerClass = "w-full bg-white text-[12px] select-text";
      
    let gridHtml = `<div class="${containerClass}">`
    
    const allRows: any[] = [headers.map((h: any) => ({ html: h.text, align: h.align })), ...bodyRows];

    allRows.forEach((row: any[], rowIndex: number) => {
      const isHeader = rowIndex === 0;
      const rowBg = isHeader 
        ? 'bg-white text-gray-900 font-black uppercase tracking-tight' 
        : (rowIndex % 2 === 0 ? 'bg-white' : 'bg-gray-50/30');
      const borderB = rowIndex < allRows.length - 1 ? 'border-b border-gray-200' : '';
        
      gridHtml += `<div class="flex w-full ${rowBg} ${borderB}">`;
      
      row.forEach((cell: any, colIndex: number) => {
        const justify = cell.align === 'center' ? 'justify-center text-center' : (cell.align === 'right' ? 'justify-end text-right' : 'justify-start');
        const borderClass = colIndex === row.length - 1 ? '' : 'border-r border-gray-100';
        
        gridHtml += `<div class="flex-1 py-2.5 px-3 min-w-0 break-words flex items-center leading-tight ${justify} ${borderClass}">
          <span>${cell.html}</span>
        </div>`;
      });
      gridHtml += `</div>`;
    });
    gridHtml += `</div>`;

    if (props.bare) {
      return `<div class="overflow-x-auto border-b border-gray-200">${gridHtml}</div>`
    }

    return `
      <div class="my-6 group">
        <div class="overflow-x-auto bg-white p-0 border-t border-gray-100">
          ${gridHtml}
        </div>
      </div>
    `
  })
  
  // Convert [N] citations to rectangular sharp badges
  html = html.replace(/\[(\d+)\]/g, (_, n) => {
    const num = parseInt(n)
    return `<span class="relative -top-1.5 text-[10px] font-bold text-blue-600 mx-0.5 cursor-default select-none transition-all">${num}</span>`
  })
  
  return html
})
</script>

<template>
  <div ref="contentRef"
       class="prose prose-slate max-w-none 
              prose-headings:text-gray-900 prose-headings:font-black prose-headings:mb-2 prose-headings:mt-6 prose-headings:tracking-tight
              prose-p:text-gray-800 prose-p:leading-relaxed prose-p:my-3
              prose-a:text-blue-600 prose-a:no-underline hover:prose-a:underline
              prose-code:bg-gray-100 prose-code:px-1 prose-code:py-0.5 prose-code:rounded-none prose-code:text-[0.9em] prose-code:font-mono prose-code:text-gray-900
              prose-pre:bg-gray-50 prose-pre:border prose-pre:border-gray-200 prose-pre:rounded-none prose-pre:p-0
              prose-img:rounded-none prose-img:my-6 prose-img:max-h-[400px] prose-img:w-auto prose-img:object-contain prose-img:border prose-img:border-gray-200 prose-img:
              prose-ol:list-decimal prose-ol:pl-4 prose-ol:list-outside
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

/* Custom List Styling - Premium technical bullet */
.prose ul {
  list-style: none !important;
  padding-left: 0.25rem !important;
  margin-top: 0.75rem !important;
  margin-bottom: 0.75rem !important;
}

.prose ul > li {
  position: relative !important;
  padding-left: 1.5rem !important;
  margin-top: 0.5rem !important;
  margin-bottom: 0.5rem !important;
  line-height: 1.6 !important;
}

.prose ul > li::before {
  content: "" !important;
  position: absolute !important;
  left: 0 !important;
  top: 0.6em !important;
  width: 6px !important;
  height: 6px !important;
  background-color: #ef4444 !important; /* Red-500 */
  border-radius: 0 !important;
}

/* Nested list styling */
.prose ul ul {
  margin-top: 0.25rem !important;
  margin-bottom: 0.25rem !important;
  padding-left: 1rem !important;
}

.prose ul ul > li {
  padding-left: 1.25rem !important;
  margin-top: 0.25rem !important;
  margin-bottom: 0.25rem !important;
}

.prose ul ul > li::before {
  width: 5px !important;
  height: 5px !important;
  background-color: #ef4444 !important; /* Red-500 - same as parent, slightly smaller */
  top: 0.65em !important;
}

/* Custom Blockquote Styling - Dual Red Lines */
.prose blockquote {
  border-left: none !important;
  padding-left: 1rem !important;
  margin-left: 0 !important;
  position: relative !important;
  font-style: italic !important;
  color: #1f2937 !important; /* gray-800 */
}

.prose blockquote::before {
  content: "" !important;
  position: absolute !important;
  left: 0 !important;
  top: 0 !important;
  bottom: 0 !important;
  width: 3px !important;
  background-color: #ef4444 !important; /* Red-500 - thick line */
}



/* Ensure images don't have artifacts */
.prose img {
  display: block;
  margin-left: auto;
  margin-right: auto;
}
.prose pre {
  border: none !important;
}
</style>
