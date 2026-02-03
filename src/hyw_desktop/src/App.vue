<script setup lang="ts">
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { Icon } from '@iconify/vue'
import { listen } from '@tauri-apps/api/event'
import { invoke } from '@tauri-apps/api/core'
import { getCurrentWindow, LogicalSize, LogicalPosition, currentMonitor } from '@tauri-apps/api/window'
import { isPermissionGranted, requestPermission, sendNotification } from '@tauri-apps/plugin-notification'
import { readText } from '@tauri-apps/plugin-clipboard-manager'

import type { RenderData, Reference } from './types'
import MarkdownContent from './components/MarkdownContent.vue'
import SettingsPanel from './components/SettingsPanel.vue'
import ThinkingBlock from './components/ThinkingBlock.vue'
import { useAgentStore } from './stores/agent'
import type { HistoryItem } from './components/RecentHistory.vue'

// Import icons for Flow area
import iconOpenai from './assets/icon/openai.svg'
import iconGemini from './assets/icon/gemini.svg'
import iconAnthropic from './assets/icon/anthropic.svg'
import iconDeepseek from './assets/icon/deepseek.png'
import iconQwen from './assets/icon/qwen.png'
import iconMistral from './assets/icon/mistral.png'
import iconGrok from './assets/icon/grok.png'
import iconHuggingface from './assets/icon/huggingface.png'
import iconCerebras from './assets/icon/cerebras.svg'
import iconMinimax from './assets/icon/minimax.png'
import iconPerplexity from './assets/icon/perplexity.svg'
import iconNvidia from './assets/icon/nvida.png'
import iconMicrosoft from './assets/icon/microsoft.svg'
import iconXiaomi from './assets/icon/xiaomi.png'
import iconOpenrouter from './assets/icon/openrouter.png'

const loading = ref(false)
const data = ref<RenderData | null>(null)
const showSettings = ref(false)
const history = ref<HistoryItem[]>([])
const windowFocused = ref(true)
const currentQuery = ref('')
const showContent = ref(false)
const agentStore = useAgentStore()

// Input handling
const inputQuery = ref('')
const inputRef = ref<HTMLTextAreaElement | null>(null)
const inputHeight = ref(24)
const LINE_HEIGHT = 24
const MAX_INPUT_HEIGHT = 192 // 8 lines

// Display overlay for previous query (not in textarea, just visual)
const showPreviousQueryOverlay = ref(false)

// Command system
const showCommands = ref(false)
const showHistoryPanel = ref(false)
const commands = [
  { name: 'history', icon: 'mdi:history', description: '查看历史会话' },
  { name: 'settings', icon: 'mdi:cog', description: '打开设置' },
]
const filteredCommands = computed(() => {
  const query = inputQuery.value.trim()
  if (!query.startsWith('/')) return []
  const search = query.slice(1).toLowerCase()
  return commands.filter(cmd => cmd.name.toLowerCase().includes(search))
})

// Persistent history storage
const HISTORY_STORAGE_KEY = 'hyw_history'
const MAX_HISTORY_ITEMS = 50

const loadHistory = (): HistoryItem[] => {
  try {
    const stored = localStorage.getItem(HISTORY_STORAGE_KEY)
    if (stored) {
      const items = JSON.parse(stored)
      return items.map((item: any) => ({
        ...item,
        timestamp: new Date(item.timestamp)
      }))
    }
  } catch (e) {
    console.error('Failed to load history:', e)
  }
  return []
}

const saveHistory = () => {
  try {
    localStorage.setItem(HISTORY_STORAGE_KEY, JSON.stringify(history.value.slice(0, MAX_HISTORY_ITEMS)))
  } catch (e) {
    console.error('Failed to save history:', e)
  }
}

const formatTime = (date: Date): string => {
  const now = new Date()
  const diff = now.getTime() - date.getTime()
  const minutes = Math.floor(diff / 60000)
  const hours = Math.floor(diff / 3600000)
  const days = Math.floor(diff / 86400000)

  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes}分钟前`
  if (hours < 24) return `${hours}小时前`
  if (days < 7) return `${days}天前`
  return date.toLocaleDateString('zh-CN')
}

const canSubmit = computed(() => !loading.value && inputQuery.value.trim().length > 0)

const adjustInputHeight = async () => {
  await nextTick()
  const textarea = inputRef.value
  if (!textarea) return

  textarea.style.height = `${LINE_HEIGHT}px`
  const scrollHeight = textarea.scrollHeight
  const newHeight = Math.min(Math.max(scrollHeight, LINE_HEIGHT), MAX_INPUT_HEIGHT)
  inputHeight.value = newHeight
  textarea.style.height = `${newHeight}px`

  updateWindowSize()
}

const onInputKeydown = (e: KeyboardEvent) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    submitQuery()
  }
  // Show commands when typing /
  if (inputQuery.value === '' && e.key === '/') {
    showCommands.value = true
  }
}

const executeCommand = (cmd: string) => {
  showCommands.value = false
  inputQuery.value = ''

  switch (cmd) {
    case 'history':
      showHistoryPanel.value = true
      updateWindowSize()
      break
    case 'settings':
      showSettings.value = true
      break
    case 'clear':
      data.value = null
      showContent.value = false
      updateWindowSize()
      break
  }
}

const submitQuery = () => {
  if (!canSubmit.value) return

  const query = inputQuery.value.trim()

  // Check if it's a command
  if (query.startsWith('/')) {
    const cmdName = query.slice(1).toLowerCase()
    const cmd = commands.find(c => c.name.toLowerCase() === cmdName)
    if (cmd) {
      executeCommand(cmd.name)
      inputQuery.value = ''
      inputHeight.value = LINE_HEIGHT
      return
    }
  }

  // Normal search
  showCommands.value = false
  showHistoryPanel.value = false
  handleSearch(query)
  inputQuery.value = ''
  inputHeight.value = LINE_HEIGHT
}

const selectHistoryItem = (item: HistoryItem) => {
  showHistoryPanel.value = false
  handleSearch(item.query)
}

watch(inputQuery, () => {
  adjustInputHeight()
  // Hide overlay when user starts typing
  showPreviousQueryOverlay.value = false
  // If user types anything, clear previous results to retreat to small box
  if (!loading.value && (data.value || showContent.value)) {
    data.value = null
    showContent.value = false
    showHistoryPanel.value = false
  }
  // Show/hide commands based on input
  if (inputQuery.value.startsWith('/')) {
    showCommands.value = true
  } else {
    showCommands.value = false
  }
})

// Task timing constants
const TASK_TIMEOUT = 60000 // 60秒超时
let taskStartTime: number | null = null

// Window dimensions
const WINDOW_WIDTH = 400
const maxContentHeight = ref(600)

// Generate unique ID
const generateId = () => Math.random().toString(36).substring(2, 9)

// Handle stop
const handleStop = () => {
  loading.value = false
  currentQuery.value = ''
}

// Handle search from InputBar
const handleSearch = async (query: string) => {
  loading.value = true
  data.value = null
  currentQuery.value = query
  showContent.value = true

  // Add to history
  const historyItem: HistoryItem = {
    id: generateId(),
    query,
    status: 'pending',
    timestamp: new Date()
  }
  history.value.unshift(historyItem)

  // Keep only last 10 items
  if (history.value.length > 10) {
    history.value = history.value.slice(0, 10)
  }

  // Save to localStorage
  saveHistory()

  // Use Agent Store
  agentStore.sendQuery(query)
}

watch(() => agentStore.status, (status) => {
    if (status === 'finished') {
        const event = agentStore.events.find(e => e.type === 'agent_finish')
        if (event && event.data) {
             const webResults = event.data.web_results || []
             const references = webResults.filter((r: any) => !r._hidden).map((r: any) => ({
                title: r.title,
                url: r.url,
                snippet: r.content,
                images: r.images,
                is_fetched: r._type === 'page',
                raw_screenshot_b64: r.screenshot_b64
             }))
             
             data.value = {
                 markdown: event.data.response,
                 total_time: event.data.stats?.total_time || 0,
                 stages_used: event.data.stages || [],
                 references: references,
                 page_references: [],
                 image_references: [],
                 stages: event.data.stages || [],
                 stats: event.data.stats,
                 theme_color: '#ef4444'
             } as RenderData
             
             loading.value = false
             
             if (history.value.length > 0 && history.value[0].query === currentQuery.value) {
                 history.value[0].status = 'complete'
                 history.value[0].preview = event.data.response.substring(0, 100)
             }
             
             if (!windowFocused.value) {
                 sendCompletionNotification(currentQuery.value)
             }
        }
    } else {
        loading.value = true
    }
})

// Send notification when query completes
const sendCompletionNotification = async (query: string) => {
  try {
    let permissionGranted = await isPermissionGranted()
    if (!permissionGranted) {
      const permission = await requestPermission()
      permissionGranted = permission === 'granted'
    }
    if (permissionGranted) {
      sendNotification({
        title: 'Query Complete',
        body: `"${query.substring(0, 50)}${query.length > 50 ? '...' : ''}" is ready`
      })
    }
  } catch (e) {
    console.error('Notification error:', e)
  }
}

// Position window to top-right of current monitor
const positionToTopRight = async () => {
  const appWindow = getCurrentWindow()
  const monitor = await currentMonitor()
  if (monitor) {
    const x = monitor.position.x + monitor.size.width - WINDOW_WIDTH - 20
    const y = monitor.position.y + 40
    await appWindow.setPosition(new LogicalPosition(x, y))
  }
}

// Check for pending query from backend (Cmd+G with selection)
// Returns true if there was a pending query to process
const checkPendingQuery = async (): Promise<boolean> => {
  try {
    const pendingQuery = await invoke<string | null>('get_pending_query')
    if (pendingQuery) {
      console.log('[Frontend] Got pending query:', pendingQuery)

      // If query is same as current result, don't re-run
      if (pendingQuery === currentQuery.value && data.value) {
        console.log('[Frontend] Query matches current result, skipping search')
        inputQuery.value = pendingQuery
        await nextTick()
        adjustInputHeight()
        showContent.value = true
        // Focus input
        setTimeout(() => inputRef.value?.focus(), 50)
        return true
      }

      // IMMEDIATELY set loading state to prevent flashing old results
      loading.value = true
      data.value = null
      showContent.value = false

      // Set the query in input
      inputQuery.value = pendingQuery

      // Update UI
      await nextTick()
      adjustInputHeight()

      // Focus input before search
      setTimeout(() => inputRef.value?.focus(), 50)

      // Execute search
      await handleSearch(pendingQuery)
      return true
    }
  } catch (e) {
    console.error('Failed to get pending query:', e)
  }
  return false
}

// Prepare for new query (called from Rust before showing window)
const prepareForNewQuery = (newQuery?: string) => {
  // If new query matches current result, preserve state
  if (newQuery && newQuery === currentQuery.value && data.value) {
    return
  }

  loading.value = true
  data.value = null
  showContent.value = false
  showHistoryPanel.value = false
  showCommands.value = false
}

// Track window focus
onMounted(async () => {
  agentStore.connect('ws://127.0.0.1:5140/hyw/ws')
  
  // @ts-ignore
  window.__checkPendingQuery = checkPendingQuery
  // @ts-ignore
  window.__prepareForNewQuery = prepareForNewQuery

  const appWindow = getCurrentWindow()

  // Load history from localStorage
  history.value = loadHistory()

  // Position window to top-right on initial load
  await positionToTopRight()

  // Listen for query completion event
  await listen('query-complete', () => {
    if (!windowFocused.value) {
      // Notification is handled in handleSearch
    }
  })

  // Listen for settings open from tray menu
  await listen('open-settings', () => {
    showSettings.value = true
  })

  // Listen for focus-input event (Cmd+G shortcut)
  await listen('focus-input', () => {
    inputRef.value?.focus()
  })

  // Track window focus state
  await appWindow.listen('tauri://focus', async () => {
    windowFocused.value = true

    // IMMEDIATELY hide old content BEFORE any async operation
    // This prevents flashing old results while we check for pending query
    const previousData = data.value
    const previousShowContent = showContent.value
    const previousCurrentQuery = currentQuery.value

    // Hide everything first
    data.value = null
    showContent.value = false

    // Check for pending query from shortcut
    const hadPendingQuery = await checkPendingQuery()

    // If no pending query, restore previous state and show overlay
    if (!hadPendingQuery && previousData && previousCurrentQuery && !loading.value) {
      // Restore previous results
      data.value = previousData
      showContent.value = previousShowContent
      currentQuery.value = previousCurrentQuery

      // Don't put text in textarea, just show overlay
      inputQuery.value = ''
      showPreviousQueryOverlay.value = true
      await nextTick()
      adjustInputHeight()
      // Focus input - when user types, overlay disappears and they type fresh
      setTimeout(() => {
        inputRef.value?.focus()
      }, 50)
    } else if (!hadPendingQuery) {
      // Auto-focus input when window gains focus (with delay for window to fully appear)
      setTimeout(() => {
        inputRef.value?.focus()
      }, 50)
    }
  })

  // Hide window when it loses focus (click outside) - with smart task detection
  await appWindow.listen('tauri://blur', () => {
    windowFocused.value = false
    if (!showSettings.value) {
      // Check if task is in progress (within timeout)
      const isTaskActive = loading.value &&
        taskStartTime &&
        (Date.now() - taskStartTime < TASK_TIMEOUT)

      if (!isTaskActive) {
        // Only hide, preserve state so it is restored when reopening
        appWindow.hide()
      }
    }
  })
})

// Auto-resize window based on content
const updateWindowSize = async () => {
  await nextTick()
  const appWindow = getCurrentWindow()
  const card = document.getElementById('main-card')
  if (card) {
    let maxHeight = 800

    try {
      const monitor = await currentMonitor()
      if (monitor) {
        const scale = monitor.scaleFactor
        const screenHeight = monitor.size.height / scale
        // Cap at screen height - top margin (40) - bottom margin (120 for Dock/Taskbar)
        maxHeight = screenHeight - 160

        // Also update max content height for the scrollable area
        // Reserve space for input header (~60px) + margins
        maxContentHeight.value = maxHeight - 80
      }
    } catch (e) {
      console.error('Failed to get monitor info:', e)
    }

    // Wait for content to re-render with new maxContentHeight
    await nextTick()

    // Measure the actual card height after applying max constraints
    const actualHeight = card.offsetHeight
    const height = Math.min(Math.max(actualHeight, 48), maxHeight)
    await appWindow.setSize(new LogicalSize(WINDOW_WIDTH, height))
    await positionToTopRight()
  }
}

// Watch for content changes to resize window
watch([loading, data, showSettings], async () => {
  await updateWindowSize()
}, { flush: 'post' })

// Watch loading state for task timing
watch(loading, async (isLoading, wasLoading) => {
  if (isLoading && !wasLoading) {
    // Task started
    taskStartTime = Date.now()
  }

  if (!isLoading && wasLoading) {
    // Task completed
    taskStartTime = null
  }
})

// --- Ported Logic from Original App.vue ---

// Get icon for card type
const getCardIcon = (contentType?: string): string => {
  switch (contentType) {
    case 'summary': return 'mdi:text-box-outline'
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

const numSearchRefs = computed(() => data.value?.references?.length || 0)
const numPageRefs = computed(() => data.value?.page_references?.length || 0)

// Helper: Strips content before the first H1 heading (e.g., AI "thought" prefixes)
const stripPrefixBeforeH1 = (text: string): string => {
  const h1Match = text.match(/^#\s+/m)
  const summaryMatch = text.match(/<summary>/)

  let startIndex = -1

  if (h1Match && h1Match.index !== undefined) {
    startIndex = h1Match.index
  }

  if (summaryMatch && summaryMatch.index !== undefined) {
    if (startIndex === -1 || summaryMatch.index < startIndex) {
        startIndex = summaryMatch.index
    }
  }

  if (startIndex !== -1) {
    return text.substring(startIndex)
  }
  return text
}

const getJsContextDisplay = (url?: string): string => {
  if (!url) return 'JavaScript Execution'
  if (url.includes('Users') || url.includes('/home/') || url.startsWith('file://')) {
     return 'VM Context'
  }
  return getDomain(url)
}

const reorderedData = computed(() => {
  const originalMd = stripPrefixBeforeH1(data.value?.markdown || '')
  if (!originalMd) return { markdown: '', references: [] }

  const searchRefs = (data.value?.references || []).map((r, i) => ({...r, type: 'search', _orig: i + 1}))
  const pageRefs = (data.value?.page_references || []).map((r, i) => ({...r, type: 'page', _orig: (data.value?.references?.length || 0) + i + 1}))
  const allRefs = [...searchRefs, ...pageRefs]

  const citationRegex = /\[(\d+)\]/g
  const usageOrder: number[] = []
  let match
  while ((match = citationRegex.exec(originalMd)) !== null) {
    const id = parseInt(match[1]!)
    if (!usageOrder.includes(id)) usageOrder.push(id)
  }

  const idMap = new Map()
  const newReferences: any[] = []

  usageOrder.forEach((oldId, idx) => {
    const newId = idx + 1
    idMap.set(oldId, newId)
    const sourceRef = allRefs[oldId - 1]
    if (sourceRef) newReferences.push({ ...sourceRef, original_idx: newId })
  })

  const newMd = originalMd.replace(citationRegex, (m, n) => {
    const newId = idMap.get(parseInt(n))
    return newId ? `[${newId}]` : m
  })

  return { markdown: newMd, references: newReferences }
})

const referencesList = computed(() => reorderedData.value.references)

const mainTitle = computed(() => {
  const md = reorderedData.value.markdown || ''
  const match = md.match(/^#\s+(.+)$/m)
  return match && match[1] ? match[1].trim() : ''
})

const processedTitle = computed(() => {
  return mainTitle.value.replace(/<u>([^<]*)<\/u>/g, (_, content) => {
    return `<span class="underline decoration-[5px] underline-offset-8" style="text-decoration-color: var(--theme-color)">${content}</span>`
  })
})

function getDomain(url: string): string {
  try {
    const urlObj = new URL(url)
    const hostname = urlObj.hostname.replace('www.', '')
    let pathname = urlObj.pathname === '/' ? '' : decodeURIComponent(urlObj.pathname)
    const maxLen = 40
    let result = hostname + pathname
    if (result.length > maxLen) {
      result = result.slice(0, maxLen - 3) + '...'
    }
    return result
  } catch {
    return url.length > 40 ? url.slice(0, 37) + '...' : url
  }
}

function getFavicon(url: string): string {
  const domain = getDomain(url)
  return `https://www.google.com/s2/favicons?domain=${domain}&sz=32`
}

function getImageUrl(src: string): string {
  if (!src) return ''
  if (src.startsWith('data:')) return src
  if (src.startsWith('//') || src.startsWith('http:') || src.startsWith('https:')) {
    return src
  }
  const cleanBase64 = src.trim()
  if (cleanBase64.length > 0) {
    return `data:image/jpeg;base64,${cleanBase64}`
  }
  return src
}

function isValidImage(src: string): boolean {
  if (!src) return false
  if (src.startsWith('http') || src.startsWith('//')) return true
  if (src.length < 20) return false
  return true
}

const instructStages = computed(() => data.value?.stages?.filter(s =>
  s.name?.toLowerCase() === 'instruct' ||
  s.name?.toLowerCase().startsWith('analysis') ||
  s.provider?.toLowerCase() === 'instruct'
) || [])

const instructStage = computed(() => {
  const stages = instructStages.value
  if (!stages.length) return null
  const first = stages[0]
  const totalTime = stages.reduce((sum, s) => sum + (s.llm_time !== undefined ? s.llm_time : (s.time || 0)), 0)
  const totalInputTokens = stages.reduce((sum, s) => sum + (s.usage?.input_tokens || 0), 0)
  const totalOutputTokens = stages.reduce((sum, s) => sum + (s.usage?.output_tokens || 0), 0)
  const totalCost = stages.reduce((sum, s) => sum + (s.cost || 0), 0)

  return {
    ...first,
    time: totalTime,
    usage: { input_tokens: totalInputTokens, output_tokens: totalOutputTokens },
    cost: totalCost
  }
})

const toolsStage = computed(() => {
  const stages = instructStages.value
  if (!stages.length) return null
  const totalToolTime = stages.reduce((sum, s) => sum + (s.tool_time || 0), 0)
  const totalToolCalls = stages.reduce((sum, s) => sum + (s.tool_calls || 0), 0)
  if (totalToolTime <= 0.01 && totalToolCalls === 0) return null
  return {
    name: 'Tools',
    icon: 'mdi:toolbox-outline',
    time: totalToolTime,
    count: totalToolCalls
  }
})

const visionStage = computed(() => data.value?.stages?.find(s => s.name === 'Vision'))
const summaryStage = computed(() => data.value?.stages?.find(s => s.name?.toLowerCase() === 'summary' || s.name?.toLowerCase() === 'agent'))
const searchStage = computed(() => data.value?.stages?.find(s => s.name?.toLowerCase() === 'search'))
const browserJsStage = computed(() => data.value?.stages?.find(s =>
  s.name?.toLowerCase() === 'browser' ||
  s.name?.toLowerCase() === 'js_executor' ||
  s.name?.toLowerCase() === 'browser_js'
))

const truncateCode = (code: string, maxLines: number = 8, maxChars: number = 500): string => {
  if (!code) return ''
  let result = code.trim()
  if (result.length > maxChars) {
    result = result.substring(0, maxChars) + '\n... (truncated)'
  }
  const lines = result.split('\n')
  if (lines.length > maxLines) {
    result = lines.slice(0, maxLines).join('\n') + '\n... (' + (lines.length - maxLines) + ' more lines)'
  }
  return result
}

const galleryImages = computed(() => {
  const refs = (data.value?.references || []) as Reference[]
  const images: string[] = []
  const seenHashes = new Set<string>()

  for (const ref of refs) {
    if (ref.images && Array.isArray(ref.images)) {
      let count = 0
      for (const b64 of ref.images) {
        if (!isValidImage(b64)) continue
        const hash = `${b64.substring(0, 100)}_${b64.length}`
        if (!seenHashes.has(hash)) {
          seenHashes.add(hash)
          images.push(b64)
          count++
          if (count >= 2) break
        }
      }
    }
  }

  if (images.length < 8) {
    for (const ref of refs) {
      if (ref.images && Array.isArray(ref.images)) {
        for (const b64 of ref.images) {
          if (!isValidImage(b64)) continue
          const hash = `${b64.substring(0, 100)}_${b64.length}`
          if (!seenHashes.has(hash)) {
            seenHashes.add(hash)
            images.push(b64)
            if (images.length >= 12) break
          }
        }
      }
      if (images.length >= 12) break
    }
  }
  return images.slice(0, 12)
})

const dedent = (text: string) => {
  const lines = text.split('\n')
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

const getLuminance = (hex: string): number => {
  const match = hex.replace('#', '').match(/.{2}/g)
  if (!match) return 0
  const [r, g, b] = match.map(x => {
    const c = parseInt(x, 16) / 255
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  })
  return 0.2126 * (r ?? 0) + 0.7152 * (g ?? 0) + 0.0722 * (b ?? 0)
}

const headerTextColor = computed(() => {
  const luminance = getLuminance(themeColor.value)
  return luminance > 0.4 ? '#1f2937' : '#ffffff'
})

const getIconPath = (stage?: any): string => {
  if (!stage) return iconOpenai
  const model = (stage.model || '').toLowerCase()
  const provider = (stage.provider || '').toLowerCase()

  if (model.includes('gpt') || model.includes('o1') || provider.includes('openai')) return iconOpenai
  if (model.includes('gemini') || provider.includes('google')) return iconGemini
  if (model.includes('claude') || provider.includes('anthropic')) return iconAnthropic
  if (model.includes('deepseek') || provider.includes('deepseek')) return iconDeepseek
  if (model.includes('qwen') || provider.includes('qwen') || provider.includes('alibaba')) return iconQwen
  if (model.includes('mistral') || provider.includes('mistral')) return iconMistral
  if (model.includes('grok') || provider.includes('xai')) return iconGrok
  if (model.includes('huggingface')) return iconHuggingface
  if (model.includes('cerebras')) return iconCerebras
  if (model.includes('minimax')) return iconMinimax
  if (model.includes('perplexity')) return iconPerplexity
  if (model.includes('nvidia')) return iconNvidia
  if (model.includes('phi') || provider.includes('microsoft')) return iconMicrosoft
  if (model.includes('xiaomi') || model.includes('mimo')) return iconXiaomi

  return iconOpenrouter
}

const themeStyle = computed(() => ({
  '--theme-color': themeColor.value,
  '--header-text-color': headerTextColor.value,
  '--text-primary': '#2c2c2e',
  '--text-body': '#3a3a3c',
  '--text-muted': '#86868b',
  '--border-color': '#e5e7eb',
  '--bg-subtle': '#f9fafb'
}))

const parsedSections = computed(() => {
  const md = reorderedData.value.markdown || ''
  if (!md) return []

  let content = md.replace(/^#\s+.+$/m, '')
  content = content.replace(/(?:^|\n)\s*(?:#{1,3}|\*\*)\s*(?:References|Citations|Sources)[\s\S]*$/i, '')
  content = content.trim()

  const sections: Array<{ type: 'markdown' | 'card', content: string, title?: string, contentType?: 'table' | 'code' | 'summary', language?: string }> = []

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

    const isCode = matchedStr.startsWith('```')
    const isSummary = matchedStr.startsWith('<summary>')
    const isTable = !isCode && !isSummary && matchedStr.trim().startsWith('|')

    if (isCode || isTable || isSummary) {
        let language = ''
        let content = matchedStr.trim()

        if (isCode) {
            const match = matchedStr.match(/^```(\w+)/)
            if (match && match[1]) language = match[1]
        } else if (isSummary) {
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
        sections.push({ type: 'markdown', content: matchedStr })
    }
    remaining = remaining.substring(index + matchedStr.length)
  }
  return sections
})
</script>

<template>
  <div id="app-wrapper" class="w-full flex flex-col" :style="themeStyle">

    <!-- Unified Container - Clean Card Style -->
    <div
      id="main-card"
      class="relative bg-white/90 backdrop-blur-2xl shadow-xl border border-white/20"
      :class="loading ? 'ring-2 ring-gray-900/10' : ''"
      style="border-radius: 16px;"
    >

      <!-- Input Section -->
      <div class="flex items-center gap-2 px-4 py-3 relative">
        <!-- Previous query overlay (fake selected text) -->
        <div
          v-if="showPreviousQueryOverlay && currentQuery && !loading"
          class="absolute inset-0 px-4 py-3 flex items-start pointer-events-none"
          style="z-index: 1;"
        >
          <span
            class="text-[15px] leading-[24px] px-0.5 truncate max-w-[calc(100%-50px)]"
            style="background-color: #b4d7ff; color: #000;"
          >{{ currentQuery }}</span>
        </div>

        <textarea
          ref="inputRef"
          v-model="inputQuery"
          @keydown="onInputKeydown"
          @focus="showPreviousQueryOverlay = false"
          @input="showPreviousQueryOverlay = false"
          :disabled="loading"
          class="flex-1 text-[15px] bg-transparent outline-none resize-none"
          :class="{
            'text-gray-900 placeholder-gray-400': !loading,
            'text-gray-400 cursor-not-allowed': loading,
            'text-transparent': showPreviousQueryOverlay && currentQuery && !loading
          }"
          :style="{ height: `${inputHeight}px`, overflowY: inputHeight >= 192 ? 'auto' : 'hidden', lineHeight: '24px' }"
          placeholder="输入问题或 / 查看命令..."
          autofocus
        />

        <button
          v-if="!loading"
          @click="submitQuery"
          :disabled="!canSubmit"
          class="shrink-0 w-7 h-7 rounded-lg flex items-center justify-center transition-all duration-150 self-end"
          :class="canSubmit
            ? 'bg-gray-900 text-white hover:bg-gray-700 active:scale-95'
            : 'bg-gray-100 text-gray-300 cursor-not-allowed'"
        >
          <Icon icon="mdi:arrow-up" class="text-base" />
        </button>

        <button
          v-else
          @click="handleStop"
          class="shrink-0 w-7 h-7 rounded-lg bg-gray-900 text-white flex items-center justify-center hover:bg-gray-700 active:scale-95 transition-all duration-150 self-end"
        >
          <Icon icon="mdi:stop" class="text-base" />
        </button>
      </div>

      <!-- Loading indicator - Minimal -->
      <div
        v-if="loading && !data?.markdown"
        class="px-4 pb-3 flex items-center gap-2"
      >
        <div class="flex gap-0.5">
          <span class="w-1 h-1 bg-gray-400 rounded-full animate-pulse"></span>
          <span class="w-1 h-1 bg-gray-400 rounded-full animate-pulse" style="animation-delay: 150ms"></span>
          <span class="w-1 h-1 bg-gray-400 rounded-full animate-pulse" style="animation-delay: 300ms"></span>
        </div>
        <span class="text-xs text-gray-400 font-medium">思考中</span>
      </div>

      <!-- Command Menu -->
      <div
        v-if="showCommands && filteredCommands.length > 0"
        class="border-t border-gray-100"
      >
        <div
          v-for="cmd in filteredCommands"
          :key="cmd.name"
          @click="executeCommand(cmd.name)"
          class="flex items-center gap-3 px-4 py-2.5 hover:bg-gray-50 cursor-pointer transition-colors"
        >
          <Icon :icon="cmd.icon" class="text-base text-gray-400" />
          <div class="flex-1">
            <span class="text-sm text-gray-700">/{{ cmd.name }}</span>
            <span class="text-xs text-gray-400 ml-2">{{ cmd.description }}</span>
          </div>
        </div>
      </div>

      <!-- History Panel -->
      <div
        v-if="showHistoryPanel"
        class="border-t border-gray-100 max-h-[360px] overflow-y-auto"
      >
        <div class="px-4 py-2 sticky top-0 bg-white/90 backdrop-blur-sm">
          <span class="text-xs font-semibold text-gray-500 uppercase tracking-wide">历史</span>
        </div>
        <div v-if="history.length === 0" class="text-sm text-gray-400 py-6 text-center">
          暂无历史
        </div>
        <div v-else>
          <div
            v-for="item in history"
            :key="item.id"
            @click="selectHistoryItem(item)"
            class="flex items-center gap-3 px-4 py-2.5 hover:bg-gray-50 cursor-pointer transition-colors"
          >
            <div class="flex-1 min-w-0">
              <div class="text-sm text-gray-700 truncate">{{ item.query }}</div>
            </div>
            <span class="text-xs text-gray-400 shrink-0">{{ formatTime(item.timestamp) }}</span>
          </div>
        </div>
      </div>

      <!-- Thinking Stream -->
      <div v-if="agentStore.events.length > 0" class="px-4 py-2 space-y-2 mb-2 max-h-[400px] overflow-y-auto custom-scrollbar">
         <ThinkingBlock 
            v-for="(event, index) in agentStore.events" 
            :key="index" 
            :event="event" 
            :index="index"
            @toggle="agentStore.toggleCollapse"
         />
      </div>

      <!-- Divider -->
      <div v-if="data?.markdown" class="mx-4 border-t border-gray-200/50"></div>

      <!-- Result Content -->
      <div
        v-if="data?.markdown"
        class="overflow-y-auto"
        style="overscroll-behavior: contain;"
        :style="{ maxHeight: `${maxContentHeight}px` }"
      >
        <div class="px-4 py-4 space-y-4 bg-transparent" data-theme="light">

          <!-- Title -->
          <header v-if="mainTitle" class="mb-6">
            <h1 class="text-[32px] font-black leading-tight tracking-tighter uppercase tabular-nums" style="color: var(--text-primary)" v-html="processedTitle"></h1>
          </header>

          <!-- Content Sections -->
          <template v-for="(section, idx) in parsedSections" :key="idx">

            <!-- Standard Markdown -->
            <div v-if="section.type === 'markdown'">
              <MarkdownContent
                :markdown="section.content"
                :num-search-refs="numSearchRefs"
                :num-page-refs="numPageRefs"
                class="prose-h2:text-[22px] prose-h2:font-black prose-h2:uppercase prose-h2:tracking-tight prose-h2:mb-4 prose-h2:text-gray-800"
              />
            </div>

            <!-- Special Card (Table/Code/Summary) -->
            <div v-else-if="section.type === 'card'" class="relative">
              <!-- Corner Rectangle Badge with Icon and Label -->
              <div
                class="absolute -top-2 -left-2 h-7 px-2.5 z-10 flex items-center justify-center gap-1.5"
                :style="{ backgroundColor: themeColor, color: headerTextColor, boxShadow: '0 2px 4px 0 rgba(0,0,0,0.15)' }"
              >
                <Icon :icon="getCardIcon(section.contentType)" class="text-[14px]" />
                <span class="text-[12px] font-bold uppercase tracking-wide">{{ getCardLabel(section.contentType, section.language) }}</span>
              </div>
              <div
                :class="[
                  section.contentType === 'summary' ? 'pt-8 px-5 pb-4 text-base leading-relaxed break-words' : '',
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

          <!-- Sources Section (Bibliography) - Styled as Card -->
          <div v-if="referencesList.length" class="relative group/sources">
            <!-- Corner Rectangle Badge -->
            <div
              class="absolute -top-2 -left-2 h-7 px-2.5 z-10 flex items-center justify-center gap-1.5"
              :style="{ backgroundColor: themeColor, color: headerTextColor, boxShadow: '0 2px 4px 0 rgba(0,0,0,0.15)' }"
            >
              <Icon icon="mdi:book-open-page-variant-outline" class="text-[14px]" />
              <span class="text-[12px] font-bold uppercase tracking-wide">Sources</span>
            </div>

            <div class="pt-10 px-5 pb-6 space-y-6">
               <div v-for="(ref, index) in referencesList" :key="ref.url + '-' + index" class="group/item flex items-start gap-3 pl-0.5">
                  <!-- Number -->
                  <div class="shrink-0 w-5 h-5 text-[14px] font-bold flex items-center justify-center pt-0.5"
                       :style="{ color: themeColor }">
                    {{ ref.original_idx }}
                  </div>

                  <!-- Content -->
                  <div class="flex-1 min-w-0">
                     <!-- Title -->
                     <a :href="ref.url" target="_blank" class="block mb-0.5">
                       <div class="text-[16px] font-bold leading-tight group-hover/item:text-[var(--theme-color)] transition-colors" style="color: var(--text-primary)">
                         {{ ref.title }}
                       </div>
                     </a>

                     <!-- Domain & Favicon -->
                     <div class="flex items-center gap-2.5 text-[10px] font-mono mb-2" style="color: var(--text-muted)">
                        <img :src="getFavicon(ref.url)" class="w-3 h-3 object-contain rounded-sm">
                        <span>{{ getDomain(ref.url) }}</span>
                     </div>

                     <!-- Snippet / Screenshot (Condition: Must have snippet or raw screenshot) -->
                     <div v-if="ref.raw_screenshot_b64 || ref.snippet"
                          class="mt-1.5 pl-3 py-0.5"
                          :class="[(ref.is_fetched || ref.type === 'page') ? 'border-l-[3px]' : 'border-l-2 border-transparent']"
                          :style="(ref.is_fetched || ref.type === 'page') ? { borderColor: themeColor } : {}"
                     >
                        <!-- Real page screenshot if available -->
                        <div v-if="ref.raw_screenshot_b64" class="relative">
                          <img
                               :src="getImageUrl(ref.raw_screenshot_b64)"
                               class="max-w-full h-auto rounded-sm border border-gray-200 shadow-sm"
                               :class="ref.is_thumbnail ? 'aspect-square object-cover' : ''"
                               alt="Page preview"
                          />
                          <!-- Thumbnail hint -->
                          <div v-if="ref.is_thumbnail && ref.screenshot_cache_id"
                               class="mt-1 text-[10px] font-mono opacity-50"
                               style="color: var(--text-muted)"
                          >
                            /w {{ ref.original_idx }} 查看完整页面
                          </div>
                        </div>
                        <!-- Fallback to markdown snippet -->
                        <MarkdownContent v-else
                          :markdown="ref.snippet"
                          :bare="true"
                          :compact="true"
                        />
                     </div>
                  </div>
               </div>
            </div>
          </div>

          <!-- Gallery Section (Extracted Images) - Masonry Layout -->
          <div v-if="galleryImages.length" class="relative group/gallery mb-8">
              <!-- Corner Badge -->
              <div
                class="absolute -top-2 -left-2 h-7 px-2.5 z-10 flex items-center justify-center gap-1.5"
                :style="{ backgroundColor: themeColor, color: headerTextColor, boxShadow: '0 2px 4px 0 rgba(0,0,0,0.15)' }"
              >
                <Icon icon="mdi:image-multiple-outline" class="text-[14px]" />
                <span class="text-[12px] font-bold uppercase tracking-wide">Gallery</span>
              </div>

              <div class="pt-10 px-6 pb-6">
                  <!-- Masonry Layout: 2 Columns -->
                  <div class="columns-2 gap-4 space-y-4">
                      <div v-for="(img, idx) in galleryImages" :key="idx" class="break-inside-avoid relative rounded-sm overflow-hidden border border-gray-100 bg-gray-50">
                          <img
                              :src="getImageUrl(img)"
                              class="w-full h-auto block object-cover transform hover:scale-105 transition-transform duration-500"
                              loading="lazy"
                          />
                      </div>
                  </div>
              </div>
          </div>

          <!-- Flow: Unified Stage Info Area -->
          <div v-if="searchStage || instructStage || summaryStage || visionStage || toolsStage || browserJsStage" class="relative group/flow">
              <!-- Corner Badge -->
              <div
                class="absolute -top-2 -left-2 h-7 px-2.5 z-10 flex items-center justify-center gap-1.5"
                :style="{ backgroundColor: themeColor, color: headerTextColor, boxShadow: '0 2px 4px 0 rgba(0,0,0,0.15)' }"
              >
                <Icon icon="mdi:sitemap-outline" class="text-[14px]" />
                <span class="text-[12px] font-bold uppercase tracking-wide">Flow</span>
              </div>

              <!-- Flow Content (Timeline Style) -->
              <div class="pt-8 px-6 pb-8">
                <div class="space-y-8 relative">

                  <!-- Search Stage -->
                  <div v-if="searchStage" class="relative flex items-start gap-4 z-10 w-full">
                    <!-- Node: Search Icon -->
                    <div class="shrink-0 w-6 h-6 flex items-center justify-center bg-white" style="color: var(--theme-color)">
                       <Icon icon="mdi:magnify" class="w-5 h-5" />
                    </div>
                    <!-- Content -->
                    <div class="flex-1 min-w-0 pt-1">
                      <div class="text-[17px] font-bold uppercase tracking-tight mb-1.5 leading-none" style="color: var(--text-primary)">Search</div>
                      <div class="flex items-center justify-between gap-x-4 text-[13px] font-mono leading-tight w-full" style="color: var(--text-muted)">
                        <span class="truncate max-w-[180px]">{{ searchStage.description || 'Web Search' }}</span>

                        <div class="flex items-center gap-4 shrink-0">
                          <div v-if="searchStage.time" class="flex items-center gap-1.5 opacity-80">
                            <Icon icon="mdi:clock-outline" class="text-[13px]" />
                            <span>{{ searchStage.time.toFixed(2) }}s</span>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>

                  <!-- Vision Stage -->
                  <div v-if="visionStage" class="relative flex items-start gap-4 z-10 w-full">
                    <!-- Node: Brand Logo -->
                    <div class="shrink-0 w-6 h-6 flex items-center justify-center bg-white">
                       <img :src="getIconPath(visionStage)" class="w-5 h-5 object-contain" alt="" />
                    </div>
                    <!-- Content -->
                    <div class="flex-1 min-w-0 pt-1">
                      <div class="text-[17px] font-bold uppercase tracking-tight mb-1.5 leading-none" style="color: var(--text-primary)">Vision</div>
                      <div class="flex items-center justify-between gap-x-4 text-[13px] font-mono leading-tight w-full" style="color: var(--text-muted)">
                        <!-- Model Name (Truncated) -->
                        <span class="truncate max-w-[180px]" :title="visionStage.model">{{ visionStage.model }}</span>

                        <!-- Metrics -->
                        <div class="flex items-center gap-4 shrink-0">
                          <div class="flex items-center gap-1.5 opacity-80">
                            <Icon icon="mdi:clock-outline" class="text-[13px]" />
                            <span>{{ (visionStage.time || 0).toFixed(2) }}s</span>
                          </div>
                          <template v-if="visionStage.cost">
                            <div class="flex items-center gap-0.5 opacity-80">
                              <span>${{ visionStage.cost.toFixed(5) }}</span>
                            </div>
                          </template>
                        </div>
                      </div>
                    </div>
                  </div>

                  <!-- Instruct Stage -->
                  <div v-if="instructStage" class="relative flex items-start gap-4 z-10 w-full">
                    <!-- Node: Brand Logo -->
                    <div class="shrink-0 w-6 h-6 flex items-center justify-center bg-white">
                       <img :src="getIconPath(instructStage)" class="w-5 h-5 object-contain" alt="" />
                    </div>
                    <!-- Content -->
                    <div class="flex-1 min-w-0 pt-1">
                      <div class="text-[17px] font-bold uppercase tracking-tight mb-1.5 leading-none" style="color: var(--text-primary)">Instruct</div>
                      <div class="flex items-center justify-between gap-x-4 text-[13px] font-mono leading-tight w-full" style="color: var(--text-muted)">
                        <span class="truncate max-w-[180px]" :title="instructStage.model">{{ instructStage.model }}</span>

                        <div class="flex items-center gap-4 shrink-0">
                          <div class="flex items-center gap-1.5 opacity-80">
                            <Icon icon="mdi:clock-outline" class="text-[13px]" />
                            <span>{{ (instructStage.time || 0).toFixed(2) }}s</span>
                          </div>
                          <template v-if="instructStage.cost">
                            <div class="flex items-center gap-0.5 opacity-80">
                              <span>${{ instructStage.cost.toFixed(5) }}</span>
                            </div>
                          </template>
                        </div>
                      </div>
                    </div>
                  </div>

                  <!-- Tools Stage -->
                  <div v-if="toolsStage" class="relative flex items-start gap-4 z-10 w-full">
                    <!-- Node: Icon -->
                    <div class="shrink-0 w-6 h-6 flex items-center justify-center bg-white" style="color: var(--theme-color)">
                       <Icon :icon="toolsStage.icon" class="w-5 h-5" />
                    </div>
                    <!-- Content -->
                    <div class="flex-1 min-w-0 pt-1">
                      <div class="text-[17px] font-bold uppercase tracking-tight mb-1.5 leading-none" style="color: var(--text-primary)">Tools</div>
                      <div class="flex items-center justify-between gap-x-4 text-[13px] font-mono leading-tight w-full" style="color: var(--text-muted)">
                        <span class="truncate max-w-[180px]">System Execution</span>

                        <div class="flex items-center gap-4 shrink-0">
                          <div class="flex items-center gap-1.5 opacity-80">
                            <Icon icon="mdi:clock-outline" class="text-[13px]" />
                            <span>{{ (toolsStage.time || 0).toFixed(2) }}s</span>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>

                  <!-- Browser JS Driver Stage -->
                  <div v-if="browserJsStage" class="relative flex items-start gap-4 z-10 w-full">
                    <!-- Node: Icon -->
                    <div class="shrink-0 w-6 h-6 flex items-center justify-center bg-white" style="color: var(--theme-color)">
                       <Icon icon="mdi:language-javascript" class="w-5 h-5" />
                    </div>
                    <!-- Content -->
                    <div class="flex-1 min-w-0 pt-1">
                      <div class="text-[17px] font-bold uppercase tracking-tight mb-1.5 leading-none" style="color: var(--text-primary)">Browser JS Driver</div>
                      <div class="flex items-center justify-between gap-x-4 text-[13px] font-mono leading-tight w-full mb-2" style="color: var(--text-muted)">
                        <span class="truncate max-w-[180px]">{{ getJsContextDisplay(browserJsStage.url) }}</span>

                        <div class="flex items-center gap-4 shrink-0">
                          <div v-if="browserJsStage.time" class="flex items-center gap-1.5 opacity-80">
                            <Icon icon="mdi:clock-outline" class="text-[13px]" />
                            <span>{{ browserJsStage.time.toFixed(2) }}s</span>
                          </div>
                        </div>
                      </div>
                      <!-- Input Code Block -->
                      <div v-if="browserJsStage.script" class="mt-2">
                        <div class="text-[11px] font-bold uppercase tracking-wide mb-1" style="color: var(--text-muted)">Input</div>
                        <pre class="text-[11px] font-mono bg-gray-100 p-2 rounded overflow-x-auto max-h-24 overflow-y-auto" style="color: var(--text-body)"><code>{{ truncateCode(browserJsStage.script, 6, 300) }}</code></pre>
                      </div>
                      <!-- Output Code Block -->
                      <div v-if="browserJsStage.output" class="mt-2">
                        <div class="text-[11px] font-bold uppercase tracking-wide mb-1" style="color: var(--text-muted)">Output</div>
                        <pre class="text-[11px] font-mono bg-gray-100 p-2 rounded overflow-x-auto max-h-24 overflow-y-auto" style="color: var(--text-body)"><code>{{ truncateCode(browserJsStage.output, 6, 300) }}</code></pre>
                      </div>
                    </div>
                  </div>

                  <!-- Summary Stage -->
                  <div v-if="summaryStage" class="relative flex items-start gap-4 z-10 w-full">
                    <!-- Node: Brand Logo -->
                    <div class="shrink-0 w-6 h-6 flex items-center justify-center bg-white">
                       <img :src="getIconPath(summaryStage)" class="w-5 h-5 object-contain" alt="" />
                    </div>
                    <!-- Content -->
                    <div class="flex-1 min-w-0 pt-1">
                      <div class="text-[17px] font-bold uppercase tracking-tight mb-1.5 leading-none" style="color: var(--text-primary)">Summary</div>
                      <div class="flex items-center justify-between gap-x-4 text-[13px] font-mono leading-tight w-full" style="color: var(--text-muted)">
                        <span class="truncate max-w-[180px]" :title="summaryStage.model">{{ summaryStage.model }}</span>

                        <div class="flex items-center gap-4 shrink-0">
                          <div class="flex items-center gap-1.5 opacity-80">
                            <Icon icon="mdi:clock-outline" class="text-[13px]" />
                            <span>{{ summaryStage.time?.toFixed(2) }}s</span>
                          </div>
                          <template v-if="summaryStage.cost">
                            <div class="flex items-center gap-0.5 opacity-80">
                              <span>${{ summaryStage.cost.toFixed(5) }}</span>
                            </div>
                          </template>
                        </div>
                      </div>
                    </div>
                  </div>

                </div>
              </div>
          </div>

        </div>
      </div>
    </div>

    <!-- Settings Panel Modal -->
    <SettingsPanel v-if="showSettings" @close="showSettings = false" />

  </div>
</template>

<style>
/* Fade in animation */
.fade-in {
  animation: fadeIn 0.5s ease-out forwards;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}

/* Scrollbar styling */
::-webkit-scrollbar {
  width: 8px;
  height: 8px;
}
::-webkit-scrollbar-track {
  background: transparent;
}
::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 4px;
}
::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}
</style>
